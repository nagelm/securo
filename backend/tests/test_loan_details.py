"""User-entered loan details: validation, the API, the sync merge, and that
they never move a balance, a P&L figure or a projection.

``Account.loan_details`` keeps Akahu's ``meta.loan_details`` shape whoever
writes it. Every figure here is made up.
"""

import pytest
from httpx import AsyncClient

from app.core.app_clock import app_today
from app.providers.akahu import _LOAN_DETAIL_KEYS
from app.schemas.account import AccountCreate, AccountUpdate
from app.services.account_service import create_account, update_account
from app.services.loan_details import (
    USER_LOAN_DETAIL_FIELDS,
    normalize_user_loan_details,
)
from app.services.report_service import _net_worth_at
from tests import test_loan_pnl_invariant as invariant
from tests.test_loan_accounts import _balance, _connection
from tests.test_loan_pnl_guard import sqlite_to_char  # noqa: F401  (fixture)
from tests.test_recurring_loan_payments import PROJECTION_PROBES, _schedule

# Checking +10,000, manual loan -300,000, "Loan interest" category.
book = invariant.book

FULL = {
    "purpose": "home",
    "type": "TABLE",
    "interest": {"rate": "5.125", "type": "FIXED", "expires_at": "2027-06-01"},
    "is_interest_only": False,
    "interest_only_expires_at": "2026-12-01T00:00:00Z",
    "term": {"years": 25, "months": 6},
    "matures_at": "2050-06-01",
    "initial_principal": "350000.005",
    "repayment": {"frequency": "FORTNIGHTLY", "next_date": "2026-11-01", "next_amount": 950.5},
}
FULL_NORMALISED = {
    "purpose": "HOME",
    "type": "TABLE",
    "interest": {"rate": 5.125, "type": "FIXED", "expires_at": "2027-06-01"},
    "is_interest_only": False,
    "interest_only_expires_at": "2026-12-01",
    "term": {"years": 25, "months": 6},
    "matures_at": "2050-06-01",
    "initial_principal": 350000.01,
    "repayment": {"frequency": "FORTNIGHTLY", "next_date": "2026-11-01", "next_amount": 950.5},
}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_user_fields_are_the_keys_akahu_documents():
    assert set(USER_LOAN_DETAIL_FIELDS) == set(_LOAN_DETAIL_KEYS)
    for key, sub_keys in _LOAN_DETAIL_KEYS.items():
        spec = USER_LOAN_DETAIL_FIELDS[key]
        if sub_keys:
            assert isinstance(spec, dict) and set(spec) == set(sub_keys), key
        elif sub_keys == ():
            assert not isinstance(spec, dict), key
    # `term`'s shape is undocumented in the guide; the API reference gives
    # years and months.
    term = USER_LOAN_DETAIL_FIELDS["term"]
    assert isinstance(term, dict) and set(term) == {"years", "months"}


def test_full_details_normalise_to_json_safe_values():
    assert normalize_user_loan_details(FULL) == FULL_NORMALISED


@pytest.mark.parametrize("value", [None, {}, {"purpose": None, "interest": {"rate": ""}, "term": {}}])
def test_empty_details_clear(value):
    assert normalize_user_loan_details(value) is None


def test_blank_values_are_dropped():
    assert normalize_user_loan_details(
        {"purpose": "", "interest": {"rate": 4, "type": None}, "matures_at": None}
    ) == {"interest": {"rate": 4}}


@pytest.mark.parametrize(
    "value, message",
    [
        ({"interest_rate": 5}, "Unknown loan_details keys: interest_rate"),
        ({"interest": {"margin": 1}}, "Unknown loan_details.interest keys: margin"),
        ({"term": {"weeks": 3}}, "Unknown loan_details.term keys: weeks"),
        ("5%", "loan_details must be an object"),
        ({"interest": 5}, "loan_details.interest must be an object"),
        ({"purpose": "BOAT"}, "loan_details.purpose must be one of"),
        ({"interest": {"type": "VARIABLE"}}, "loan_details.interest.type must be one of"),
        ({"repayment": {"frequency": "DAILY"}}, "loan_details.repayment.frequency must be one of"),
        ({"interest": {"rate": "abc"}}, "loan_details.interest.rate must be a number"),
        ({"interest": {"rate": True}}, "loan_details.interest.rate must be a number"),
        ({"interest": {"rate": 101}}, "loan_details.interest.rate must be between"),
        ({"interest": {"rate": -1}}, "loan_details.interest.rate must be between"),
        ({"initial_principal": "NaN"}, "loan_details.initial_principal must be between"),
        ({"repayment": {"next_amount": -5}}, "loan_details.repayment.next_amount must be between"),
        ({"term": {"years": 2.5}}, "loan_details.term.years must be a whole number"),
        ({"matures_at": "next year"}, "loan_details.matures_at must be a date"),
        ({"matures_at": 20500101}, "loan_details.matures_at must be a date"),
        ({"is_interest_only": "yes"}, "loan_details.is_interest_only must be true or false"),
    ],
)
def test_bad_details_are_refused(value, message):
    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        normalize_user_loan_details(value)


