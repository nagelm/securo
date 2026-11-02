"""Akahu removals and re-issues on real PostgreSQL, whose foreign keys
(SQLite in the other file does not enforce them) must accept every delete
and every link moved to a re-issued row. Every name and figure is made up."""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.reconciliation import ReconciliationEvent, ReconciliationSuggestion
from app.models.transaction_attachment import TransactionAttachment
from app.services.provider_transaction_changes import REMOVED_TAG
from tests.test_akahu_transaction_changes import (
    FakeAkahu,
    akahu_connection,
    akahu_row,
    attach,
    category,
    run_sync,
    synced,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixtures start in a fresh per-test PostgreSQL schema."""


def _suggestion(workspace_id, tx_id, expectation_id) -> ReconciliationSuggestion:
    return ReconciliationSuggestion(
        workspace_id=workspace_id, transaction_id=tx_id, expectation_kind="recurring",
        expectation_id=expectation_id, strategy_id="test", node="test",
        amount=Decimal("40"), scores={}, status="pending",
    )


def _event(workspace_id, tx_id, expectation_id) -> ReconciliationEvent:
    return ReconciliationEvent(
        workspace_id=workspace_id, action="suggested", transaction_id=tx_id,
        expectation_kind="recurring", expectation_id=expectation_id, amount=Decimal("40"),
    )


async def test_a_removed_row_with_matching_history_is_deleted(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_keep", 10, -12.5), akahu_row("trans_gone", 11, -40.0)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    gone = (await synced(session, conn))["trans_gone"]
    expectation = uuid.uuid4()
    session.add(_suggestion(test_workspace.id, gone.id, expectation))
    event = _event(test_workspace.id, gone.id, expectation)
    session.add(event)
    await session.commit()

    fake.rows = [akahu_row("trans_keep", 10, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)

    assert set(await synced(session, conn)) == {"trans_keep"}
    # The open question goes with the row; the history keeps its record.
    assert (await session.execute(select(ReconciliationSuggestion))).scalars().all() == []
    kept = await session.get(ReconciliationEvent, event.id, populate_existing=True)
    assert kept is not None and kept.transaction_id is None


async def test_a_removed_row_with_an_attachment_is_kept(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_keep", 10, -12.5), akahu_row("trans_receipt", 11, -40.0)])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    await attach(session, (await synced(session, conn))["trans_receipt"])

    fake.rows = [akahu_row("trans_keep", 10, -12.5)]
    await run_sync(session, conn, test_workspace, test_user, fake)

    assert (await synced(session, conn))["trans_receipt"].notes == REMOVED_TAG


async def test_a_reissued_row_takes_over_every_link(session, test_user, test_workspace):
    fake = FakeAkahu([akahu_row("trans_keep", 10, -12.5), akahu_row("trans_old", 12, -40.0, "POS 99 SHOP")])
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, fake)
    gifts = await category(session, test_workspace, "Gifts")
    old = (await synced(session, conn))["trans_old"]
    old.category_id = gifts.id
    expectation = uuid.uuid4()
    session.add(_suggestion(test_workspace.id, old.id, expectation))
    event = _event(test_workspace.id, old.id, expectation)
    session.add(event)
    await session.commit()
    await attach(session, old)

    fake.rows = [akahu_row("trans_keep", 10, -12.5), akahu_row("trans_new", 13, -40.0, "SHOP LTD")]
    await run_sync(session, conn, test_workspace, test_user, fake)

    rows = await synced(session, conn)
    assert set(rows) == {"trans_keep", "trans_new"}
    new = rows["trans_new"]
    assert new.category_id == gifts.id
    attachment = (await session.execute(select(TransactionAttachment))).scalar_one()
    assert attachment.transaction_id == new.id
    suggestion = (
        await session.execute(
            select(ReconciliationSuggestion).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert suggestion.transaction_id == new.id
    moved = await session.get(ReconciliationEvent, event.id, populate_existing=True)
    assert moved is not None and moved.transaction_id == new.id
