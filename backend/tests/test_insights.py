"""Fork: the Insights page's monthly briefing. Every figure is made up."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.services.insights_service import Row, analyse, default_month

MONTH = date(2026, 9, 1)


def spend(when: date, amount: float, category: str = "Food", payee: str | None = None, account: str = "acc") -> Row:
    return Row(
        when=when, amount=amount, is_debit=True, category_id=category, category=category,
        payee_id=payee, payee=payee, account_id=account,
    )


def pay(when: date, amount: float, payee: str = "Employer") -> Row:
    return Row(
        when=when, amount=amount, is_debit=False, category_id="Salary", category="Salary",
        payee_id=payee, payee=payee, account_id="acc",
    )


def months_back(n: int) -> list[date]:
    """The first day of each of the n months up to and including MONTH."""
    out = []
    y, m = MONTH.year, MONTH.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def steady_income(amount: float = 5000) -> list[Row]:
    return [pay(d.replace(day=15), amount) for d in months_back(24)]


def test_default_month_is_last_complete_month():
    assert default_month(date(2026, 10, 15)) == date(2026, 9, 1)
    assert default_month(date(2026, 1, 3)) == date(2025, 12, 1)


def test_category_over_its_usual_month_is_worse():
    rows = steady_income()
    for d in months_back(24)[:-1]:
        rows.append(spend(d.replace(day=10), 200, "Eating out"))
    rows += [spend(MONTH.replace(day=d), 100, "Eating out") for d in (3, 9, 17, 24, 28)]
    b = analyse(rows, MONTH)
    row = next(c for c in b["categories"] if c["name"] == "Eating out")
    assert (row["amount"], row["usual"], row["diff"]) == (500, 200, 300)
    card = next(c for c in b["worse"] if c["kind"] == "category_month")
    assert card["params"]["category"] == "Eating out"
    assert card["params"]["visits"] == 5
    assert card["link_from"] == "2026-09-01" and card["link_to"] == "2026-09-30"
    assert len(card["series"]) == 12


def test_small_moves_make_no_card():
    rows = steady_income()
    for d in months_back(24)[:-1]:
        rows.append(spend(d.replace(day=10), 200, "Groceries"))
    rows.append(spend(MONTH.replace(day=10), 230, "Groceries"))
    b = analyse(rows, MONTH)
    assert not [c for c in b["better"] + b["worse"] if c["kind"] == "category_month"]


def test_one_off_purchases_stay_out_of_everyday_spending():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=10), 100, "Groceries"))
    rows.append(spend(MONTH.replace(day=5), 25000, "Car"))
    b = analyse(rows, MONTH)
    assert b["everyday_spend"] == 100
    assert b["one_off_count"] == 1 and b["one_off_total"] == 25000
    assert {"category_id": "Car", "name": "Car", "reason": "one_off"} in b["excluded"]
    assert all(c["name"] != "Car" for c in b["categories"])


def test_an_extra_fortnightly_payment_is_not_a_change():
    # A loan charged every 14 days: most months hold two charges, some three.
    rows = steady_income()
    d = date(2026, 9, 29)  # September holds three: the 1st, 15th and 29th
    while d >= date(2024, 10, 1):
        rows.append(spend(d, 300, "Interest", payee="Bank", account="loan"))
        d -= timedelta(days=14)
    b = analyse(rows, MONTH)
    assert {"category_id": "Interest", "name": "Interest", "reason": "more_payments"} in b["excluded"]
    assert all(c["name"] != "Interest" for c in b["categories"])


def test_savings_rate_slide_leads_the_worse_column():
    rows = steady_income()
    for i, d in enumerate(months_back(24)):
        # Spending grows from 1,200 to 2,580 a month over two years, in
        # everyday-sized shops.
        rows += [spend(d.replace(day=day), 300 + 15 * i, "Groceries") for day in (5, 12, 19, 26)]
    b = analyse(rows, MONTH)
    first = b["worse"][0]
    assert first["kind"] == "savings_rate"
    assert first["params"]["rate"] < first["params"]["before"] - 8
    assert b["savings_rate"] == first["params"]["rate"]


def test_price_step_on_a_regular_payment():
    rows = steady_income()
    for d in months_back(24):
        price = 25.0 if d < date(2026, 8, 1) else 29.0
        rows.append(spend(d.replace(day=6), price, "Subscriptions", payee="Stream"))
    b = analyse(rows, MONTH)
    card = next(c for c in b["worse"] if c["kind"] == "price_up")
    assert card["params"]["payee"] == "Stream"
    assert (card["params"]["before"], card["params"]["after"]) == (25.0, 29.0)


def test_a_single_odd_charge_is_not_a_price_step():
    rows = steady_income()
    for d in months_back(24):
        price = 25.0 if d < MONTH else 12.0
        rows.append(spend(d.replace(day=6), price, "Subscriptions", payee="Stream"))
    b = analyse(rows, MONTH)
    assert not [c for c in b["better"] + b["worse"] if c["kind"].startswith("price_")]


def test_new_payee_becoming_a_real_cost():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=6), 40, "Software", payee="OldTool"))
    rows += [spend(MONTH.replace(day=day), 80, "Software", payee="NewTool") for day in (2, 9, 16)]
    b = analyse(rows, MONTH)
    card = next(c for c in b["worse"] if c["kind"] in ("payee_new", "category_month"))
    # The category card and the payee card tell the same story: one survives.
    kinds = [c["kind"] for c in b["worse"] if c.get("category_id") == "Software"]
    assert len(kinds) == 1
    assert card["category_id"] == "Software"


def test_burst_of_small_charges():
    rows = steady_income()
    for d in months_back(24)[:-1]:
        rows.append(spend(d.replace(day=6), 5, "Games", payee="GameCo"))
    rows += [spend(MONTH.replace(day=1 + i), 9.99, "Games", payee="GameCo") for i in range(15)]
    b = analyse(rows, MONTH)
    card = next(c for c in b["worse"] if c["payee_id"] == "GameCo")
    assert card["kind"] == "small_burst"
    assert card["params"]["count"] == 15


def test_regular_payment_that_stopped():
    rows = steady_income()
    for d in months_back(24):
        if d < date(2026, 7, 1):
            rows.append(spend(d.replace(day=6), 35, "Software", payee="Editor"))
    b = analyse(rows, MONTH)
    card = next(c for c in b["better"] if c["kind"] == "payee_stopped")
    assert card["params"]["payee"] == "Editor"
    assert card["params"]["last"] == "2026-06-06"


def test_holiday_pause_is_not_a_stop():
    rows = steady_income()
    d = date(2024, 10, 1)
    while d <= date(2026, 8, 25):
        rows.append(spend(d, 120, "Groceries", payee="MealKit"))
        d += timedelta(days=7)
    b = analyse(rows, MONTH)
    assert not [c for c in b["better"] if c["kind"] == "payee_stopped"]


def test_twelve_month_total_past_its_peak():
    rows = steady_income()
    for d in months_back(24):
        amount = 400 if date(2025, 3, 1) <= d <= date(2025, 10, 1) else 100
        rows.append(spend(d.replace(day=6), amount, "Takeaways"))
    b = analyse(rows, MONTH)
    card = next(c for c in b["better"] if c["category_id"] == "Takeaways")
    assert card["kind"] == "trend_cooling"
    assert card["params"]["total"] < 0.8 * card["params"]["peak"]


def test_income_change_from_a_regular_payer():
    rows = []
    d = date(2024, 10, 2)
    while d <= date(2026, 9, 30):
        rows.append(pay(d, 2000 if d < date(2026, 8, 1) else 1700, payee="Employer"))
        d += timedelta(days=14)
    b = analyse(rows, MONTH)
    card = next(c for c in b["worse"] if c["kind"] == "income_change")
    assert card["params"]["before"] == 2000 and card["params"]["after"] == 1700


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_api(client, session: AsyncSession, test_user, auth_headers):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Everyday", type="checking",
        balance=Decimal("0"), currency="BRL", is_closed=False,
    )
    food = Category(id=uuid.uuid4(), user_id=test_user.id, name="Food", icon="tag", color="#123456")
    session.add_all([account, food])
    await session.flush()
    for d in months_back(8):
        for amount in (100, 50):
            session.add(Transaction(
                id=uuid.uuid4(), user_id=test_user.id, account_id=account.id, category_id=food.id,
                description="food", amount=Decimal(str(amount)), currency="BRL", date=d.replace(day=10),
                type="debit", source="manual", status="posted", created_at=datetime.now(timezone.utc),
            ))
    await session.commit()

    response = await client.get("/api/insights/briefing?month=2026-09", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["month"] == "2026-09"
    assert body["everyday_spend"] == 150
    assert body["categories"][0]["name"] == "Food"

    assert (await client.get("/api/insights/briefing?month=2026-13", headers=auth_headers)).status_code == 400
    assert (await client.get("/api/insights/briefing?month=Sept", headers=auth_headers)).status_code == 422


def test_usual_starts_at_a_step_change():
    # A subscription that started halfway through the six months: the usual month
    # is the new level, not a median that mixes in the months before it.
    rows = steady_income()
    for d in months_back(24)[:-1]:
        rows.append(spend(d.replace(day=4), 40, "Software", payee="OldTool"))
        if d >= date(2026, 6, 1):
            rows.append(spend(d.replace(day=17), 400, "Software", payee="BigSub"))
    rows.append(spend(MONTH.replace(day=4), 40, "Software", payee="OldTool"))
    rows.append(spend(MONTH.replace(day=17), 400, "Software", payee="BigSub"))
    b = analyse(rows, MONTH)
    row = next(c for c in b["categories"] if c["name"] == "Software")
    assert row["usual_since"] == "2026-06"  # a plain median of the six would be 240
    assert (row["usual"], row["diff"]) == (440, 0)


def test_no_step_for_a_category_that_merely_varies():
    rows = steady_income()
    for i, d in enumerate(months_back(24)):
        rows.append(spend(d.replace(day=9), 150 if i % 2 else 250, "Food"))
    b = analyse(rows, MONTH)
    assert next(c for c in b["categories"] if c["name"] == "Food")["usual_since"] is None


def test_drivers_name_the_payee_behind_a_change():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=4), 300, "Software", payee="Steady"))
    rows.append(spend(MONTH.replace(day=8), 250, "Software", payee="NewAI"))
    b = analyse(rows, MONTH)
    row = next(c for c in b["categories"] if c["name"] == "Software")
    assert row["drivers"] == [{"payee_id": "NewAI", "payee": "NewAI", "diff": 250}]
    card = next(c for c in b["worse"] if c["category_id"] == "Software")
    assert card["kind"] in ("category_month", "payee_new")


def test_no_driver_for_a_small_change():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=4), 300, "Software", payee="Steady"))
    rows.append(spend(MONTH.replace(day=8), 30, "Software", payee="Tiny"))
    b = analyse(rows, MONTH)
    assert next(c for c in b["categories"] if c["name"] == "Software")["drivers"] == []


def test_project_categories_stay_out_of_everyday_spending():
    # A renovation: mostly big invoices, plus hardware-store trips that are
    # part of the project, not daily life.
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=10), 500, "Groceries"))
        rows.append(spend(d.replace(day=12), 4000, "Renovations"))
        rows.append(spend(d.replace(day=13), 300, "Renovations", payee="Hardware"))
    b = analyse(rows, MONTH)
    assert b["everyday_spend"] == 500


def test_a_sporadic_category_needs_a_bigger_move():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=10), 500, "Groceries"))
    # Car: something in only three of the last twelve months.
    for d in (date(2025, 12, 5), date(2026, 3, 5)):
        rows.append(spend(d, 100, "Car"))
    rows.append(spend(MONTH.replace(day=5), 250, "Car"))
    b = analyse(rows, MONTH)
    assert not [c for c in b["worse"] if c["category_id"] == "Car"]


def test_trends_only_for_habits():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=10), 500, "Groceries"))
    # Pets: two big months a year ago and almost nothing since.
    rows += [spend(date(2025, 3, 3), 900, "Pets"), spend(date(2025, 4, 3), 800, "Pets"), spend(date(2026, 8, 3), 60, "Pets")]
    b = analyse(rows, MONTH)
    assert not [c for c in b["better"] + b["worse"] if c["kind"].startswith("trend_") and c["category_id"] == "Pets"]


def test_leave_is_recognised_and_told_once():
    rows = []
    d = date(2024, 10, 2)
    while d <= date(2026, 9, 30):
        on_leave = d >= date(2026, 4, 1)
        rows.append(pay(d, 600 if on_leave else 2500, payee="Employer"))
        if on_leave:
            rows.append(pay(d + timedelta(days=1), 800, payee="IRD"))
        d += timedelta(days=14)
    for m in months_back(24):
        rows += [spend(m.replace(day=day), 500, "Groceries") for day in (3, 8, 13, 18, 23, 28)]
    b = analyse(rows, MONTH)
    card = b["worse"][0]
    assert card["kind"] == "savings_rate"
    assert card["params"]["leave"] is True and card["params"]["leave_payer"] == "IRD"
    assert card["params"]["income_movers"][0]["name"] == "Employer"
    # No separate, alarming "Employer paying 76% less" card.
    assert not [c for c in b["worse"] if c["kind"] == "income_change"]


def test_a_split_is_one_card_with_the_combined_cost():
    # Rates of 110.00 a week until water moves to its own company: rates
    # 70.00 plus water 50.00 a week, together 9% more.
    rows = steady_income()
    d = date(2024, 10, 1)
    while d <= date(2026, 9, 30):
        rows.append(spend(d, 110.00 if d < date(2026, 8, 5) else 70.00, "Rates", payee="Council"))
        if d >= date(2026, 8, 20):
            rows.append(spend(d + timedelta(days=1), 50.00, "Rates", payee="Water"))
        d += timedelta(days=7)
    b = analyse(rows, MONTH)
    cards = b["better"] + b["worse"]
    switch = next(c for c in cards if c["kind"] == "payee_switch")
    assert (switch["params"]["from_payee"], switch["params"]["to_payee"]) == ("Council", "Water")
    assert switch["direction"] == "worse" and switch["params"]["change_pct"] == 9
    assert not [c for c in cards if c["kind"] in ("price_down", "payee_new")]


def test_a_habit_that_ended():
    rows = steady_income()
    # Bus card top-ups on no fixed day: a habit, but never a "regular payment".
    for i, d in enumerate(months_back(24)):
        rows.append(spend(d.replace(day=10), 500, "Groceries"))
        if d <= date(2026, 6, 1):
            rows.append(spend(d.replace(day=(3, 22, 9, 28, 14, 5)[i % 6]), 40 + d.month, "Transport", payee="BusCard"))
    b = analyse(rows, MONTH)
    card = next(c for c in b["better"] if c["kind"] == "habit_ended")
    assert card["params"]["payee"] == "BusCard"


def test_tax_on_interest_is_never_a_burst():
    rows = steady_income()
    for i in range(15):
        rows.append(spend(MONTH.replace(day=1 + i), 1.5, "Taxes", payee="IRD"))
    b = analyse(rows, MONTH)
    assert not [c for c in b["worse"] if c["kind"] == "small_burst"]


def test_a_yearly_price_review_is_labelled():
    rows = steady_income()
    for d in months_back(24):
        price = 20.0 if d < date(2025, 8, 1) else 22.0 if d < date(2026, 8, 1) else 24.0
        rows.append(spend(d.replace(day=6), price, "Subscriptions", payee="Stream"))
    b = analyse(rows, MONTH)
    card = next(c for c in b["worse"] if c["kind"] == "price_up")
    assert card["params"]["yearly"] is True


def test_drivers_fall_back_to_the_bank_description():
    rows = steady_income()
    for d in months_back(24):
        rows.append(spend(d.replace(day=4), 60, "Leisure", payee="Cinema"))
    rows.append(Row(
        when=MONTH.replace(day=12), amount=300, is_debit=True, category_id="Leisure", category="Leisure",
        payee_id=None, payee=None, account_id="acc", description="Tickets*example Grey Lynn 0099",
    ))
    b = analyse(rows, MONTH)
    row = next(c for c in b["categories"] if c["name"] == "Leisure")
    assert row["drivers"][0]["payee"] == "Tickets example Grey"
