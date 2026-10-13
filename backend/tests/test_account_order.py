"""The user's own account order: PUT /api/accounts/order and every list
that follows it.

Every name and amount here is made up.
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.akahu import AkahuProvider
from app.providers.base import AccountData, ConnectionData
from app.services import account_service, search_service
from app.services.account_order import reorder_accounts
from app.services.connection_service import handle_oauth_callback
from tests.test_akahu_sync import (
    BANK_A,
    _raw,
    accounts_by_external_id,
    akahu_connection,
    bank_accounts,
    money_view,
    nzd_user,
    run_sync,
    sync_salaried,
)

pytestmark = pytest.mark.asyncio


async def _account(session: AsyncSession, user, workspace, name: str, *, position: int = 0,
                   display_name: str | None = None, **over) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name,
        display_name=display_name, type=over.pop("type", "checking"),
        balance=Decimal("0"), currency="BRL", position=position, **over,
    )
    session.add(account)
    await session.commit()
    return account


async def _listed(client: AsyncClient, headers: dict, **params) -> list[str]:
    response = await client.get("/api/accounts", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return [a["display_name"] or a["name"] for a in response.json()]


async def _put(client: AsyncClient, headers: dict, ids: list) -> int:
    response = await client.put(
        "/api/accounts/order", headers=headers, json={"account_ids": [str(i) for i in ids]},
    )
    return response.status_code


async def _positions(session: AsyncSession, workspace) -> dict[str, int]:
    rows = await session.execute(
        select(Account).where(Account.workspace_id == workspace.id)
        .execution_options(populate_existing=True)
    )
    return {a.name: a.position for a in rows.scalars().all()}


async def _other_workspace(session: AsyncSession, owner_id: uuid.UUID) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name="Elsewhere", kind="personal",
                   created_by_user_id=owner_id, default_currency="BRL")
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=owner_id, role="owner"))
    await session.commit()
    return ws


# ----- Listing ------------------------------------------------------------------


async def test_the_list_follows_position_not_the_alphabet(client, auth_headers, session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Alpha", position=2)
    await _account(session, test_user, test_workspace, "Bravo", position=0)
    await _account(session, test_user, test_workspace, "Charlie", position=1)

    assert await _listed(client, auth_headers) == ["Bravo", "Charlie", "Alpha"]
    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    assert [a["position"] for a in listed] == [0, 1, 2]


async def test_ties_fall_back_to_the_name_the_user_sees(client, auth_headers, session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Zulu")
    await _account(session, test_user, test_workspace, "Alpha", display_name="Yankee")
    await _account(session, test_user, test_workspace, "Mike")

    assert await _listed(client, auth_headers) == ["Mike", "Yankee", "Zulu"]


async def test_closed_accounts_list_in_the_same_order(client, auth_headers, session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Alpha", position=1)
    await _account(session, test_user, test_workspace, "Bravo", position=0, is_closed=True)
    await _account(session, test_user, test_workspace, "Charlie", position=2)

    assert await _listed(client, auth_headers) == ["Alpha", "Charlie"]
    assert await _listed(client, auth_headers, include_closed=True) == ["Bravo", "Alpha", "Charlie"]


async def test_search_lists_matching_accounts_in_the_users_order(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Card Alpha", position=1)
    await _account(session, test_user, test_workspace, "Card Bravo", position=0)

    hits = await search_service.search_all(session, test_workspace.id, test_user.id, "card")
    assert [h["label"] for h in hits if h["type"] == "account"] == ["Card Bravo", "Card Alpha"]


# ----- New accounts -------------------------------------------------------------


async def test_a_new_manual_account_goes_to_the_end(client, auth_headers, session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Zulu", position=0)
    await _account(session, test_user, test_workspace, "Yankee", position=4)

    response = await client.post("/api/accounts", headers=auth_headers,
                                 json={"name": "Alpha", "type": "checking"})
    assert response.status_code == 201, response.text
    assert response.json()["position"] == 5
    assert await _listed(client, auth_headers) == ["Zulu", "Yankee", "Alpha"]


async def test_the_first_account_of_a_workspace_gets_position_zero(client, auth_headers):
    response = await client.post("/api/accounts", headers=auth_headers,
                                 json={"name": "Only", "type": "checking"})
    assert response.status_code == 201, response.text
    assert response.json()["position"] == 0


async def test_another_workspace_does_not_push_new_accounts_back(client, auth_headers, session, test_user, test_workspace):
    other = await _other_workspace(session, test_user.id)
    await _account(session, test_user, other, "Far away", position=40)

    response = await client.post("/api/accounts", headers=auth_headers,
                                 json={"name": "Here", "type": "checking"})
    assert response.status_code == 201, response.text
    assert response.json()["position"] == 0


# ----- Reordering ---------------------------------------------------------------


async def test_reordering_every_account(client, auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1)
    c = await _account(session, test_user, test_workspace, "Charlie", position=2)

    assert await _put(client, auth_headers, [c.id, a.id, b.id]) == 204
    assert await _listed(client, auth_headers) == ["Charlie", "Alpha", "Bravo"]
    assert await _positions(session, test_workspace) == {"Charlie": 0, "Alpha": 1, "Bravo": 2}


async def test_a_partial_list_swaps_only_among_its_own_places(client, auth_headers, session, test_user, test_workspace):
    # Two groups interleaved: manual (A, C, E) and another group (B, D).
    accounts = {
        name: await _account(session, test_user, test_workspace, name, position=i)
        for i, name in enumerate(["A", "B", "C", "D", "E"])
    }

    assert await _put(client, auth_headers, [accounts["E"].id, accounts["A"].id, accounts["C"].id]) == 204

    # E, A, C took the places A, C, E held; B and D never moved.
    assert await _listed(client, auth_headers) == ["E", "B", "A", "D", "C"]


async def test_tied_positions_are_separated_before_reordering(client, auth_headers, session, test_user, test_workspace):
    # Accounts that never had a place (all 0) list alphabetically; moving two
    # of them must not depend on the tie.
    accounts = {name: await _account(session, test_user, test_workspace, name) for name in ["A", "B", "C"]}

    assert await _put(client, auth_headers, [accounts["C"].id, accounts["B"].id]) == 204

    assert await _listed(client, auth_headers) == ["A", "C", "B"]
    assert await _positions(session, test_workspace) == {"A": 0, "C": 1, "B": 2}


async def test_closed_accounts_can_be_reordered(client, auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0, is_closed=True)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1, is_closed=True)

    assert await _put(client, auth_headers, [b.id, a.id]) == 204
    assert await _listed(client, auth_headers, include_closed=True) == ["Bravo", "Alpha"]


async def test_a_connected_account_reorders_like_a_manual_one(client, auth_headers, session, test_user, test_workspace, test_account):
    manual = await _account(session, test_user, test_workspace, "Wallet", position=5)

    assert await _put(client, auth_headers, [manual.id, test_account.id]) == 204
    assert await _listed(client, auth_headers) == ["Wallet", test_account.name]


async def test_duplicates_are_rejected_and_nothing_moves(client, auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1)

    assert await _put(client, auth_headers, [b.id, a.id, b.id]) == 400
    assert await _positions(session, test_workspace) == {"Alpha": 0, "Bravo": 1}


async def test_an_unknown_account_is_rejected_and_nothing_moves(client, auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1)

    assert await _put(client, auth_headers, [b.id, uuid.uuid4(), a.id]) == 404
    assert await _positions(session, test_workspace) == {"Alpha": 0, "Bravo": 1}


async def test_another_workspaces_account_is_not_found_and_not_moved(client, auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1)
    other = await _other_workspace(session, test_user.id)
    foreign = await _account(session, test_user, other, "Foreign", position=7)

    assert await _put(client, auth_headers, [b.id, foreign.id, a.id]) == 404
    assert await _positions(session, test_workspace) == {"Alpha": 0, "Bravo": 1}
    assert await _positions(session, other) == {"Foreign": 7}


async def test_reordering_one_workspace_leaves_another_alone(client, auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1)
    other = await _other_workspace(session, test_user.id)
    await _account(session, test_user, other, "Yonder", position=0)
    await _account(session, test_user, other, "Xray", position=1)

    assert await _put(client, auth_headers, [b.id, a.id]) == 204
    assert await _positions(session, other) == {"Yonder": 0, "Xray": 1}


@pytest.mark.parametrize("body", [{"account_ids": []}, {"account_ids": ["not-a-uuid"]}, {}])
async def test_a_malformed_body_is_422(client, auth_headers, body):
    response = await client.put("/api/accounts/order", headers=auth_headers, json=body)
    assert response.status_code == 422


async def test_a_viewer_cannot_reorder(client, viewer_auth_headers, session, test_user, test_workspace):
    a = await _account(session, test_user, test_workspace, "Alpha", position=0)
    b = await _account(session, test_user, test_workspace, "Bravo", position=1)

    assert await _put(client, viewer_auth_headers, [b.id, a.id]) == 403
    assert await _positions(session, test_workspace) == {"Alpha": 0, "Bravo": 1}


# ----- Bank sync ----------------------------------------------------------------


async def test_synced_accounts_go_after_existing_ones_and_sync_keeps_the_order(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Wallet", position=0)
    conn = await akahu_connection(session, test_user, test_workspace)
    await run_sync(session, conn, test_workspace, test_user, bank_accounts())

    rows = await accounts_by_external_id(session, conn)
    assert sorted(a.position for a in rows.values()) == [1, 2, 3, 4]

    # The user puts the card first and the cheque account last.
    wanted = ["acc_card", "acc_visa", "acc_sav", "acc_chq"]
    await reorder_accounts(session, test_workspace.id, [rows[e].id for e in wanted])
    await session.commit()

    # The bank renames an account, and reports a new one.
    renamed = [
        {**raw, "name": "Everyday Plus"} if raw["_id"] == "acc_chq" else raw
        for raw in bank_accounts()
    ] + [_raw("acc_new", "Holiday Fund", "SAVINGS", BANK_A, 50.0)]
    await run_sync(session, conn, test_workspace, test_user, renamed)

    listed = [
        a["external_id"] or a["name"]
        for a in await account_service.get_accounts(session, test_workspace.id)
    ]
    assert listed == ["Wallet", *wanted, "acc_new"]


async def test_a_new_connection_appends_its_accounts(session, test_user, test_workspace):
    await _account(session, test_user, test_workspace, "Zulu", position=3)
    provider = AsyncMock()
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-order", institution_name="Example Bank", credentials={"token": "x"},
        accounts=[
            AccountData(external_id="first", name="Alpha", type="checking",
                        balance=Decimal("1"), currency="BRL"),
            AccountData(external_id="second", name="Bravo", type="savings",
                        balance=Decimal("2"), currency="BRL"),
        ],
    ))
    provider.get_transactions = AsyncMock(return_value=[])
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        connection = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "code", "pluggy",
        )
    assert connection is not None

    listed = [a["name"] for a in await account_service.get_accounts(session, test_workspace.id)]
    assert listed == ["Zulu", "Alpha", "Bravo"]
    assert await _positions(session, test_workspace) == {"Zulu": 3, "Alpha": 4, "Bravo": 5}


# ----- Display only ---------------------------------------------------------------


async def test_reordering_changes_no_balance_net_worth_or_pnl(session, test_user, test_workspace):
    await nzd_user(session, test_user)
    conn = await akahu_connection(session, test_user, test_workspace)
    await sync_salaried(session, conn, test_workspace, test_user, AkahuProvider())

    def balances(listed: list[dict]) -> dict:
        return {a["id"]: (a["current_balance"], a["previous_balance"]) for a in listed}

    before_list = await account_service.get_accounts(session, test_workspace.id, include_closed=True)
    before = await money_view(session, test_workspace, test_user)
    assert before["income"] > 0

    reversed_ids = [a["id"] for a in reversed(before_list)]
    await reorder_accounts(session, test_workspace.id, reversed_ids)
    await session.commit()

    after_list = await account_service.get_accounts(session, test_workspace.id, include_closed=True)
    assert [a["id"] for a in after_list] == reversed_ids
    assert balances(after_list) == balances(before_list)
    assert await money_view(session, test_workspace, test_user) == before
