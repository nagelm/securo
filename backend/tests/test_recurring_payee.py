"""Fork: recurring items carry a payee (f012).

The migration's matching rule and backfill, the API (workspace scoping and
the payee's display fields), and the payee reaching the rows an item
generates or links. Every name and amount is made up.
"""

import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.payee import Payee
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.services import payee_service, recurring_match_service
from app.services.payee_display import payee_logo_url
from app.services.recurring_transaction_service import generate_pending

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f012_recurring_payee.py"
_SPEC = importlib.util.spec_from_file_location("f012_recurring_payee", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


# ---------------------------------------------------------------------------
# payee_for_description: the pure rule
# ---------------------------------------------------------------------------

_NETFLIX, _ACME, _ACME_INS, _TWO, _SPOT = (uuid.uuid4() for _ in range(5))


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Netflix", _NETFLIX),
        ("netflix", _NETFLIX),
        ("  NETFLIX ", _NETFLIX),
        ("Spotify", _SPOT),
        ("Acme house", _ACME),
        ("Acme: contents", _ACME),
        ("Acme-contents", _ACME),
        ("4seasons mobile", _TWO),
        # The longest matching name wins.
        ("Acme insurance", _ACME_INS),
        ("Acme insurance car", _ACME_INS),
        # The name has to end at a word boundary.
        ("Acmeville rent", None),
        ("Netflixx", None),
        ("Rent", None),
        ("", None),
    ],
)
def test_payee_for_description(description, expected):
    candidates = [
        (_NETFLIX, "Netflix"),
        (_ACME, "Acme"),
        (_ACME_INS, "Acme Insurance"),
        (_TWO, "4seasons"),
        (_SPOT, "Spotify"),
    ]
    assert migration.payee_for_description(description, candidates) == expected


def test_payee_for_description_tie_is_none():
    a, b = uuid.uuid4(), uuid.uuid4()
    assert migration.payee_for_description("Netflix plan", [(a, "Netflix"), (b, "NETFLIX")]) is None
    # A longer match settles a tie between shorter ones.
    c = uuid.uuid4()
    assert (
        migration.payee_for_description("Netflix plan", [(a, "Netflix"), (b, "NETFLIX"), (c, "Netflix Plan")])
        == c
    )


# ---------------------------------------------------------------------------
# backfill: against the test database
# ---------------------------------------------------------------------------


async def _payee(session: AsyncSession, user: User, ws_id: uuid.UUID, name: str, **kw) -> Payee:
    payee = Payee(id=uuid.uuid4(), user_id=user.id, workspace_id=ws_id, name=name, **kw)
    session.add(payee)
    await session.flush()
    return payee


async def _recurring(
    session: AsyncSession, user: User, ws_id: uuid.UUID, account: Account, description: str, **kw
) -> RecurringTransaction:
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=ws_id,
        account_id=account.id,
        description=description,
        amount=Decimal(kw.pop("amount", "10.00")),
        currency="NZD",
        type="debit",
        frequency="monthly",
        start_date=kw.pop("start_date", date(2026, 1, 5)),
        next_occurrence=kw.pop("next_occurrence", date(2026, 1, 5)),
        **kw,
    )
    session.add(rec)
    await session.flush()
    return rec


async def _tx(
    session: AsyncSession, user: User, account: Account, rec: RecurringTransaction | None, when: date, **kw
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        account_id=account.id,
        description=kw.pop("description", "Card charge"),
        amount=Decimal(kw.pop("amount", "10.00")),
        currency="NZD",
        date=when,
        type="debit",
        source=kw.pop("source", "manual"),
        recurring_transaction_id=rec.id if rec else None,
        **kw,
    )
    session.add(tx)
    await session.flush()
    return tx


@pytest_asyncio.fixture
async def manual_account(session: AsyncSession, test_user: User) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Everyday", type="checking",
        balance=Decimal("1000"), currency="NZD",
    )
    session.add(account)
    await session.commit()
    return account


async def _run_backfill(session: AsyncSession) -> int:
    def go(sync_session):
        connection = sync_session.connection()
        schema = (connection.get_execution_options().get("schema_translate_map") or {}).get(None)
        if schema and connection.dialect.name == "postgresql":
            # The PostgreSQL tests isolate each test in a schema through a
            # translate map, which plain `sa.table()` clauses don't follow.
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        return migration.backfill(connection)

    updated = await session.run_sync(go)
    await session.commit()
    return updated


