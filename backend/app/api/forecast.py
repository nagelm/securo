"""Fork: the Forecast page (projected cash and cards, expected payments,
scenarios). Workspace-scoped like recurring transactions: reads need
membership, writes the write gate."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.forecast import (
    ForecastItemCreate,
    ForecastItemRead,
    ForecastItemUpdate,
    ForecastResponse,
    ForecastScenarioCreate,
    ForecastScenarioRead,
    ForecastScenarioUpdate,
)
from app.services import forecast_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("", response_model=ForecastResponse)
async def get_forecast(
    months: int = Query(12, ge=1, le=60),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    scenario_ids: Optional[list[uuid.UUID]] = Query(None),
    include_spending: bool = Query(True),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.compute_forecast(
        session, ctx.workspace.id, ctx.user_id, months, interval,
        scenario_ids=scenario_ids, include_spending=include_spending,
    )


@router.get("/scenarios", response_model=list[ForecastScenarioRead])
async def list_scenarios(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.list_scenarios(session, ctx.workspace.id)


@router.post("/scenarios", response_model=ForecastScenarioRead, status_code=status.HTTP_201_CREATED)
async def create_scenario(
    data: ForecastScenarioCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.create_scenario(session, ctx.workspace.id, ctx.user_id, data)


@router.patch("/scenarios/{scenario_id}", response_model=ForecastScenarioRead)
async def update_scenario(
    scenario_id: uuid.UUID,
    data: ForecastScenarioUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        scenario = await forecast_service.update_scenario(session, ctx.workspace.id, scenario_id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")
    return scenario


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(
    scenario_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if not await forecast_service.delete_scenario(session, ctx.workspace.id, scenario_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found")


@router.get("/items", response_model=list[ForecastItemRead])
async def list_items(
    scenario_id: Optional[uuid.UUID] = Query(None),
    expected: bool = Query(False, description="Only expected payments (items with no scenario)"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.list_items(
        session, ctx.workspace.id, scenario_id=scenario_id, expected_only=expected
    )


@router.post("/items", response_model=ForecastItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    data: ForecastItemCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await forecast_service.create_item(session, ctx.workspace.id, ctx.user_id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/items/{item_id}", response_model=ForecastItemRead)
async def update_item(
    item_id: uuid.UUID,
    data: ForecastItemUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        item = await forecast_service.update_item(session, ctx.workspace.id, item_id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forecast item not found")
    return item


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if not await forecast_service.delete_item(session, ctx.workspace.id, item_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forecast item not found")
