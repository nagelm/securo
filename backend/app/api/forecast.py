"""Forecasts. Upstream: detected recurring streams and a day-by-day cash
flow simulation (`/recurring`, `/cashflow`). Fork: the Forecast page
(projected cash and cards, expected payments, scenarios), workspace-scoped
like recurring transactions: reads need membership, writes the write gate."""
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
    CashflowForecastResponse,
    ForecastBudgetChoices,
    ForecastItemCreate,
    ForecastItemRead,
    ForecastItemUpdate,
    ForecastResponse,
    ForecastScenarioCreate,
    ForecastScenarioRead,
    ForecastScenarioUpdate,
    RecurringDetectionResponse,
)
from app.services import cashflow_forecast_service, forecast_service, recurring_detector_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/recurring", response_model=RecurringDetectionResponse)
async def get_detected_recurring_transactions(
    min_occurrences: int = Query(2, ge=2, le=20, description="Minimum occurrences to classify as recurring"),
    lookback_days: int = Query(365, ge=30, le=730, description="Number of days to analyze historical stream"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Detect recurring expenses, subscriptions, and periodic income streams using interval clustering."""
    return await recurring_detector_service.detect_recurring_patterns(
        session=session,
        workspace_id=ctx.workspace.id,
        min_occurrences=min_occurrences,
        lookback_days=lookback_days,
    )


@router.get("/cashflow", response_model=CashflowForecastResponse)
async def get_cashflow_forecast(
    horizon_days: int = Query(90, ge=7, le=365, description="Forecast horizon in days (e.g. 30, 60, 90)"),
    include_discretionary: bool = Query(True, description="Include 90-day daily discretionary baseline burn"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Generate day-by-day predictive cashflow simulation and calculate liquidity runway."""
    return await cashflow_forecast_service.generate_cashflow_forecast(
        session=session,
        workspace_id=ctx.workspace.id,
        horizon_days=horizon_days,
        include_discretionary_burn=include_discretionary,
    )


@router.get("", response_model=ForecastResponse)
async def get_forecast(
    months: int = Query(12, ge=1, le=60),
    interval: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    scenario_ids: Optional[list[uuid.UUID]] = Query(None),
    include_spending: bool = Query(True),
    # Fork: an account view; by default all cash and card accounts.
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await forecast_service.compute_forecast(
        session, ctx.workspace.id, ctx.user_id, months, interval,
        scenario_ids=scenario_ids, include_spending=include_spending, account_ids=account_ids,
    )


@router.get("/budget-choices", response_model=ForecastBudgetChoices)
async def get_budget_choices(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Fork: the budgets the forecast applies (none by default)."""
    return await forecast_service.get_budget_choices(session, ctx.workspace.id)


@router.put("/budget-choices", response_model=ForecastBudgetChoices)
async def set_budget_choices(
    data: ForecastBudgetChoices,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await forecast_service.set_budget_choices(session, ctx.workspace.id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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
