"""Fork: the table-loan projection behind the Forecast page's Loans section.
Pure functions; the textbook figures are the standard amortisation examples,
the rest is made up."""
from datetime import date, timedelta

import pytest

from app.services.loan_projection import (
    LoanChange,
    LoanSchedule,
    LoanTerms,
    cash_difference,
    loan_terms,
    payment_for,
    period_date,
    periods_to_repay,
    project_loan,
)

TODAY = date(2026, 10, 14)


def _payoff(schedule: LoanSchedule) -> date:
    assert schedule.payoff_date is not None
    return schedule.payoff_date


def test_payment_formula_known_examples():
    # $200,000 over 30 years at 6% a year, monthly: $1,199.10 a month.
    assert payment_for(200_000, 6, 12, 360) == pytest.approx(1199.10, abs=0.005)
    # $10,000 over a year at 12%, monthly: $888.49.
    assert payment_for(10_000, 12, 12, 12) == pytest.approx(888.49, abs=0.005)
    # No interest: the principal in equal parts.
    assert payment_for(1200, 0, 12, 12) == pytest.approx(100)


def test_periods_to_repay_inverts_the_payment_formula():
    payment = payment_for(150_000, 5.15, 52, 20 * 52)
    assert periods_to_repay(150_000, 5.15, 52, payment) == 20 * 52
    assert (periods_to_repay(150_000, 5.15, 52, payment + 50) or 0) < 20 * 52
    # A repayment that doesn't cover the interest never clears the loan.
    assert periods_to_repay(150_000, 5.15, 52, 150_000 * 0.0515 / 52) is None
    assert periods_to_repay(1200, 0, 12, 100) == 12


def test_projection_matches_the_textbook_mortgage():
    terms = LoanTerms(debt=200_000, rate=6, frequency="MONTHLY", repayment=1199.10, first_date=date(2026, 11, 1))
    schedule = project_loan(terms)

    first = schedule.periods[0]
    assert first.interest == pytest.approx(1000.00)  # 200,000 × 6% / 12
    assert first.principal == pytest.approx(199.10)
    assert first.closing == pytest.approx(199_800.90)
    # 360 payments; the cents rounding leaves a few cents for a 361st.
    assert len(schedule.periods) in (360, 361)
    assert schedule.total_interest == pytest.approx(231_676.38, abs=2)
    assert schedule.payoff_date == schedule.periods[-1].date
    assert schedule.periods[-1].closing == 0


def test_period_dates_keep_the_day_of_month():
    assert period_date(date(2026, 1, 31), "MONTHLY", 1) == date(2026, 2, 28)
    assert period_date(date(2026, 1, 31), "MONTHLY", 2) == date(2026, 3, 31)
    assert period_date(date(2026, 1, 31), "QUARTERLY", 1) == date(2026, 4, 30)
    assert period_date(date(2026, 10, 14), "FORTNIGHTLY", 2) == date(2026, 11, 11)


def _weekly(debt=100_000.0, rate=5.15, repayment=352.40, **kwargs) -> LoanTerms:
    return LoanTerms(debt=debt, rate=rate, frequency="WEEKLY", repayment=repayment,
                     first_date=TODAY + timedelta(days=7), **kwargs)


def test_paying_more_pays_off_earlier_and_saves_interest():
    terms = _weekly()
    base = project_loan(terms)
    more = project_loan(terms, [LoanChange(start=TODAY, repayment=600)])

    assert _payoff(more) < _payoff(base)
    assert more.total_interest < base.total_interest
    assert all(p.repayment == 600 for p in more.periods[:-1])


def test_rate_change_recalculated_keeps_the_term_to_maturity():
    matures = TODAY + timedelta(days=7 * 520)  # 520 weekly repayments left
    terms = _weekly(matures_at=matures)
    change_on = TODAY + timedelta(days=7 * 10)  # the 10th, so 511 left counting it
    schedule = project_loan(terms, [LoanChange(start=change_on, rate=6.5, recalculate=True)])

    at_change = next(p for p in schedule.periods if p.date >= change_on)
    remaining = sum(1 for p in schedule.periods if p.date >= at_change.date)
    expected = round(payment_for(at_change.opening, 6.5, 52, 511), 2)
    assert at_change.payment == pytest.approx(expected)
    assert at_change.rate == 6.5
    # Cleared on the maturity date (within a cents-rounding period).
    assert abs((_payoff(schedule) - matures).days) <= 7
    assert remaining in (511, 512)


