"""Unit tests for the Akahu provider.

Akahu is faked with ``httpx.MockTransport``: no tokens, no network. Payloads
are fictional and only as large as each test needs.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.agents.services.crypto import encrypt
from app.providers.akahu import (
    AkahuProvider,
    category_hint_for,
    clean_description,
    parse_tokens,
)
from app.providers.base import (
    ProviderRateLimited,
    ProviderUserActionRequired,
    SessionExpiredError,
)

USER = "user_token_abc123"
APP = "app_token_xyz789"


def _creds() -> dict:
    return {"user_token_enc": encrypt(USER), "app_token_enc": encrypt(APP)}


def _patched(handler):
    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, timeout=30)

    return patch.object(AkahuProvider, "_client", fake_client)


def _account(**over) -> dict:
    acc = {
        "_id": "acc_1",
        "name": "Everyday",
        "type": "CHECKING",
        "status": "ACTIVE",
        "formatted_account": "12-3456-0123456-00",
        "attributes": ["TRANSACTIONS", "TRANSFER_FROM"],
        "balance": {"currency": "NZD", "current": 1234.56, "available": 1200.0},
        "connection": {"_id": "conn_bank", "name": "Example Bank", "logo": "https://logo.example/bank.png"},
        "refreshed": {"transactions": "2026-10-12T00:00:00Z"},
    }
    acc.update(over)
    return acc


def _txn(**over) -> dict:
    txn = {
        "_id": "trans_1",
        "_account": "acc_1",
        "date": "2026-10-10T11:30:00.000Z",
        "description": "COUNTDOWN EXAMPLETOWN",
        "amount": -42.5,
        "type": "EFTPOS",
        "merchant": {"_id": "merchant_1", "name": "Countdown"},
        "category": {
            "_id": "nzfcc_1",
            "name": "Supermarkets and grocery stores",
            "groups": {"personal_finance": {"_id": "group_1", "name": "Food"}},
        },
    }
    txn.update(over)
    return txn


# ----- pure helpers -----------------------------------------------------------


def test_parse_tokens_both_in_any_order():
    assert parse_tokens(f"{APP} {USER}") == (USER, APP)
    assert parse_tokens(f"  {USER}\n{APP} ") == (USER, APP)


@pytest.mark.parametrize("raw", [USER, APP, "", "nonsense"])
def test_parse_tokens_requires_both(raw):
    with pytest.raises(ValueError):
        parse_tokens(raw)


def test_clean_description_drops_numbers_and_shouting():
    raw = "TRANSFER TO 12-3456-0123456-00 CARD 4835-****-****-1234 EXAMPLE CAFE"
    cleaned = clean_description(raw)
    assert "0123456" not in cleaned
    assert "1234" not in cleaned
    assert cleaned == "Transfer To Card Example Cafe"


def test_clean_description_keeps_mixed_case():
    assert clean_description("Example Cafe Ltd") == "Example Cafe Ltd"


def test_category_hint_specific_before_group():
    assert category_hint_for(_txn(category={
        "name": "Cafes and restaurants",
        "groups": {"personal_finance": {"name": "Food"}},
    })) == "food"


def test_category_hint_flights_and_hotels_are_travel():
    for name in ("Air transport services", "Hotels, motels, and other temporary accommodation"):
        assert category_hint_for(_txn(category={
            "name": name, "groups": {"personal_finance": {"name": "Lifestyle"}},
        })) == "travel"


def test_category_hint_group_fallback():
    assert category_hint_for(_txn()) == "groceries"


def test_category_hint_transfer_type_wins():
    assert category_hint_for(_txn(type="TRANSFER")) == "transfers"


def test_category_hint_unknown_is_none():
    assert category_hint_for(_txn(category=None)) is None


# ----- requests -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_carry_both_tokens():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["app"] = request.headers.get("x-akahu-id")
        seen["host"] = request.url.host
        return httpx.Response(200, json={"success": True, "items": [_account()]})

    with _patched(handler):
        await AkahuProvider().get_accounts(_creds())
    assert seen == {"auth": f"Bearer {USER}", "app": APP, "host": "api.akahu.io"}


@pytest.mark.asyncio
async def test_missing_app_token_is_session_expired():
    with pytest.raises(SessionExpiredError):
        await AkahuProvider().get_accounts({"user_token_enc": encrypt(USER)})


@pytest.mark.asyncio
async def test_missing_user_token_is_session_expired():
    with pytest.raises(SessionExpiredError):
        await AkahuProvider().get_accounts({})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors_need_user_action(status):
    with _patched(lambda r: httpx.Response(status, json={"success": False})):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await AkahuProvider().get_accounts(_creds())
    assert exc.value.code == "credentials_invalid"


@pytest.mark.asyncio
async def test_rate_limit_is_transient():
    with _patched(lambda r: httpx.Response(429)):
        with pytest.raises(ProviderRateLimited):
            await AkahuProvider().get_accounts(_creds())


@pytest.mark.asyncio
async def test_error_message_does_not_echo_the_body():
    with _patched(lambda r: httpx.Response(500, text="secret-ish upstream text")):
        with pytest.raises(RuntimeError) as exc:
            await AkahuProvider().get_accounts(_creds())
    assert "secret-ish" not in str(exc.value)


# ----- connection -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_stores_tokens_encrypted():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"success": True, "item": {"_id": "user_1"}})
        return httpx.Response(200, json={"success": True, "items": [_account()]})

    with _patched(handler):
        conn = await AkahuProvider().handle_oauth_callback(f"{USER} {APP}")
    assert conn.external_id == "akahu-user_1"
    assert conn.institution_name == "Example Bank"
    stored = json.dumps(conn.credentials)
    assert USER not in stored and APP not in stored
    assert [a.external_id for a in conn.accounts] == ["acc_1"]


# ----- accounts -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accounts_map_types_and_skip_investments():
    accounts = [
        _account(),
        _account(_id="acc_cc", name="Card", type="CREDITCARD",
                 balance={"currency": "NZD", "current": -250.0, "limit": 5000}),
        _account(_id="acc_ks", name="KiwiSaver", type="KIWISAVER", attributes=[]),
        _account(_id="acc_sav", name="Saver", type="SAVINGS"),
    ]
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": accounts})):
        result = await AkahuProvider().get_accounts(_creds())
    by_id = {a.external_id: a for a in result}
    assert set(by_id) == {"acc_1", "acc_cc", "acc_sav"}
    assert by_id["acc_1"].type == "checking"
    assert by_id["acc_1"].balance == Decimal("1234.56")
    assert by_id["acc_1"].masked_number == "5600"
    assert by_id["acc_1"].institution_name == "Example Bank"
    assert by_id["acc_cc"].type == "credit_card"
    assert by_id["acc_cc"].balance == Decimal("250.0")  # positive debt
    assert by_id["acc_cc"].credit_limit == Decimal("5000")
    assert by_id["acc_sav"].type == "savings"


# ----- transactions ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_transactions_paginate_and_map():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": [_account()]})
        calls.append(dict(request.url.params))
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={
                "success": True,
                "items": [_txn()],
                "cursor": {"next": "page2"},
            })
        return httpx.Response(200, json={
            "success": True,
            "items": [_txn(_id="trans_2", amount=1000, type="DIRECT CREDIT",
                           description="SALARY EXAMPLE LTD", merchant=None,
                           category={"name": "Salary or wages"})],
            "cursor": {"next": None},
        })

    with _patched(handler):
        txns = await AkahuProvider().get_transactions(_creds(), "acc_1", since=None)
    assert len(calls) == 2
    assert calls[1]["cursor"] == "page2"
    assert calls[0]["start"] == calls[1]["start"]  # same window on every page
    first, second = txns
    assert first.external_id == "trans_1"
    assert first.type == "debit" and first.amount == Decimal("42.5")
    assert first.date == date(2026, 10, 11)  # 11:30 UTC is the next day in NZ
    assert first.payee == "Countdown"
    assert first.status == "posted"
    assert first.currency == "NZD"
    assert first.category_hint == "groceries"
    assert first.raw_data is not None
    assert first.raw_data["description"] == "COUNTDOWN EXAMPLETOWN"
    assert second.type == "credit" and second.payee is None
    assert second.category_hint == "salary"


@pytest.mark.asyncio
async def test_transactions_respect_payee_source_none():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": [_account()]})
        return httpx.Response(200, json={"success": True, "items": [_txn()], "cursor": {}})

    with _patched(handler):
        txns = await AkahuProvider().get_transactions(_creds(), "acc_1", payee_source="none")
    assert txns[0].payee is None


@pytest.mark.asyncio
async def test_follow_up_sync_rereads_the_lookback_window():
    windows = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": [_account()]})
        windows.append(request.url.params["start"])
        return httpx.Response(200, json={"success": True, "items": [], "cursor": {}})

    today = datetime.now(timezone.utc).date()
    with _patched(handler):
        await AkahuProvider().get_transactions(_creds(), "acc_1", since=today - timedelta(days=2))
    start = datetime.fromisoformat(windows[0].replace("Z", "+00:00")).date()
    assert (today - start).days >= 30


@pytest.mark.asyncio
async def test_balance_only_and_inactive_accounts_fetch_nothing():
    accounts = [
        _account(_id="acc_td", type="TERMDEPOSIT", attributes=[]),
        _account(_id="acc_old", status="INACTIVE"),
    ]
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"success": True, "items": accounts})

    provider = AkahuProvider()
    with _patched(handler):
        assert await provider.get_transactions(_creds(), "acc_td") == []
        assert await provider.get_transactions(_creds(), "acc_old") == []
    assert all(p.endswith("/accounts") for p in paths)


@pytest.mark.asyncio
async def test_malformed_rows_are_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": [_account()]})
        return httpx.Response(200, json={"success": True, "items": [
            _txn(_id=None), _txn(_id="t2", amount="nope"), _txn(_id="t3", date=None), _txn(_id="ok"),
        ], "cursor": {}})

    with _patched(handler):
        txns = await AkahuProvider().get_transactions(_creds(), "acc_1")
    assert [t.external_id for t in txns] == ["ok"]


# ----- holdings ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investment_accounts_become_one_holding_each():
    accounts = [
        _account(),
        _account(_id="acc_ks", name="KiwiSaver Growth", type="KIWISAVER", attributes=[],
                 balance={"currency": "NZD", "current": 54321.0},
                 meta={"portfolio": [{"name": "Growth Fund", "value": 54321.0}]}),
    ]
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": accounts})):
        holdings = await AkahuProvider().get_holdings(_creds())
    assert len(holdings) == 1
    h = holdings[0]
    assert h.external_id == "acc_ks"
    assert h.current_value == Decimal("54321.0")
    assert h.account_external_id == "acc_ks"
    assert h.metadata is not None
    assert h.metadata["portfolio"][0]["name"] == "Growth Fund"


# ----- refresh ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_skipped_when_data_is_fresh():
    fresh = datetime.now(timezone.utc).isoformat()
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(request.url.path)
        return httpx.Response(200, json={"success": True, "items": [
            _account(refreshed={"transactions": fresh}),
        ]})

    with _patched(handler):
        assert await AkahuProvider().trigger_refresh(_creds()) == "skipped"
    assert posted == []


@pytest.mark.asyncio
async def test_refresh_requested_and_polled_when_stale():
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    state = {"refreshed": stale}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["refreshed"] = datetime.now(timezone.utc).isoformat()
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"success": True, "items": [
            _account(refreshed={"transactions": state["refreshed"]}),
        ]})

    with _patched(handler), patch("app.providers.akahu.asyncio.sleep", new=AsyncMock()):
        assert await AkahuProvider().trigger_refresh(_creds()) == "refreshed"


@pytest.mark.asyncio
async def test_refresh_reports_user_action_on_bad_tokens():
    with _patched(lambda r: httpx.Response(401)):
        assert await AkahuProvider().trigger_refresh(_creds()) == "needs_user_action"


# ----- richer mapping -------------------------------------------------------------


def test_other_party_own_account_is_a_transfer():
    own = frozenset({"123456012345600"})
    raw = _txn(type="DIRECT CREDIT", category=None, amount=50,
               meta={"other_account": "12-3456-0123456-00"})
    assert category_hint_for(raw, own) == "transfers"


def test_payment_into_investment_provider_is_investments():
    invest = frozenset({"389000123456700"})
    raw = _txn(type="PAYMENT", category=None, meta={"other_account": "38-9000-1234567-00"})
    assert category_hint_for(raw, frozenset(), invest) == "investments"


@pytest.mark.parametrize("akahu_type,amount,expected", [
    ("INTEREST", 3.2, "investments"),
    ("INTEREST", -12.0, "taxes"),
    ("FEE", -5.0, "taxes"),
    ("TAX", -0.9, "taxes"),
    ("DIRECT CREDIT", 500.0, None),
])
def test_uncategorised_rows_fall_back_to_type(akahu_type, amount, expected):
    assert category_hint_for(_txn(type=akahu_type, amount=amount, category=None)) == expected


@pytest.mark.parametrize("name,expected", [
    ("Business software and cloud services", "subscriptions"),
    ("General retail stores", "shopping"),
    ("Haircuts and treatments", "personal_care"),
    ("Financial asset brokers, exchanges, and managed funds", "investments"),
])
def test_specific_nzfcc_categories_beat_their_group(name, expected):
    raw = _txn(category={"name": name, "groups": {"personal_finance": {"name": "Professional Services"}}})
    assert category_hint_for(raw) == expected


def test_masked_account_numbers_never_match():
    raw = _txn(type="PAYMENT", category=None, meta={"other_account": "12-****-*******-00"})
    assert category_hint_for(raw, frozenset({"120000000000000"})) is None


def test_notes_carry_particulars_code_reference():
    from app.providers.akahu import notes_for

    raw = _txn(meta={"particulars": "RENT", "code": "OCT", "reference": "FLAT 2", "card_suffix": "1234"})
    assert notes_for(raw) == "Particulars: RENT · Code: OCT · Reference: FLAT 2"
    assert notes_for(_txn(meta={})) is None


def _built(raw, **kw):
    t = AkahuProvider._build_transaction(raw, "NZD", "auto", **kw)
    assert t is not None
    return t


def test_merchant_website_reaches_the_payee():
    t = _built(_txn(merchant={"name": "Countdown", "website": "https://www.countdown.co.nz/"}))
    assert t.payee_website == "https://www.countdown.co.nz/"
    assert _built(_txn(merchant={"name": "Countdown", "website": "javascript:x"})).payee_website is None


LOGO = "https://cdn.akahu.nz/logos/merchants/merchant_1"


def test_merchant_logo_reaches_the_payee():
    assert _built(_txn(meta={"logo": LOGO})).payee_logo_url == LOGO


@pytest.mark.parametrize(
    "raw",
    [
        _txn(meta={"logo": "https://cdn.akahu.nz/logos/merchants/default.png"}),
        _txn(meta={"logo": "https://cdn.akahu.nz/logos/merchants/DEFAULT.png?v=2"}),
        _txn(merchant=None, meta={"logo": LOGO}),
        _txn(merchant={"name": "Countdown"}, meta={"logo": LOGO}),
        _txn(meta={"logo": "http://cdn.akahu.nz/logos/merchants/merchant_1"}),
        _txn(meta={}),
    ],
    ids=["placeholder", "placeholder-query", "no-merchant", "merchant-without-id", "not-https", "absent"],
)
def test_no_real_merchant_logo_leaves_the_payee_without_one(raw):
    from app.providers.akahu import merchant_logo_for

    assert merchant_logo_for(raw) is None


def test_no_logo_when_the_payee_is_not_the_merchant():
    t = AkahuProvider._build_transaction(_txn(meta={"logo": LOGO}), "NZD", "none")
    assert t is not None and t.payee is None and t.payee_logo_url is None


def test_foreign_purchase_uses_the_conversion():
    t = _built(_txn(amount=-5.4, meta={"conversion": {"amount": 2.15, "currency": "GBP", "rate": 0.49}}))
    assert (t.amount, t.currency, t.amount_in_account_currency) == (Decimal("2.15"), "GBP", Decimal("5.4"))
    assert t.type == "debit"


def test_conversion_in_account_currency_is_ignored():
    t = _built(_txn(amount=-5.4, meta={"conversion": {"amount": 5.4, "currency": "NZD"}}))
    assert (t.amount, t.currency, t.amount_in_account_currency) == (Decimal("5.4"), "NZD", None)


@pytest.mark.asyncio
async def test_get_transactions_uses_the_users_own_account_numbers():
    accounts = [
        _account(),
        _account(_id="acc_2", formatted_account="12-3456-7654321-00"),
        _account(_id="acc_ks", type="KIWISAVER", attributes=[], formatted_account=None,
                 meta={"payment_details": {"account_number": "38-9000-1234567-00"}}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": accounts})
        return httpx.Response(200, json={"success": True, "cursor": {}, "items": [
            _txn(_id="t_own", type="DEBIT", category=None, meta={"other_account": "12-3456-7654321-00"}),
            _txn(_id="t_ks", type="PAYMENT", category=None, meta={"other_account": "38-9000-1234567-00"}),
        ]})

    with _patched(handler):
        txns = await AkahuProvider().get_transactions(_creds(), "acc_1")
    assert {t.external_id: t.category_hint for t in txns} == {"t_own": "transfers", "t_ks": "investments"}


def _hatch_accounts(wallet_cash: float = 1000.00) -> list[dict]:
    """Hatch as Akahu sends it: the investment balance includes the wallet's cash,
    and one position is listed twice under two fund ids sharing a ticker."""
    position = {"symbol": "XMPL", "shares": 10, "value": 1500.0, "currency": "USD", "returns": 400.0}
    conn = {"_id": "conn_hatch", "name": "Hatch"}
    return [
        _account(_id="acc_hatch", name="Individual account", type="INVESTMENT", attributes=[], connection=conn,
                 balance={"currency": "USD", "current": 2500.00},
                 meta={"breakdown": {"returns": 400.0, "cost_basis": 1100},
                       "portfolio": [{**position, "fund_id": "f_etf", "name": "EXAMPLE ETF"},
                                     {**position, "fund_id": "f_example", "name": "Example Holdings Corp"}]}),
        _account(_id="acc_wallet", name="Individual account wallet", type="WALLET", attributes=[], connection=conn,
                 balance={"currency": "USD", "current": wallet_cash}),
    ]


@pytest.mark.asyncio
async def test_share_portfolio_becomes_one_holding_per_position_and_cash_counts_once():
    accounts = _hatch_accounts()
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": accounts})):
        holdings = await AkahuProvider().get_holdings(_creds())
    by_id = {h.external_id: h for h in holdings}
    assert set(by_id) == {"acc_hatch::XMPL", "acc_wallet"}
    pos = by_id["acc_hatch::XMPL"]
    assert pos.name == "Example Holdings Corp"
    assert (pos.ticker, pos.quantity, pos.current_value, pos.purchase_price) == (
        "XMPL", Decimal("10"), Decimal("1500.0"), Decimal("1100.0"))
    assert pos.currency == "USD" and pos.account_external_id == "acc_hatch"
    assert pos.metadata is not None and pos.metadata["fund_ids"] == ["f_etf", "f_example"]
    assert sum(h.current_value for h in holdings) == Decimal("2500.00")


@pytest.mark.asyncio
async def test_share_portfolio_cash_not_in_a_wallet_is_its_own_holding():
    accounts = _hatch_accounts(wallet_cash=50.0)
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": accounts})):
        holdings = await AkahuProvider().get_holdings(_creds())
    by_id = {h.external_id: h for h in holdings}
    assert set(by_id) == {"acc_hatch::XMPL", "acc_hatch::cash", "acc_wallet"}
    assert by_id["acc_hatch::cash"].current_value == Decimal("1000.00")


@pytest.mark.asyncio
async def test_holding_metadata_keeps_the_profile():
    accounts = [_account(_id="acc_ks", type="KIWISAVER", attributes=[], meta={"profile": {"pir": 0.28}})]
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": accounts})):
        holdings = await AkahuProvider().get_holdings(_creds())
    assert holdings[0].metadata is not None
    assert holdings[0].metadata["profile"] == {"pir": 0.28}


@pytest.mark.asyncio
async def test_card_debt_sign_depends_on_connection_type():
    accounts = [
        _account(_id="acc_classic", type="CREDITCARD",
                 connection={"_id": "c1", "name": "Amex", "connection_type": "classic"},
                 balance={"currency": "NZD", "current": -1234.56, "limit": 10000}),
        _account(_id="acc_official", type="CREDITCARD",
                 connection={"_id": "c2", "name": "Kiwibank", "connection_type": "official"},
                 balance={"currency": "NZD", "current": 250.0, "available": 4750.0}),
    ]
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": accounts})):
        result = {a.external_id: a.balance for a in await AkahuProvider().get_accounts(_creds())}
    assert result == {"acc_classic": Decimal("1234.56"), "acc_official": Decimal("250.0")}


# ----- loans ------------------------------------------------------------------


def _loan(**over) -> dict:
    """A fictional Akahu home loan: every figure is made up."""
    loan = _account(
        _id="acc_loan",
        name="Home Loan",
        type="LOAN",
        formatted_account="12-3456-0999999-90",
        balance={"currency": "NZD", "current": -300000.0},
        meta={"loan_details": {
            "purpose": "HOME",
            "type": "TABLE",
            "interest": {"rate": 5.25, "type": "FIXED", "expires_at": "2027-06-01T00:00:00Z",
                         "margin": "not documented"},
            "is_interest_only": False,
            "term": {"years": 25, "months": 0},
            "matures_at": "2049-01-01T00:00:00Z",
            "initial_principal": 350000,
            "repayment": {"frequency": "MONTHLY", "next_date": "2026-11-01T00:00:00Z",
                          "next_amount": 2000.0, "account": "12-3456-0123456-00"},
            "borrower": {"name": "Not documented"},
        }},
    )
    loan.update(over)
    return loan


@pytest.mark.asyncio
async def test_loan_becomes_a_loan_account_with_its_details():
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": [_loan()]})):
        (loan,) = await AkahuProvider().get_accounts(_creds())
    assert loan.type == "loan"
    assert loan.balance == Decimal("-300000.0")  # Akahu's sign: negative = owed
    assert loan.loan_subtype == "mortgage"
    assert loan.loan_details == {
        "purpose": "HOME",
        "type": "TABLE",
        "interest": {"rate": 5.25, "type": "FIXED", "expires_at": "2027-06-01T00:00:00Z"},
        "is_interest_only": False,
        "term": {"years": 25, "months": 0},
        "matures_at": "2049-01-01T00:00:00Z",
        "initial_principal": 350000,
        "repayment": {"frequency": "MONTHLY", "next_date": "2026-11-01T00:00:00Z",
                      "next_amount": 2000.0},
    }
    assert loan.credit_limit is None


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["PERSONAL", "UNKNOWN", None])
async def test_loan_subtype_only_from_a_home_purpose(purpose):
    raw = _loan(meta={"loan_details": {"purpose": purpose, "interest": {"rate": 9.9}}})
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": [raw]})):
        (loan,) = await AkahuProvider().get_accounts(_creds())
    assert loan.loan_subtype is None
    assert loan.loan_details is not None
    assert loan.loan_details["interest"] == {"rate": 9.9}


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [None, {}, {"loan_details": None}, {"loan_details": "n/a"},
                                  {"loan_details": {"borrower": "x", "interest": {"bank": "x"}}}])
async def test_loan_without_usable_details(meta):
    raw = _loan(meta=meta)
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": [raw]})):
        (loan,) = await AkahuProvider().get_accounts(_creds())
    assert loan.type == "loan"
    assert loan.loan_details is None
    assert loan.loan_subtype is None


@pytest.mark.asyncio
async def test_loan_details_only_on_loans():
    raw = _account(meta={"loan_details": {"purpose": "HOME"}})
    with _patched(lambda r: httpx.Response(200, json={"success": True, "items": [raw]})):
        (acc,) = await AkahuProvider().get_accounts(_creds())
    assert acc.type == "checking"
    assert acc.loan_details is None and acc.loan_subtype is None


@pytest.mark.parametrize("akahu_type,amount,expected", [
    ("TRANSFER", 2000, "transfers"),       # repayment into the loan
    ("CREDIT", 2000, "transfers"),         # any money in is a repayment
    ("LOAN", -1200, "loan_interest"),      # interest charged on the loan
    ("INTEREST", -1200, "loan_interest"),
    ("FEE", -10, "taxes"),                 # anything else falls through
])
def test_category_hints_on_a_loan_account(akahu_type, amount, expected):
    raw = _txn(type=akahu_type, amount=amount, category=None, merchant=None)
    assert category_hint_for(raw, account_type="LOAN") == expected


def test_loan_hints_apply_only_on_loan_accounts():
    raw = _txn(type="LOAN", amount=-1200, category=None, merchant=None)
    assert category_hint_for(raw) is None
    assert category_hint_for(raw, account_type="CHECKING") is None
    interest = _txn(type="INTEREST", amount=-3, category=None, merchant=None)
    assert category_hint_for(interest, account_type="CHECKING") == "taxes"


def test_loan_interest_beats_a_lending_category():
    raw = _txn(type="LOAN", amount=-1200, merchant=None,
               category={"name": "Lending services"})
    assert category_hint_for(raw, account_type="LOAN") == "loan_interest"
    assert category_hint_for(raw, account_type="CHECKING") == "other"


@pytest.mark.asyncio
async def test_get_transactions_on_a_loan_uses_the_loan_hints():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(200, json={"success": True, "items": [_account(), _loan()]})
        return httpx.Response(200, json={"success": True, "cursor": {}, "items": [
            _txn(_id="t_rep", _account="acc_loan", type="TRANSFER", amount=2000,
                 category=None, merchant=None),
            _txn(_id="t_int", _account="acc_loan", type="LOAN", amount=-1200,
                 category=None, merchant=None),
        ]})

    with _patched(handler):
        txns = await AkahuProvider().get_transactions(_creds(), "acc_loan")
    assert {t.external_id: (t.type, t.category_hint) for t in txns} == {
        "t_rep": ("credit", "transfers"),
        "t_int": ("debit", "loan_interest"),
    }
