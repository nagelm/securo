"""The user's own wallet order: PUT /api/asset-groups/order (fork).

Every name here is made up.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_group import AssetGroup

pytestmark = pytest.mark.asyncio


async def _wallet(session: AsyncSession, user, workspace, name: str, position: int, source: str = "manual"):
    group = AssetGroup(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name,
        position=position, source=source,
    )
    session.add(group)
    await session.commit()
    return group


async def _put(client: AsyncClient, headers: dict, ids: list) -> int:
    response = await client.put(
        "/api/asset-groups/order", headers=headers, json={"group_ids": [str(i) for i in ids]},
    )
    return response.status_code


async def _order(session: AsyncSession, workspace) -> list[str]:
    rows = await session.execute(
        select(AssetGroup).where(AssetGroup.workspace_id == workspace.id)
        .order_by(AssetGroup.position, AssetGroup.name)
        .execution_options(populate_existing=True)
    )
    return [g.name for g in rows.scalars().all()]


async def test_wallets_take_the_new_order(client, auth_headers, session, test_user, test_workspace):
    a = await _wallet(session, test_user, test_workspace, "Alpha", 0)
    b = await _wallet(session, test_user, test_workspace, "Bravo", 1)
    c = await _wallet(session, test_user, test_workspace, "Charlie", 2)
    assert await _put(client, auth_headers, [c.id, a.id, b.id]) == 204
    assert await _order(session, test_workspace) == ["Charlie", "Alpha", "Bravo"]
    response = await client.get("/api/asset-groups", headers=auth_headers)
    assert [g["name"] for g in response.json()] == ["Charlie", "Alpha", "Bravo"]


async def test_a_subset_keeps_the_others_in_place(client, auth_headers, session, test_user, test_workspace):
    # Tied positions (every wallet starts at 0 upstream) are separated by name first.
    await _wallet(session, test_user, test_workspace, "Alpha", 0)
    b = await _wallet(session, test_user, test_workspace, "Bravo", 0)
    await _wallet(session, test_user, test_workspace, "Charlie", 0)
    d = await _wallet(session, test_user, test_workspace, "Delta", 0)
    assert await _put(client, auth_headers, [d.id, b.id]) == 204
    assert await _order(session, test_workspace) == ["Alpha", "Delta", "Charlie", "Bravo"]


async def test_duplicates_and_unknown_wallets_are_refused(client, auth_headers, session, test_user, test_workspace):
    a = await _wallet(session, test_user, test_workspace, "Alpha", 0)
    b = await _wallet(session, test_user, test_workspace, "Bravo", 1)
    assert await _put(client, auth_headers, [a.id, a.id]) == 400
    assert await _put(client, auth_headers, [b.id, uuid.uuid4()]) == 404
    assert await _order(session, test_workspace) == ["Alpha", "Bravo"]


@pytest.mark.parametrize("body", [{"group_ids": []}, {"group_ids": ["not-a-uuid"]}, {}])
async def test_a_malformed_body_is_422(client, auth_headers, body):
    response = await client.put("/api/asset-groups/order", headers=auth_headers, json=body)
    assert response.status_code == 422


async def test_a_viewer_cannot_reorder(client, viewer_auth_headers, session, test_user, test_workspace):
    a = await _wallet(session, test_user, test_workspace, "Alpha", 0)
    b = await _wallet(session, test_user, test_workspace, "Bravo", 1)
    assert await _put(client, viewer_auth_headers, [b.id, a.id]) == 403
    assert await _order(session, test_workspace) == ["Alpha", "Bravo"]
