"""Defaults are found by the key they were seeded from, not by their name.

A user who renames a default category ("Loan repayments" to something of
their own) keeps it working for every lookup that needs that default: provider
sync hints, transfer-pair and loan repayment legs, rule packs. Every name and
amount here is made up.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.category import Category
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.category import CategoryUpdate
from app.schemas.transaction import TransferCreate
from app.services import transaction_service
from app.services.category_service import (
    DEFAULT_CATEGORIES_I18N,
    create_default_categories,
    delete_category,
    find_default_category,
    update_category,
)
from app.services.connection_service import _match_category_hint, _match_pluggy_category
from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY
from app.services.rule_service import _ensure_categories_for_keys, _resolve_categories_by_internal_key
from tests.test_loan_repayments_category import _categories, _ledger


async def _rename(session: AsyncSession, workspace: Workspace, category_id: uuid.UUID, name: str) -> None:
    await update_category(session, category_id, workspace.id, CategoryUpdate(name=name))
    await session.commit()


async def _plain(session: AsyncSession, user: User, workspace: Workspace, name: str, **over) -> Category:
    cat = Category(
        id=over.pop("id", uuid.uuid4()), user_id=user.id, workspace_id=workspace.id, name=name,
        icon="tag", color="#000000", is_system=False, **over,
    )
    session.add(cat)
    await session.commit()
    return cat


async def _keys(session: AsyncSession, workspace: Workspace) -> dict[str, str | None]:
    rows = await session.execute(
        select(Category.name, Category.default_key)
        .where(Category.workspace_id == workspace.id)
        .execution_options(populate_existing=True)
    )
    return {name: key for name, key in rows}


async def _second_workspace(session: AsyncSession, user: User) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name="Second", created_by_user_id=user.id)
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=user.id, role="owner"))
    await session.commit()
    return ws


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "pt-BR", "de"])
async def test_seeded_defaults_carry_their_key(session, test_user, test_workspace, lang):
    await create_default_categories(session, test_user.id, lang, workspace_id=test_workspace.id)

    keys = await _keys(session, test_workspace)
    assert keys == {data.get(lang, data["en"]): key for key, data in DEFAULT_CATEGORIES_I18N.items()}


async def test_user_made_categories_carry_no_key(client, auth_headers, session, test_user, test_workspace):
    response = await client.post("/api/categories", headers=auth_headers, json={"name": "Example hobby"})
    assert response.status_code in (200, 201), response.text

    assert (await _keys(session, test_workspace)) == {"Example hobby": None}


async def test_rule_pack_seeding_sets_the_key_and_skips_a_renamed_default(session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    groceries = await find_default_category(session, test_workspace.id, "groceries")
    assert groceries is not None
    await _rename(session, test_workspace, groceries, "Example food shop")
    other = await find_default_category(session, test_workspace.id, "other")
    assert other is not None
    await delete_category(session, other, test_workspace.id)

    created = await _ensure_categories_for_keys(
        session, test_workspace.id, test_user.id, {"groceries", "other"}, "en"
    )

    # The renamed Groceries still counts; only the deleted Other comes back.
    assert created == 1
    keys = await _keys(session, test_workspace)
    assert keys["Example food shop"] == "groceries"
    assert keys["Other"] == "other"
    assert "Groceries" not in keys


def test_rule_templates_resolve_a_renamed_default_by_key():
    by_name = {"Example food shop": "id-groceries", "Transfers": "id-unkeyed-transfers"}
    by_key = {"groceries": "id-groceries"}

    resolved = _resolve_categories_by_internal_key(by_name, by_key)

    assert resolved["groceries"] == "id-groceries"
    # A key nothing carries still resolves by name, as before.
    assert resolved["transfers"] == "id-unkeyed-transfers"


def test_rule_templates_never_take_a_keyed_category_by_another_keys_name():
    # A default renamed to another default's name stays the default it was.
    by_name = {"Transfers": "id-other"}
    by_key = {"other": "id-other"}

    resolved = _resolve_categories_by_internal_key(by_name, by_key)

    assert resolved == {"other": "id-other"}


# ---------------------------------------------------------------------------
# lookups after a rename
# ---------------------------------------------------------------------------


async def test_a_renamed_default_is_still_found(session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    repayments = await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY)
    assert repayments is not None

    await _rename(session, test_workspace, repayments, "Example repayments")

    assert await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY) == repayments
    assert (await _keys(session, test_workspace))["Example repayments"] == LOAN_REPAYMENTS_KEY


async def test_sync_hint_resolves_a_renamed_default(session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    repayments = await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY)
    groceries = await find_default_category(session, test_workspace.id, "groceries")
    assert repayments is not None and groceries is not None
    await _rename(session, test_workspace, repayments, "Example repayments")
    await _rename(session, test_workspace, groceries, "Example food shop")

    # Principal legs keep landing in the renamed category, not in Transfers.
    assert await _match_category_hint(session, test_workspace.id, "loan_repayment") == repayments
    assert await _match_category_hint(session, test_workspace.id, "groceries") == groceries


async def test_sync_hint_falls_back_once_the_default_is_deleted(session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    repayments = await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY)
    transfers = await find_default_category(session, test_workspace.id, "transfers")
    assert repayments is not None and transfers is not None

    await delete_category(session, repayments, test_workspace.id)

    assert await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY) is None
    assert await _match_category_hint(session, test_workspace.id, "loan_repayment") == transfers


async def test_repayment_legs_follow_a_renamed_category(session, test_user, test_workspace):
    ledger = await _ledger(session, test_user, test_workspace)
    await _rename(session, test_workspace, ledger.repayments, "Example repayments")
    await _rename(session, test_workspace, ledger.transfers, "Example moves")

    debit, credit = await transaction_service.create_transfer(
        session, test_workspace.id, test_user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.loan.id,
                       amount=Decimal("800"), date=app_today(), description="Extra repayment"),
    )
    assert await _categories(session, debit, credit) == [ledger.repayments, ledger.repayments]

    debit, credit = await transaction_service.create_transfer(
        session, test_workspace.id, test_user.id,
        TransferCreate(from_account_id=ledger.checking.id, to_account_id=ledger.savings.id,
                       amount=Decimal("50"), date=app_today(), description="Move"),
    )
    assert await _categories(session, debit, credit) == [ledger.transfers, ledger.transfers]


async def test_pluggy_mapping_resolves_a_renamed_default(session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "pt-BR", workspace_id=test_workspace.id)
    food = await find_default_category(session, test_workspace.id, "food")
    assert food is not None
    await _rename(session, test_workspace, food, "Example meals")

    assert await _match_pluggy_category(session, test_workspace.id, "Eating out") == food


# ---------------------------------------------------------------------------
# rows without a key
# ---------------------------------------------------------------------------


async def test_unkeyed_rows_still_match_by_name(session, test_user, test_workspace):
    transfers = await _plain(session, test_user, test_workspace, "Transfers")

    assert await find_default_category(session, test_workspace.id, "transfers") == transfers.id
    assert await _match_category_hint(session, test_workspace.id, "loan_repayment") == transfers.id


async def test_the_keyed_category_wins_over_a_same_named_one(session, test_user, test_workspace):
    # The unkeyed one sorts first by id, so only the key can pick the other.
    await _plain(session, test_user, test_workspace, "Transfers", id=uuid.UUID(int=1))
    keyed = await _plain(
        session, test_user, test_workspace, "Example moves", id=uuid.UUID(int=2), default_key="transfers"
    )

    assert await find_default_category(session, test_workspace.id, "transfers") == keyed.id


async def test_a_keyed_category_named_like_another_default_is_not_that_default(session, test_user, test_workspace):
    await _plain(session, test_user, test_workspace, "Transfers", default_key="other")

    assert await find_default_category(session, test_workspace.id, "transfers") is None


async def test_unknown_keys_match_nothing(session, test_user, test_workspace):
    await _plain(session, test_user, test_workspace, "Example", default_key="no-such-key")

    assert await find_default_category(session, test_workspace.id, "no-such-key") is None


# ---------------------------------------------------------------------------
# one category per key in a workspace
# ---------------------------------------------------------------------------


async def test_a_key_is_unique_per_workspace(session, test_user, test_workspace):
    await _plain(session, test_user, test_workspace, "Example one", default_key="transfers")

    with pytest.raises(IntegrityError):
        await _plain(session, test_user, test_workspace, "Example two", default_key="transfers")
    await session.rollback()


async def test_the_same_key_in_two_workspaces_and_many_unkeyed_rows_are_fine(session, test_user, test_workspace):
    other = await _second_workspace(session, test_user)
    await _plain(session, test_user, test_workspace, "Example one", default_key="transfers")
    await _plain(session, test_user, other, "Example two", default_key="transfers")
    await _plain(session, test_user, test_workspace, "Example three")
    await _plain(session, test_user, test_workspace, "Example four")

    assert (await _keys(session, test_workspace)) == {
        "Example one": "transfers", "Example three": None, "Example four": None,
    }
