"""Fork: the Insights page, a monthly briefing of patterns in the payment data.
Read-only and workspace-scoped, like the reports."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.models.user import User
from app.schemas.insights import InsightsBriefing
from app.services import insights_service

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/briefing", response_model=InsightsBriefing)
async def get_briefing(
    month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; defaults to last month"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    when = None
    if month:
        try:
            when = date.fromisoformat(f"{month}-01")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid month")
    user = await session.get(User, ctx.user_id)
    currency = user.primary_currency if user else get_settings().default_currency
    return await insights_service.get_briefing(session, ctx.workspace.id, currency, when)
