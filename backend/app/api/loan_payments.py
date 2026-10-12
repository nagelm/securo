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
from app.schemas.loan_payment import LoanPaymentCreate, LoanPaymentRead, LoanPaymentUpdate
from app.services import loan_payment_service
from app.services.loan_payment_service import LoanPaymentConflict, LoanPaymentNotFound

router = APIRouter(prefix="/api/loan-payments", tags=["loan-payments"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LoanPaymentNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, LoanPaymentConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=list[LoanPaymentRead])
async def list_loan_payments(
    loan_account_id: Optional[uuid.UUID] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await loan_payment_service.list_payments(
        session, ctx.workspace.id, loan_account_id=loan_account_id
    )


@router.get("/{payment_id}", response_model=LoanPaymentRead)
async def get_loan_payment(
    payment_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await loan_payment_service.get_payment(session, ctx.workspace.id, payment_id)
    except LoanPaymentNotFound as exc:
        raise _http_error(exc)


@router.post("", response_model=LoanPaymentRead, status_code=status.HTTP_201_CREATED)
async def create_loan_payment(
    data: LoanPaymentCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        payment = await loan_payment_service.record_payment(session, ctx.workspace.id, ctx.user_id, data)
    except (ValueError, LoanPaymentNotFound, LoanPaymentConflict) as exc:
        raise _http_error(exc)
    return await loan_payment_service.get_payment(session, ctx.workspace.id, payment.id)


@router.patch("/{payment_id}", response_model=LoanPaymentRead)
async def update_loan_payment(
    payment_id: uuid.UUID,
    data: LoanPaymentUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        await loan_payment_service.update_payment(session, ctx.workspace.id, ctx.user_id, payment_id, data)
    except (ValueError, LoanPaymentNotFound, LoanPaymentConflict) as exc:
        raise _http_error(exc)
    return await loan_payment_service.get_payment(session, ctx.workspace.id, payment_id)


@router.delete("/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_loan_payment(
    payment_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        await loan_payment_service.delete_payment(session, ctx.workspace.id, payment_id)
    except LoanPaymentNotFound as exc:
        raise _http_error(exc)