async def _payee_of(session: AsyncSession, rec: RecurringTransaction):
    return (
        await session.execute(
            select(RecurringTransaction.payee_id).where(RecurringTransaction.id == rec.id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_backfill(session: AsyncSession, test_user: User, test_workspace: Workspace, manual_account):
    ws = test_workspace.id
    netflix = await _payee(session, test_user, ws, "Netflix")
    streaming = await _payee(session, test_user, ws, "Streaming Co")
    acme = await _payee(session, test_user, ws, "Acme")
    # Names are unique per workspace under the database's lower(); the match
    # folds case fully (casefold), so these two are a tie.
    await _payee(session, test_user, ws, "Straße")
    await _payee(session, test_user, ws, "Strasse")
    other_ws = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other_ws)
    await session.flush()
    await _payee(session, test_user, other_ws.id, "Gym")

    # Linked rows: the most recent one with a payee wins over the name.
    linked = await _recurring(session, test_user, ws, manual_account, "Netflix")
    await _tx(session, test_user, manual_account, linked, date(2026, 1, 5), payee_id=netflix.id)
    await _tx(session, test_user, manual_account, linked, date(2026, 2, 5), payee_id=streaming.id)
    await _tx(session, test_user, manual_account, linked, date(2026, 3, 5))  # no payee
    by_name = await _recurring(session, test_user, ws, manual_account, "Acme insurance")
    linked_without_payee = await _recurring(session, test_user, ws, manual_account, "netflix")
    await _tx(session, test_user, manual_account, linked_without_payee, date(2026, 3, 5))
    ambiguous = await _recurring(session, test_user, ws, manual_account, "Strasse cafe")
    other_workspace_only = await _recurring(session, test_user, ws, manual_account, "Gym")
    no_match = await _recurring(session, test_user, ws, manual_account, "Rent")
    kept = await _recurring(session, test_user, ws, manual_account, "Acme", payee_id=netflix.id)
    await session.commit()

    assert await _run_backfill(session) == 3

    assert await _payee_of(session, linked) == streaming.id
    assert await _payee_of(session, by_name) == acme.id
    assert await _payee_of(session, linked_without_payee) == netflix.id
    assert await _payee_of(session, ambiguous) is None
    assert await _payee_of(session, other_workspace_only) is None
    assert await _payee_of(session, no_match) is None
    assert await _payee_of(session, kept) == netflix.id

    # Idempotent.
    assert await _run_backfill(session) == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _body(account_id, **kw):
    return {
        "description": "Netflix",
        "amount": 25.99,
        "currency": "NZD",
        "type": "debit",
        "frequency": "monthly",
        "start_date": "2026-03-01",
        "account_id": str(account_id),
        **kw,
    }


@pytest.mark.asyncio
async def test_api_create_read_update_with_payee(
    client, auth_headers, session: AsyncSession, test_user: User, test_workspace: Workspace, manual_account
):
    logo = await _payee(session, test_user, test_workspace.id, "Netflix", website="https://www.netflix.com")
    icon = await _payee(session, test_user, test_workspace.id, "Corner dairy", icon="store", logo_url="https://example.com/l.png")
    await session.commit()

    response = await client.post(
        "/api/recurring-transactions", json=_body(manual_account.id, payee_id=str(logo.id)), headers=auth_headers
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["payee_id"] == str(logo.id)
    assert created["payee_name"] == "Netflix"
    assert created["payee_logo_url"] == payee_logo_url(logo)
    assert created["payee_logo_url"] is not None
    assert created["payee_icon"] is None

    listed = (await client.get("/api/recurring-transactions", headers=auth_headers)).json()
    (row,) = [r for r in listed if r["id"] == created["id"]]
    assert (row["payee_id"], row["payee_name"], row["payee_logo_url"]) == (
        str(logo.id), "Netflix", payee_logo_url(logo),
    )

    response = await client.patch(
        f"/api/recurring-transactions/{created['id']}", json={"payee_id": str(icon.id)}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["payee_id"] == str(icon.id)
    assert (updated["payee_name"], updated["payee_icon"], updated["payee_logo_url"]) == (
        "Corner dairy", "store", "https://example.com/l.png",
    )

    response = await client.patch(
        f"/api/recurring-transactions/{created['id']}", json={"payee_id": None}, headers=auth_headers
    )
    assert response.status_code == 200
    cleared = response.json()
    assert (cleared["payee_id"], cleared["payee_name"], cleared["payee_logo_url"], cleared["payee_icon"]) == (
        None, None, None, None,
    )

    # Untouched when the update doesn't name it.
    await client.patch(
        f"/api/recurring-transactions/{created['id']}", json={"payee_id": str(logo.id)}, headers=auth_headers
    )
    response = await client.patch(
        f"/api/recurring-transactions/{created['id']}", json={"description": "Netflix premium"}, headers=auth_headers
    )
    assert response.json()["payee_id"] == str(logo.id)


@pytest.mark.asyncio
async def test_api_rejects_a_payee_of_another_workspace(
    client, auth_headers, session: AsyncSession, test_user: User, test_workspace: Workspace, manual_account
):
    other_ws = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other_ws)
    await session.flush()
    foreign = await _payee(session, test_user, other_ws.id, "Netflix")
    mine = await _payee(session, test_user, test_workspace.id, "Netflix")
    await session.commit()

    response = await client.post(
        "/api/recurring-transactions", json=_body(manual_account.id, payee_id=str(foreign.id)), headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Payee not found"

    response = await client.post(
        "/api/recurring-transactions", json=_body(manual_account.id, payee_id=str(mine.id)), headers=auth_headers
    )
    assert response.status_code == 201
    response = await client.patch(
        f"/api/recurring-transactions/{response.json()['id']}",
        json={"payee_id": str(foreign.id)},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Payee not found"


# ---------------------------------------------------------------------------
# generated and linked rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generated_placeholder_carries_the_payee(
    session: AsyncSession, test_user: User, test_workspace: Workspace, manual_account
):
    netflix = await _payee(session, test_user, test_workspace.id, "Netflix")
    rec = await _recurring(
        session, test_user, test_workspace.id, manual_account, "Netflix",
        payee_id=netflix.id, start_date=date(2026, 1, 5), next_occurrence=date(2026, 1, 5),
    )
    await session.commit()

    assert await generate_pending(session, test_user.id, up_to=date(2026, 2, 10)) == 2

    rows = (
        await session.execute(select(Transaction).where(Transaction.recurring_transaction_id == rec.id))
    ).scalars().all()
    assert len(rows) == 2
    assert {row.payee_id for row in rows} == {netflix.id}


@pytest.mark.asyncio
async def test_linked_real_row_takes_the_payee_only_when_it_has_none(
    session: AsyncSession, test_user: User, test_workspace: Workspace, manual_account
):
    netflix = await _payee(session, test_user, test_workspace.id, "Netflix")
    bank = await _payee(session, test_user, test_workspace.id, "NETFLIX.COM")
    rec = await _recurring(
        session, test_user, test_workspace.id, manual_account, "Netflix",
        payee_id=netflix.id, start_date=date(2026, 1, 5), next_occurrence=date(2026, 1, 5),
    )
    jan = await _tx(session, test_user, manual_account, None, date(2026, 1, 5), description="Netflix")
    feb = await _tx(
        session, test_user, manual_account, None, date(2026, 2, 5), description="Netflix", payee_id=bank.id
    )
    await session.commit()

    assert await generate_pending(session, test_user.id, up_to=date(2026, 2, 10)) == 0

    await session.refresh(jan)
    await session.refresh(feb)
    assert (jan.recurring_transaction_id, jan.payee_id) == (rec.id, netflix.id)
    assert (feb.recurring_transaction_id, feb.payee_id) == (rec.id, bank.id)


def test_carry_payee():
    payee_id, own = uuid.uuid4(), uuid.uuid4()
    rec = RecurringTransaction(payee_id=payee_id)
    bare, kept = Transaction(), Transaction(payee_id=own)
    recurring_match_service.carry_payee(rec, bare)
    recurring_match_service.carry_payee(rec, kept)
    assert (bare.payee_id, kept.payee_id) == (payee_id, own)
    untouched = Transaction()
    recurring_match_service.carry_payee(RecurringTransaction(), untouched)
    assert untouched.payee_id is None


# ---------------------------------------------------------------------------
# payee delete and merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payee_merge_and_delete_follow_to_recurring(
    session: AsyncSession, test_user: User, test_workspace: Workspace, manual_account
):
    ws = test_workspace.id
    target = await _payee(session, test_user, ws, "Netflix")
    source = await _payee(session, test_user, ws, "NETFLIX.COM")
    rec = await _recurring(session, test_user, ws, manual_account, "Netflix", payee_id=source.id)
    await session.commit()

    await payee_service.merge_payees(session, ws, target.id, [source.id])
    assert await _payee_of(session, rec) == target.id

    assert await payee_service.delete_payee(session, target.id, ws)
    assert await _payee_of(session, rec) is None
