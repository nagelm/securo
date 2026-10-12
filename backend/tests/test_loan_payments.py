"""Loan payments API: read, edit, the loan summary, CSV and backup.

The P&L invariant itself is proved in `test_loan_pnl_invariant.py`. Every
amount here is made up.
"""

import csv
import io
import json
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.services.dashboard_service import get_summary
from tests.test_loan_accounts import _balance, _tx
from tests.test_loan_pnl_invariant import INTEREST, _category, _fresh, _pay, book  # noqa: F401

pytestmark = pytest.mark.asyncio


def _payload(book, **overrides):  # noqa: F811
    body = {
        "loan_account_id": str(book.loan.id),
        "cash_account_id": str(book.checking.id),
        "date": app_today().isoformat(),
        "total_amount": "2000.00",
        "interest_amount": "1200.00",
        "interest_category_id": str(book.interest.id),
    }
    body.update(overrides)
    return body


async def test_create_and_read_derive_amounts(client: AsyncClient, auth_headers, book):  # noqa: F811
    created = await client.post("/api/loan-payments", json=_payload(book, description="March"), headers=auth_headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert Decimal(body["total"]) == Decimal("2000")
    assert Decimal(body["interest"]) == Decimal("1200")
    assert Decimal(body["principal"]) == Decimal("800")
    assert body["cash_account_id"] == str(book.checking.id)
    assert body["interest_category_id"] == str(book.interest.id)
    assert body["description"] == "March"
    assert body["cash_leg_created"] is True

    listed = await client.get(
        "/api/loan-payments", params={"loan_account_id": str(book.loan.id)}, headers=auth_headers
    )
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [body["id"]]

    one = await client.get(f"/api/loan-payments/{body['id']}", headers=auth_headers)
    assert one.status_code == 200 and one.json()["id"] == body["id"]
    missing = await client.get(f"/api/loan-payments/{uuid.uuid4()}", headers=auth_headers)
    assert missing.status_code == 404


async def test_edit_moves_both_legs_and_the_interest_row(client: AsyncClient, auth_headers, session, book):  # noqa: F811
    created = (await client.post("/api/loan-payments", json=_payload(book), headers=auth_headers)).json()

    edited = await client.patch(
        f"/api/loan-payments/{created['id']}",
        json={"total_amount": "2500.00", "interest_amount": "1000.00"},
        headers=auth_headers,
    )
    assert edited.status_code == 200, edited.text
    assert Decimal(edited.json()["principal"]) == Decimal("1500")
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-300000 + 2500 - 1000)
    assert await _balance(session, book.workspace, book.checking.id) == pytest.approx(7500.0)
    summary = await get_summary(session, book.workspace.id, book.user.id)
    assert summary.monthly_expenses == pytest.approx(1000.0)

    # Interest to zero deletes the interest row; back up re-creates it.
    no_interest = await client.patch(
        f"/api/loan-payments/{created['id']}", json={"interest_amount": "0"}, headers=auth_headers
    )
    assert no_interest.status_code == 200, no_interest.text
    assert no_interest.json()["interest_transaction_id"] is None
    assert await _fresh(session, Transaction, uuid.UUID(created["interest_transaction_id"])) is None
    summary = await get_summary(session, book.workspace.id, book.user.id)
    assert summary.monthly_expenses == pytest.approx(0.0)

    again = await client.patch(
        f"/api/loan-payments/{created['id']}",
        json={"interest_amount": "300", "interest_category_id": str(book.interest.id)},
        headers=auth_headers,
    )
    assert again.status_code == 200, again.text
    assert Decimal(again.json()["interest"]) == Decimal("300")
    assert again.json()["interest_transaction_id"] is not None

    moved = app_today().replace(day=1)
    dated = await client.patch(
        f"/api/loan-payments/{created['id']}", json={"date": moved.isoformat()}, headers=auth_headers
    )
    assert dated.status_code == 200, dated.text
    rows = (
        await session.execute(
            select(Transaction.date).where(
                Transaction.id.in_([
                    uuid.UUID(created["cash_transaction_id"]),
                    uuid.UUID(created["loan_transaction_id"]),
                    uuid.UUID(again.json()["interest_transaction_id"]),
                ])
            ).execution_options(populate_existing=True)
        )
    ).scalars().all()
    assert set(rows) == {moved}

    bad = await client.patch(
        f"/api/loan-payments/{created['id']}", json={"interest_amount": "9999"}, headers=auth_headers
    )
    assert bad.status_code == 400


