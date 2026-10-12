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


def _creds(app: bool = True) -> dict:
    creds = {"user_token_enc": encrypt(USER)}
    if app:
        creds["app_token_enc"] = encrypt(APP)
    return creds


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


def test_parse_tokens_user_only():
    assert parse_tokens(f"  {USER}\n") == (USER, None)


def test_parse_tokens_both_in_any_order():
    assert parse_tokens(f"{APP} {USER}") == (USER, APP)


def test_parse_tokens_requires_user_token():
    with pytest.raises(ValueError):
        parse_tokens(APP)


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
async def test_app_token_falls_back_to_settings():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["app"] = request.headers.get("x-akahu-id")
        return httpx.Response(200, json={"success": True, "items": []})

    from app.core.config import get_settings
    from pydantic import SecretStr

    settings = get_settings()
    with patch.object(settings, "akahu_app_token", SecretStr("app_token_from_env")), _patched(handler):
        await AkahuProvider().get_accounts(_creds(app=False))
    assert seen["app"] == "app_token_from_env"


@pytest.mark.asyncio
async def test_missing_app_token_needs_user_action():
    from app.core.config import get_settings
    from pydantic import SecretStr

    settings = get_settings()
    with patch.object(settings, "akahu_app_token", SecretStr("")):
        with pytest.raises(ProviderUserActionRequired):
            await AkahuProvider().get_accounts(_creds(app=False))


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