# ---------------------------------------------------------------------------
# the service and the API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_edit_loan_details_over_api(client: AsyncClient, auth_headers):
    created = await client.post(
        "/api/accounts",
        json={"name": "Test loan", "type": "loan", "balance": "1000", "currency": "BRL",
              "loan_details": FULL},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["loan_details"] == FULL_NORMALISED
    loan_id = created.json()["id"]

    edited = await client.patch(
        f"/api/accounts/{loan_id}",
        json={"loan_details": {"interest": {"rate": 6.5, "type": "FLOATING"}}},
        headers=auth_headers,
    )
    assert edited.status_code == 200, edited.text
    # The request replaces the details as a whole.
    assert edited.json()["loan_details"] == {"interest": {"rate": 6.5, "type": "FLOATING"}}

    # Other fields leave the details alone.
    renamed = await client.patch(
        f"/api/accounts/{loan_id}", json={"name": "Renamed loan"}, headers=auth_headers
    )
    assert renamed.json()["loan_details"] == {"interest": {"rate": 6.5, "type": "FLOATING"}}

    cleared = await client.patch(
        f"/api/accounts/{loan_id}", json={"loan_details": None}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["loan_details"] is None


@pytest.mark.asyncio
async def test_bad_loan_details_are_a_400(client: AsyncClient, auth_headers):
    created = await client.post(
        "/api/accounts",
        json={"name": "Test loan", "type": "loan", "balance": "1000", "currency": "BRL"},
        headers=auth_headers,
    )
    loan_id = created.json()["id"]

    unknown = await client.patch(
        f"/api/accounts/{loan_id}", json={"loan_details": {"lender": "x"}}, headers=auth_headers
    )
    assert unknown.status_code == 400
    assert "Unknown loan_details keys: lender" in unknown.json()["detail"]

    on_create = await client.post(
        "/api/accounts",
        json={"name": "Other loan", "type": "loan", "balance": "1000", "currency": "BRL",
              "loan_details": {"interest": {"rate": "high"}}},
        headers=auth_headers,
    )
    assert on_create.status_code == 400


@pytest.mark.asyncio
async def test_loan_details_refused_on_other_types(client: AsyncClient, auth_headers):
    on_create = await client.post(
        "/api/accounts",
        json={"name": "Everyday", "type": "checking", "balance": "10", "currency": "BRL",
              "loan_details": {"interest": {"rate": 5}}},
        headers=auth_headers,
    )
    assert on_create.status_code == 400
    assert on_create.json()["detail"] == "Loan fields can only be set on loan accounts"

    checking = await client.post(
        "/api/accounts",
        json={"name": "Everyday", "type": "checking", "balance": "10", "currency": "BRL"},
        headers=auth_headers,
    )
    on_update = await client.patch(
        f"/api/accounts/{checking.json()['id']}",
        json={"loan_details": {"interest": {"rate": 5}}},
        headers=auth_headers,
    )
    assert on_update.status_code == 400
    # A null is always allowed: it is how a field is cleared.
    null = await client.patch(
        f"/api/accounts/{checking.json()['id']}", json={"loan_details": None}, headers=auth_headers
    )
    assert null.status_code == 200


@pytest.mark.asyncio
async def test_connected_loan_details_are_editable(session, test_user, test_workspace):
    conn = await _connection(session, test_user)
    loan = await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Bank loan", type="loan", currency="BRL"),
    )
    loan.connection_id = conn.id
    await session.commit()

    updated = await update_account(
        session, loan.id, test_workspace.id,
        AccountUpdate(loan_details={"interest": {"rate": 7}, "term": {"years": 5}}),
    )
    assert updated is not None
    assert updated.loan_details == {"interest": {"rate": 7}, "term": {"years": 5}}

    with pytest.raises(ValueError, match="Unknown loan_details keys"):
        await update_account(
            session, loan.id, test_workspace.id, AccountUpdate(loan_details={"bank": "x"})
        )


@pytest.mark.asyncio
async def test_turning_into_a_loan_with_details_in_one_request(session, test_user, test_workspace):
    account = await create_account(
        session, test_workspace.id, test_user.id,
        AccountCreate(name="Was checking", type="checking", currency="BRL"),
    )
    updated = await update_account(
        session, account.id, test_workspace.id,
        AccountUpdate(type="loan", loan_details={"repayment": {"frequency": "monthly"}}),
    )
    assert updated is not None
    assert updated.loan_details == {"repayment": {"frequency": "MONTHLY"}}


# ---------------------------------------------------------------------------
# display only: editing the details moves no figure anywhere
# ---------------------------------------------------------------------------


async def _every_figure(session, book) -> dict:
    figures: dict[str, object] = {
        name: await probe(session, book) for name, probe in invariant.PROBES.items()
    }
    figures |= {f"projected:{name}": await probe(session, book) for name, probe in PROJECTION_PROBES.items()}
    for account in (book.checking, book.loan):
        figures[f"balance:{account.id}"] = await _balance(session, book.workspace, account.id)
    net_worth = await _net_worth_at(session, book.workspace.id, app_today(), primary_currency="BRL")
    figures["net_worth"] = (net_worth.value, net_worth.breakdowns)
    return figures


@pytest.mark.asyncio
async def test_editing_loan_details_changes_no_report(session, book, sqlite_to_char):  # noqa: F811
    await invariant._pay(session, book)
    await _schedule(session, book)
    before = await _every_figure(session, book)

    await update_account(
        session, book.loan.id, book.workspace.id,
        AccountUpdate(loan_details={
            "interest": {"rate": 99, "type": "FLOATING"},
            "initial_principal": 900000000,
            "term": {"years": 1},
            "matures_at": app_today().isoformat(),
            "repayment": {"frequency": "WEEKLY", "next_date": app_today().isoformat(),
                          "next_amount": 123456},
        }),
    )
    assert (await _every_figure(session, book)) == before

    await update_account(session, book.loan.id, book.workspace.id, AccountUpdate(loan_details=None))
    assert (await _every_figure(session, book)) == before