async def test_paired_bank_debit_keeps_its_total_and_date(client: AsyncClient, auth_headers, session, book):  # noqa: F811
    bank_row = await _tx(session, book.user, book.checking, "2000", "debit", source="sync")
    created = await client.post(
        "/api/loan-payments",
        json=_payload(book, cash_account_id=None, date=None, existing_transaction_id=str(bank_row.id)),
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    payment_id = created.json()["id"]
    assert created.json()["cash_leg_created"] is False

    for change in ({"total_amount": "2100"}, {"date": (app_today() - timedelta(days=3)).isoformat()}):
        response = await client.patch(f"/api/loan-payments/{payment_id}", json=change, headers=auth_headers)
        assert response.status_code == 400, response.text

    split = await client.patch(
        f"/api/loan-payments/{payment_id}", json={"interest_amount": "900"}, headers=auth_headers
    )
    assert split.status_code == 200, split.text
    assert Decimal(split.json()["principal"]) == Decimal("1100")
    bank = await _fresh(session, Transaction, bank_row.id)
    assert bank is not None and bank.amount == Decimal("2000.00") and bank.date == app_today()


async def test_loan_summary_reads_the_ledger(client: AsyncClient, auth_headers, session, book):  # noqa: F811
    await _pay(session, book)
    fee = await _category(session, book.workspace, book.user, "Hidden fees", is_ignored=True)
    # An interest debit in an ignored category moves the balance but is not P&L.
    await _tx(session, book.user, book.loan, "50", "debit", category_id=fee.id)

    response = await client.get(f"/api/accounts/{book.loan.id}/loan-summary", params={"months": 3}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["manual_payments_allowed"] is True
    assert [m["month"] for m in body["months"]][-1] == app_today().strftime("%Y-%m")
    assert len(body["months"]) == 3
    current = body["months"][-1]
    assert current["paid"] == pytest.approx(2000.0)
    assert current["interest"] == pytest.approx(float(INTEREST))
    assert current["principal_reduction"] == pytest.approx(2000.0 - 1200.0 - 50.0)
    # The opening balance is not a payment or a reduction.
    assert body["total_principal_reduction"] == pytest.approx(750.0)

    not_loan = await client.get(f"/api/accounts/{book.checking.id}/loan-summary", headers=auth_headers)
    assert not_loan.status_code == 400
    missing = await client.get(f"/api/accounts/{uuid.uuid4()}/loan-summary", headers=auth_headers)
    assert missing.status_code == 404


async def test_csv_marks_transfer_legs(client: AsyncClient, auth_headers, session, book):  # noqa: F811
    payment = await _pay(session, book)

    response = await client.get("/api/transactions/export", headers=auth_headers)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    header = rows[0]
    assert header[-1] == "is_transfer"
    assert header[:14] == [
        "date", "description", "amount", "type", "currency", "category", "account", "payee",
        "payee_name", "notes", "status", "source", "amount_primary", "fx_rate_used",
    ]
    flags = {(r[6], r[3], r[2]): r[-1] for r in rows[1:]}
    assert flags[(book.checking.name, "debit", "2000.00")] == "true"
    assert flags[(book.loan.name, "credit", "2000.00")] == "true"
    assert flags[(book.loan.name, "debit", "1200.00")] == "false"
    assert payment.interest_transaction_id is not None


async def test_backup_includes_loan_payments(client: AsyncClient, auth_headers, session, book):  # noqa: F811
    payment = await _pay(session, book)

    response = await client.get("/api/export/backup", headers=auth_headers)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        records = json.loads(zf.read("loan_payments.json"))
        counts = json.loads(zf.read("metadata.json"))["entity_counts"]
    assert counts["loan_payments"] == 1
    assert records[0]["id"] == str(payment.id)
    assert records[0]["cash_transaction_id"] == str(payment.cash_transaction_id)
    # Metadata only: no amounts are stored.
    assert not any("amount" in key or "principal" in key for key in records[0])


async def test_viewer_cannot_record_payments(client: AsyncClient, viewer_auth_headers, book):  # noqa: F811
    response = await client.post("/api/loan-payments", json=_payload(book), headers=viewer_auth_headers)
    assert response.status_code == 403, response.text