def test_rate_change_recalculated_without_maturity_keeps_the_remaining_term():
    terms = _weekly()
    base = project_loan(terms)
    schedule = project_loan(terms, [LoanChange(start=TODAY, rate=6.5, recalculate=True)])

    assert schedule.periods[0].payment > terms.repayment
    assert abs((_payoff(schedule) - _payoff(base)).days) <= 7
    assert schedule.total_interest > base.total_interest


def test_rate_change_keeping_the_repayment_moves_the_payoff():
    terms = _weekly()
    base = project_loan(terms)
    higher = project_loan(terms, [LoanChange(start=TODAY, rate=6.5, recalculate=False)])
    lower = project_loan(terms, [LoanChange(start=TODAY, rate=4.0, recalculate=False)])

    assert higher.periods[0].payment == terms.repayment
    assert _payoff(higher) > _payoff(base)
    assert _payoff(lower) < _payoff(base)
    assert lower.total_interest < base.total_interest


def test_lump_sum_leaves_cash_on_its_date_and_cuts_the_balance_with_the_next_repayment():
    terms = _weekly()
    base = project_loan(terms)
    on = TODAY + timedelta(days=10)
    schedule = project_loan(terms, [LoanChange(start=on, lump_sum=10_000)])

    assert schedule.lump_sums == [(on, 10_000)]
    applied = next(p for p in schedule.periods if p.date >= on)
    assert applied.extra == 10_000
    assert applied.closing == pytest.approx(applied.opening + applied.interest - applied.repayment - 10_000)
    assert _payoff(schedule) < _payoff(base)
    diff = cash_difference(base, schedule)
    assert diff[on] == -10_000
    # Every repayment after the new payoff is cash kept.
    kept = [d for d, v in diff.items() if v > 0]
    assert kept and min(kept) >= _payoff(schedule)


def test_lump_sum_is_capped_at_what_is_owed():
    terms = _weekly(debt=1_000)
    schedule = project_loan(terms, [LoanChange(start=TODAY, lump_sum=5_000)])

    paid = schedule.periods[0]
    assert schedule.lump_sums[0][1] == pytest.approx(paid.opening + paid.interest - paid.repayment)
    assert schedule.payoff_date == paid.date


def test_a_repayment_below_the_interest_never_pays_off():
    schedule = project_loan(_weekly(repayment=50), max_years=2)

    assert schedule.payoff_date is None
    assert schedule.periods[-1].closing > 100_000


def test_cash_difference_over_a_full_payoff_equals_the_interest_saved():
    terms = _weekly(debt=5_000)
    base = project_loan(terms)
    scenario = project_loan(terms, [LoanChange(start=TODAY + timedelta(days=3), lump_sum=2_000)])

    kept = sum(cash_difference(base, scenario).values())
    assert kept == pytest.approx(base.total_interest - scenario.total_interest, abs=0.01)


def _details(**overrides):
    details = {
        "type": "TABLE",
        "interest": {"rate": 4.5, "type": "FIXED", "expires_at": "2027-02-01"},
        "repayment": {"frequency": "WEEKLY", "next_date": "2026-10-14", "next_amount": 190.00},
        "matures_at": "2045-03-01",
    }
    details.update(overrides)
    return details


def test_loan_terms_from_details():
    terms = loan_terms(_details(), 50_000, TODAY)

    assert terms is not None
    assert (terms.rate, terms.frequency, terms.repayment) == (4.5, "WEEKLY", 190.00)
    assert terms.first_date == date(2026, 10, 21)  # a repayment due today is already in the balance
    assert terms.matures_at == date(2045, 3, 1)


def test_a_stale_next_date_rolls_forward():
    terms = loan_terms(_details(repayment={"frequency": "MONTHLY", "next_date": "2026-01-20", "next_amount": 900}),
                       50_000, TODAY)
    assert terms is not None and terms.first_date == date(2026, 10, 20)


@pytest.mark.parametrize("details", [
    None,
    {"type": "REVOLVING"},
    _details(type="REVOLVING"),
    _details(type="REDUCING"),
    _details(interest={}),
    _details(repayment={"frequency": "WEEKLY"}),
    _details(repayment={"next_amount": 100}),
    _details(repayment={"frequency": "DAILY", "next_amount": 100}),
])
def test_loans_without_enough_details_have_no_terms(details):
    assert loan_terms(details, 50_000, TODAY) is None
