"""A loan account's ``loan_details``: the user-entered side and the sync merge.

``Account.loan_details`` has one shape whether the provider or the user wrote
it: the keys Akahu documents for ``meta.loan_details``
(https://developers.akahu.nz/reference/get_accounts, mirrored by
``app.providers.akahu._LOAN_DETAIL_KEYS``)::

    purpose                   HOME | PERSONAL | BUSINESS | UNKNOWN
    type                      TABLE | REDUCING | REVOLVING | UNKNOWN
    interest.rate             percent per year, e.g. 5.25
    interest.type             FIXED | FLOATING
    interest.expires_at       date the rate ends
    is_interest_only          bool
    interest_only_expires_at  date
    term.years, term.months   whole numbers
    matures_at                date
    initial_principal         amount first borrowed
    repayment.frequency       WEEKLY | FORTNIGHTLY | MONTHLY | QUARTERLY | BIANNUALLY | ANNUALLY
    repayment.next_date       date
    repayment.next_amount     amount

Display only: nothing that totals money (balances, P&L, budgets, projections)
reads it.

A loan whose ``type`` is ``REVOLVING`` (``is_revolving``) may also carry a
credit limit in ``Account.credit_limit``, the column a credit card uses; it is
display only too (``account_service``).

Sync rule (``merge_provider_details``): the keys a provider actually sends
replace the stored ones, one leaf at a time inside ``interest``, ``term`` and
``repayment``; every key the provider does not send keeps its stored value,
whoever entered it. A sync without details changes nothing.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Optional

PURPOSES = ("HOME", "PERSONAL", "BUSINESS", "UNKNOWN")
LOAN_TYPES = ("TABLE", "REDUCING", "REVOLVING", "UNKNOWN")
INTEREST_TYPES = ("FIXED", "FLOATING")
REPAYMENT_FREQUENCIES = ("WEEKLY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY", "BIANNUALLY", "ANNUALLY")

_MAX_AMOUNT = Decimal("1000000000000")

Normaliser = Callable[[str, Any], Any]


def _enum(*allowed: str) -> Normaliser:
    def check(path: str, value: Any) -> str:
        if not isinstance(value, str) or value.upper() not in allowed:
            raise ValueError(f"loan_details.{path} must be one of {', '.join(allowed)}")
        return value.upper()

    return check


def _number(low: Decimal, high: Decimal, places: int, whole: bool = False) -> Normaliser:
    def check(path: str, value: Any) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"loan_details.{path} must be a number")
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise ValueError(f"loan_details.{path} must be a number") from None
        if not number.is_finite() or not low <= number <= high:
            raise ValueError(f"loan_details.{path} must be between {low} and {high}")
        if whole and number != number.to_integral_value():
            raise ValueError(f"loan_details.{path} must be a whole number")
        number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return int(number) if number == number.to_integral_value() else float(number)

    return check


def _date(path: str, value: Any) -> str:
    """An ISO date, or the date part of an ISO date-time; stored as YYYY-MM-DD."""
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"loan_details.{path} must be a date (YYYY-MM-DD)")


def _boolean(path: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"loan_details.{path} must be true or false")
    return value


_AMOUNT = _number(Decimal(0), _MAX_AMOUNT, 2)

#: What a user may set: each top-level key maps to a normaliser, or to the
#: normalisers of its nested keys.
USER_LOAN_DETAIL_FIELDS: dict[str, Normaliser | dict[str, Normaliser]] = {
    "purpose": _enum(*PURPOSES),
    "type": _enum(*LOAN_TYPES),
    "interest": {
        "rate": _number(Decimal(0), Decimal(100), 4),
        "type": _enum(*INTEREST_TYPES),
        "expires_at": _date,
    },
    "is_interest_only": _boolean,
    "interest_only_expires_at": _date,
    "term": {
        "years": _number(Decimal(0), Decimal(100), 0, whole=True),
        "months": _number(Decimal(0), Decimal(1200), 0, whole=True),
    },
    "matures_at": _date,
    "initial_principal": _AMOUNT,
    "repayment": {
        "frequency": _enum(*REPAYMENT_FREQUENCIES),
        "next_date": _date,
        "next_amount": _AMOUNT,
    },
}


def normalize_user_loan_details(value: Any) -> Optional[dict[str, Any]]:
    """Validate loan details a user sent and return them JSON-safe.

    Unknown keys and wrong value types raise ``ValueError`` (a 400). A null
    or empty value, at any level, means "not set": it is dropped, and details
    with nothing left are None (which clears them).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("loan_details must be an object")
    unknown = sorted(str(k) for k in value if k not in USER_LOAN_DETAIL_FIELDS)
    if unknown:
        raise ValueError(f"Unknown loan_details keys: {', '.join(unknown)}")
    kept: dict[str, Any] = {}
    for key, spec in USER_LOAN_DETAIL_FIELDS.items():
        raw = value.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(spec, dict):
            if not isinstance(raw, dict):
                raise ValueError(f"loan_details.{key} must be an object")
            unknown = sorted(str(k) for k in raw if k not in spec)
            if unknown:
                raise ValueError(f"Unknown loan_details.{key} keys: {', '.join(unknown)}")
            nested = {
                inner: check(f"{key}.{inner}", raw[inner])
                for inner, check in spec.items()
                if raw.get(inner) is not None and raw.get(inner) != ""
            }
            if nested:
                kept[key] = nested
        else:
            kept[key] = spec(key, raw)
    return kept or None


def is_revolving(details: Any) -> bool:
    """Whether loan details describe a revolving loan (a drawable limit)."""
    return isinstance(details, dict) and str(details.get("type") or "").upper() == "REVOLVING"


def merge_provider_details(
    stored: Optional[dict[str, Any]], incoming: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """The details to keep after a sync (see the module docstring for the rule).

    Returns a new dict, so the JSON column sees the change.
    """
    if not incoming:
        return stored
    merged: dict[str, Any] = dict(stored or {})
    for key, value in incoming.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged
