"""Akahu provider (New Zealand open finance).

Akahu (https://www.akahu.nz) aggregates New Zealand bank, credit card,
KiwiSaver and investment accounts. This module targets an Akahu **Personal
App**: the user registers one at https://my.akahu.nz/developers, links their
own accounts, and gets two tokens - an App ID Token (``app_token_...``) and a
User Access Token (``user_token_...``). Every request carries
``Authorization: Bearer <user token>`` and ``X-Akahu-Id: <app token>``.

The connection flow reuses the token dialog (``flow_type = "token"``), which
asks for both tokens; Akahu needs both on every request. They are stored
encrypted per connection, so each household member connects their own
Personal App.

What is mapped, and why some things are not:

* **Pending transactions** come from Akahu's separate pending endpoint. They
  have no id and their date and text still change, so Akahu says to rebuild
  them on every read: each gets an id derived from its content, and the sync
  layer replaces an account's pending rows wholesale (``replaces_pending_rows``),
  handing any edit the user made to the settled row that follows. Account
  balances exclude pending rows (``balance_includes_pending``).
* **Foreign purchases.** ``meta.conversion`` holds the amount and currency
  actually charged abroad; that becomes the transaction amount and currency,
  and the account-currency amount becomes ``amount_in_account_currency``.
* **Investment, KiwiSaver and wallet accounts become ``investment`` accounts**
  with the value Akahu reports, under their own institution like any other
  account. They bring no transactions: their balance moves with the market,
  and contributions are already on the paying bank account, so nothing about
  them reaches income or expenses. Their holdings are synced as detail assets
  that the account's balance already carries
  (``holdings_in_account_balance``), so net worth counts each dollar once.
  The provider's portfolio, breakdown and profile are kept in the holding's
  metadata. A brokerage account whose portfolio lists share positions becomes
  one holding per position (ticker, shares, cost); its uninvested cash is
  counted once, by the wallet account when the connection has one, so the
  brokerage account's balance leaves that cash out.
* **Categories** become a ``category_hint`` (a default-category key), resolved
  by the sync layer to the workspace's category of that name: transfers
  (Akahu type ``TRANSFER``, or the other party is one of the user's own
  accounts), payments into an investment account (its ``payment_details``
  account number), the NZFCC category or its personal-finance group, and for
  rows Akahu leaves uncategorised the transaction type (interest, fees, tax).
  On a loan account, money in is a repayment of principal (``loan_repayment``:
  the Loan repayments category, else Transfers) and money out of Akahu type
  ``LOAN`` or ``INTEREST`` is the lender's interest (``loan_interest``: a
  "Loan interest" category, else Taxes & Fees).
* **Loans** become ``loan`` accounts with the balance Akahu sends (negative =
  owed). ``meta.loan_details`` (rate, term, maturity, repayment) is kept as
  the account's ``loan_details``, display only; a ``HOME`` purpose makes the
  loan a mortgage. A loan with a ``balance.limit`` is a revolving facility,
  and the limit becomes its credit limit.
* **Credit cards** take ``balance.limit``, or on an official connection that
  sends none, available + owed as an estimate (``credit_limit_for``); and
  from an undocumented ``meta.bill`` the minimum payment and, when it can
  only be the due date, the due day (``card_bill_fields``).
* **Inactive accounts** (Akahu lost access to the bank) keep their cached
  balance, fetch no transactions, and flag the connection for the user to
  reconnect the bank at my.akahu.nz (``inactive_account_warnings``).
* **Particulars, code and reference** go to the transaction's notes, and the
  merchant's website to its payee.

All Akahu-specific shapes are contained in this module.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.agents.services.crypto import decrypt, encrypt
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    HoldingData,
    ProviderRateLimited,
    ProviderUserActionRequired,
    RefreshOutcome,
    SessionExpiredError,
    TransactionData,
    mask_last4,
)

logger = logging.getLogger(__name__)

AKAHU_HTTP_TIMEOUT = 60.0
AKAHU_HELP_URL = "https://my.akahu.nz/developers"
# Where the user reconnects a bank Akahu has lost access to.
AKAHU_CONNECTIONS_URL = "https://my.akahu.nz/connections"
# Akahu keeps 730 days of history for a Personal App.
AKAHU_INITIAL_HISTORY_DAYS = 730
# Banks correct dates and descriptions in the days after settlement, and some
# rows appear late. Every sync re-reads at least this window.
AKAHU_LOOKBACK_DAYS = 30
# Akahu refreshes every account on its own schedule; an on-demand refresh is
# only worth asking for when the last one is older than this.
AKAHU_REFRESH_REST = timedelta(hours=1)
AKAHU_REFRESH_POLL_SECONDS = 3.0
AKAHU_REFRESH_POLL_ATTEMPTS = 10
AKAHU_MAX_PAGES = 200
# Akahu is New Zealand only; its timestamps are UTC, and a purchase at 11pm
# belongs to the New Zealand calendar day.
AKAHU_TZ = ZoneInfo("Pacific/Auckland")

INVESTMENT_TYPES = frozenset({"INVESTMENT", "KIWISAVER", "WALLET"})
_ACCOUNT_TYPE_MAP = {
    "CHECKING": "checking",
    "SAVINGS": "savings",
    "TERMDEPOSIT": "savings",
    "CREDITCARD": "credit_card",
    "LOAN": "loan",
    "FOREIGN": "checking",
    "TAX": "checking",
    "REWARDS": "checking",
    "INVESTMENT": "investment",
    "KIWISAVER": "investment",
    "WALLET": "investment",
}

# The documented keys of an account's `meta.loan_details`
# (https://developers.akahu.nz/docs/the-account-model); every one is optional.
# A nested object lists its own keys; None means an object of any scalar
# fields, or a scalar (the shape of `term` is not documented).
_LOAN_DETAIL_KEYS: dict[str, Optional[tuple[str, ...]]] = {
    "purpose": (),
    "type": (),
    "interest": ("rate", "type", "expires_at"),
    "is_interest_only": (),
    "interest_only_expires_at": (),
    "term": None,
    "matures_at": (),
    "initial_principal": (),
    "repayment": ("frequency", "next_date", "next_amount"),
}
# Akahu loan purpose -> Securo loan subtype. Only HOME has a clear meaning;
# any other purpose leaves the subtype for the user to pick.
_LOAN_PURPOSE_SUBTYPES = {"HOME": "mortgage"}

_USER_TOKEN_RE = re.compile(r"user_token_[A-Za-z0-9_\-]+")
_APP_TOKEN_RE = re.compile(r"app_token_[A-Za-z0-9_\-]+")

# NZFCC (https://nzfcc.org) category or personal-finance group -> Securo
# default-category key (app.services.category_service.DEFAULT_CATEGORIES_I18N),
# or an extra hint key the sync layer resolves (connection_service.EXTRA_CATEGORY_HINTS,
# FALLBACK_CATEGORY_HINTS).
# Specific categories first; the group catches the rest. Income categories
# have no personal-finance group, so they are matched by name.
NZFCC_CATEGORY_HINTS = {
    "Cafes and restaurants": "food",
    "Bars, pubs, nightclubs": "food",
    "Fast food stores": "takeaways",
    "Bakeries": "food",
    "Fuel stations": "transport",
    "Parking services": "public_transport",
    "Toll fees": "transport",
    "Taxi, rideshare, and on-demand transport services": "public_transport",
    "Bus and shuttle transport services": "public_transport",
    "Train and rail transport services": "public_transport",
    "Automotive repair and servicing": "transport",
    "Internet services": "housing",
    "Telecommunication services": "housing",
    "Supermarkets and grocery stores": "groceries",
    "Meal kit stores": "groceries",
    "Pharmacies": "health",
    "Gyms, fitness, aquatic facilities, yoga, pilates": "health",
    "Media and entertainment streaming services": "subscriptions",
    "Business software and cloud services": "subscriptions",
    "General retail stores": "shopping",
    "Specialty retail stores (not elsewhere classified)": "shopping",
    "Haircuts and treatments": "haircuts",
    "Childcare services": "childcare",
    "Digital gaming products and services": "games",
    "Financial asset brokers, exchanges, and managed funds": "investments",
    "National government services": "taxes",
    "Hotels, motels, and other temporary accommodation": "travel",
    "Air transport services": "travel",
    "Travel agencies and tour operators": "travel",
    "Insurance": "other",
    "Tax payments": "taxes",
    "Debt repayments": "other",
    "Lending services": "other",
    "Salary or wages": "salary",
    "Self employed income": "salary",
    "Bonuses or commissions": "salary",
    "Redundancy payouts": "salary",
    "Expense reimbursements": "salary",
    "Interest": "interest",
    "Dividends": "investments",
    "Investment withdrawals": "investments",
    "Kiwisaver withdrawals": "investments",
    "Property rental income": "salary",
}
# Akahu transaction types that say what an otherwise uncategorised row is
# (the-transaction-model: INTEREST "an interest payment from the account
# provider", FEE "a fee from the account provider", TAX "a tax payment").
TYPE_HINTS = {
    ("INTEREST", "in"): "interest",
    ("INTEREST", "out"): "taxes",
    ("FEE", "out"): "taxes",
    ("TAX", "out"): "taxes",
}
NZFCC_GROUP_HINTS = {
    "Food": "groceries",
    "Lifestyle": "leisure",
    "Household": "housing",
    "Housing": "housing",
    "Utilities": "housing",
    "Transport": "transport",
    "Health": "health",
    "Appearance": "shopping",
    "Education": "education",
    "Professional Services": "other",
}

_CARD_NUMBER_RE = re.compile(r"\b\d{4}[-\s]?[*xX]{4}[-\s]?[*xX]{4}[-\s]?\d{4}\b")
# NZ bank account number: bank-branch-account-suffix, 2-4-7-2/3 digits.
_NZ_ACCOUNT_RE = re.compile(r"\b\d{2}[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d{2,3}\b")


def parse_tokens(raw: str) -> tuple[str, str]:
    """(user token, app token) from the submitted text; both are required."""
    text = raw or ""
    user = _USER_TOKEN_RE.search(text)
    app = _APP_TOKEN_RE.search(text)
    if not user or not app:
        raise ValueError(
            "Akahu needs both the App ID Token (app_token_...) and the "
            "User Access Token (user_token_...)"
        )
    return user.group(0), app.group(0)


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _iso_currency(value: Any, fallback: Optional[str]) -> Optional[str]:
    if isinstance(value, str) and len(value) == 3 and value.isalpha():
        return value.upper()
    return fallback


def _local_date(value: Any) -> Optional[date]:
    """New Zealand calendar day of an Akahu ISO 8601 timestamp."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(AKAHU_TZ).date()


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def clean_description(raw: Any) -> str:
    """Bank text without card or account numbers, and not shouting.

    The description is shown in every shared view, so a card or account
    number has no place in it. The untouched text stays in ``raw_data``.
    """
    text = _CARD_NUMBER_RE.sub(" ", str(raw or ""))
    text = _NZ_ACCOUNT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -/,:;")
    if text and text == text.upper() and re.search(r"[A-Z]", text):
        text = re.sub(
            r"(^|[\s/&(-])(\w)",
            lambda m: m.group(1) + m.group(2).upper(),
            text.lower(),
        )
    return text[:500]


def account_digits(value: Any) -> str:
    """Digits of an NZ account number ('12-3456-...' and '123456...' compare equal); '' if none."""
    if not isinstance(value, str) or re.search(r"[*xX]", value):
        return ""
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) >= 8 else ""


def _loan_scalar(value: Any) -> Any:
    """A JSON-safe scalar from the feed, or None for anything else."""
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if Decimal(str(value)).is_finite() else None
    if isinstance(value, str):
        return value[:100]
    return None


def loan_details_for(raw_account: dict) -> Optional[dict]:
    """The documented keys of ``meta.loan_details``, JSON-safe, or None.

    Display only: nothing that totals money reads it.
    """
    details = (raw_account.get("meta") or {}).get("loan_details")
    if not isinstance(details, dict):
        return None
    kept: dict[str, Any] = {}
    for key, sub_keys in _LOAN_DETAIL_KEYS.items():
        value = details.get(key)
        if isinstance(value, dict) and sub_keys != ():
            nested = {
                k: scalar
                for k, v in value.items()
                if isinstance(k, str)
                and (sub_keys is None or k in sub_keys)
                and (scalar := _loan_scalar(v)) is not None
            }
            if nested:
                kept[key] = dict(list(nested.items())[:10])
        elif not isinstance(value, dict):
            scalar = _loan_scalar(value)
            if scalar is not None:
                kept[key] = scalar
    return kept or None


def loan_subtype_for(loan_details: Optional[dict]) -> Optional[str]:
    """Securo loan subtype from the loan's purpose, or None when it isn't clear."""
    purpose = (loan_details or {}).get("purpose")
    return _LOAN_PURPOSE_SUBTYPES.get(str(purpose).upper()) if purpose else None


def category_hint_for(
    raw: dict,
    own_accounts: frozenset[str] = frozenset(),
    investment_accounts: frozenset[str] = frozenset(),
    account_type: Optional[str] = None,
) -> Optional[str]:
    """Securo default-category key for an Akahu transaction, or None.

    ``own_accounts`` are the digits of the user's own account numbers;
    ``investment_accounts`` the digits of the bank accounts their investment
    and KiwiSaver providers take payments into (account ``payment_details``).
    ``account_type`` is the Akahu type of the account the row is on. On a
    ``LOAN`` account money in is a repayment, and money out of type ``LOAN``
    or ``INTEREST`` is interest; any other row on a loan falls through to the
    ordinary rules.
    """
    if str(account_type or "").upper() == "LOAN":
        loan_amount = _to_decimal(raw.get("amount"))
        if loan_amount is not None and loan_amount > 0:
            return "loan_repayment"
        if loan_amount is not None and loan_amount < 0 and raw.get("type") in ("LOAN", "INTEREST"):
            return "loan_interest"
    other = account_digits((raw.get("meta") or {}).get("other_account"))
    if raw.get("type") == "TRANSFER" or (other and other in own_accounts):
        return "transfers"
    if other and other in investment_accounts:
        return "investments"
    category = raw.get("category") or {}
    name = category.get("name")
    if name in NZFCC_CATEGORY_HINTS:
        return NZFCC_CATEGORY_HINTS[name]
    group = ((category.get("groups") or {}).get("personal_finance") or {}).get("name")
    if group in NZFCC_GROUP_HINTS:
        return NZFCC_GROUP_HINTS[group]
    amount = _to_decimal(raw.get("amount"))
    if amount is None:
        return None
    return TYPE_HINTS.get((str(raw.get("type") or ""), "out" if amount < 0 else "in"))


def notes_for(raw: dict) -> Optional[str]:
    """Particulars, code and reference as one line, or None."""
    meta = raw.get("meta") or {}
    parts = [
        f"{label}: {str(meta[key]).strip()}"
        for key, label in (("particulars", "Particulars"), ("code", "Code"), ("reference", "Reference"))
        if isinstance(meta.get(key), (str, int)) and str(meta[key]).strip()
    ]
    return " · ".join(parts)[:1000] or None


def merchant_logo_for(raw: dict) -> Optional[str]:
    """The matched merchant's logo URL, or None when there isn't a real one.

    ``meta.logo`` is "typically the logo of the transaction merchant", and a
    generic placeholder (``…/merchants/default.png``) when Akahu has none.
    Only a row Akahu matched to a merchant (``merchant._id``) counts, and the
    placeholder never does: a payee without a real logo is left for the user
    to pick an icon for.
    """
    if not (raw.get("merchant") or {}).get("_id"):
        return None
    logo = (raw.get("meta") or {}).get("logo")
    if not isinstance(logo, str) or not logo.startswith("https://"):
        return None
    if logo.rsplit("/", 1)[-1].split("?", 1)[0].lower() in ("default", "default.png"):
        return None
    return logo[:500]


PENDING_ID_PREFIX = "pending:"


def pending_key(account_external_id: str, raw: dict) -> str:
    """An id for a pending row, from what identifies it while it is pending.

    Two identical rows (same time, amount and text) share the key; the caller
    numbers them in order.
    """
    parts = "|".join(
        str(v)
        for v in (account_external_id, raw.get("date"), raw.get("amount"), raw.get("description"), raw.get("type"))
    )
    return PENDING_ID_PREFIX + hashlib.sha256(parts.encode()).hexdigest()[:20]


def account_number_sets(raw_accounts: list[dict]) -> tuple[frozenset[str], frozenset[str]]:
    """(own account numbers, investment payment account numbers) as digit strings."""
    own = {account_digits(a.get("formatted_account")) for a in raw_accounts}
    invest = {
        account_digits(((a.get("meta") or {}).get("payment_details") or {}).get("account_number"))
        for a in raw_accounts
        if str(a.get("type") or "").upper() in INVESTMENT_TYPES
    }
    return frozenset(own - {""}), frozenset(invest - {""})


def _is_active(raw_acc: dict) -> bool:
    """Akahu still has access: ``status`` ACTIVE (the documented default)."""
    return str(raw_acc.get("status") or "ACTIVE").upper() == "ACTIVE"


def inactive_account_warnings(raw_accounts: list[dict]) -> list[dict]:
    """One user-action warning per account Akahu has lost access to.

    An INACTIVE account keeps its cached data, which no longer refreshes,
    until the user reconnects the bank at my.akahu.nz. The sync layer marks
    the connection as needing the user (``action_required_warnings``) while
    any is listed, and active again once none is.
    """
    return [
        {
            "code": "account_inactive",
            "message": f"Akahu has lost access to this account; reconnect it at {AKAHU_CONNECTIONS_URL}",
            "account_external_id": str(raw["_id"]),
            "help_url": AKAHU_CONNECTIONS_URL,
        }
        for raw in raw_accounts
        if not _is_active(raw)
    ]


def transaction_refresh_stamps(raw_accounts: list[dict]) -> dict[str, datetime]:
    """When Akahu last read each ACTIVE account's transactions, by account id.

    An account whose transactions Akahu has never fetched has no
    ``refreshed.transactions`` (some cards are like that), and an INACTIVE
    account no longer refreshes; neither is listed, so neither can hold up a
    refresh.
    """
    stamps: dict[str, datetime] = {}
    for raw in raw_accounts:
        stamp = _parse_timestamp((raw.get("refreshed") or {}).get("transactions"))
        if stamp is not None and _is_active(raw):
            stamps[str(raw["_id"])] = stamp
    return stamps


def credit_limit_for(
    raw: dict, account_type: str, balance: Decimal
) -> tuple[Optional[Decimal], bool]:
    """(credit limit, derived) for a card or loan; (None, False) otherwise.

    ``balance.limit`` is "only present when provided directly by the
    institution"; when it is, it is the limit, for a card and for a loan (a
    loan with a limit is a revolving facility). ``balance`` is the account's
    balance in Securo's convention (a card's debt positive).

    A card without one can still show its limit: an official open-banking
    card sends ``available``, and its limit is available + owed. Akahu and
    the bank sent no explicit limit, so this is a derived figure, flagged so
    sync only fills an empty limit with it. Pending purchases already reduce
    ``available`` but not the balance, so while any are held it reads low by
    their amount. Classic (screen-scraped) connections are left alone: their
    cards report debt with the opposite sign, and how each bank fills
    ``available`` there is not documented, so the sum is not reliably the
    limit.
    """
    if account_type not in ("credit_card", "loan"):
        return None, False
    balance_obj = raw.get("balance") or {}
    limit = _to_decimal(balance_obj.get("limit"))
    if limit is not None:
        return limit.copy_abs(), False
    if account_type != "credit_card":
        return None, False
    if (raw.get("connection") or {}).get("connection_type") != "official":
        return None, False
    available = _to_decimal(balance_obj.get("available"))
    if available is None:
        return None, False
    derived = (available + balance).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (derived, True) if derived > 0 else (None, False)


def card_bill_fields(raw: dict, today: Optional[date] = None) -> tuple[Optional[int], Optional[Decimal]]:
    """(payment due day, minimum payment) from a card's ``meta.bill``.

    Some classic card connections send ``meta.bill`` (``bill_date``,
    ``closing_balance``, ``due_amount``, ``minimum_amount``,
    ``outstanding_amount``); Akahu does not document it. Only what fits an
    existing card field is read:

    * ``minimum_amount`` is the statement's minimum payment, kept as a
      positive amount owed.
    * ``bill_date`` is not documented as either the statement date or the
      due date. A statement is never dated in the future, so a ``bill_date``
      after today (New Zealand time) can only be the due date, and its day
      of the month becomes the payment due day. A past or same-day one is
      left alone (None keeps the stored day on sync).

    The balances have no Securo field and are not read.
    """
    bill = (raw.get("meta") or {}).get("bill")
    if not isinstance(bill, dict):
        return None, None
    minimum = _to_decimal(bill.get("minimum_amount"))
    due_day = None
    bill_date = _local_date(bill.get("bill_date"))
    if bill_date is not None and bill_date > (today or datetime.now(AKAHU_TZ).date()):
        due_day = bill_date.day
    return due_day, minimum.copy_abs() if minimum is not None else None


def _has_transactions(raw_acc: dict) -> bool:
    attributes = raw_acc.get("attributes")
    if isinstance(attributes, list):
        return "TRANSACTIONS" in attributes
    return True


def _share_positions(portfolio: Any, currency: str) -> list[dict]:
    """The share positions in an investment account's `meta.portfolio`.

    Only entries with a share count and a value count: a brokerage (Hatch,
    Sharesies) lists positions, while a fund manager's portfolio is a fund
    split of one balance and stays a single holding. Akahu can list one
    position more than once under different fund ids when a ticker maps to
    two funds (seen with Hatch: same symbol, shares, value and returns, and
    the account's own `breakdown` totals match one position). Those collapse
    into one, named after the later entry.
    """
    if not isinstance(portfolio, list):
        return []
    positions: dict[tuple, dict] = {}
    for entry in portfolio:
        if not isinstance(entry, dict):
            continue
        shares = _to_decimal(entry.get("shares"))
        value = _to_decimal(entry.get("value"))
        if shares is None or value is None:
            continue
        symbol = str(entry.get("symbol") or "").strip().upper() or None
        key = (symbol or entry.get("fund_id") or entry.get("name"), shares, value,
               _to_decimal(entry.get("returns")),
               _iso_currency(entry.get("currency"), currency))
        fund_ids = positions.get(key, {}).get("fund_ids", [])
        positions[key] = {
            **entry,
            "symbol": symbol,
            "shares": shares,
            "value": value,
            "returns": key[3],
            "currency": key[4],
            "fund_ids": fund_ids + [entry.get("fund_id")],
        }
    return list(positions.values())


def _wallet_holds(raw: dict, currency: str, cash: Decimal, raw_accounts: list[dict]) -> bool:
    """Does a WALLET account of the same provider connection carry this cash?"""
    connection_id = (raw.get("connection") or {}).get("_id")
    return any(
        str(acc.get("type") or "").upper() == "WALLET"
        and (acc.get("connection") or {}).get("_id") == connection_id
        and _iso_currency((acc.get("balance") or {}).get("currency"), currency) == currency
        and (wallet := _to_decimal((acc.get("balance") or {}).get("current"))) is not None
        and abs(wallet - cash) < Decimal("0.01")
        for acc in raw_accounts
    )


def investment_balance(raw: dict, raw_accounts: list[dict]) -> Decimal:
    """What an investment, KiwiSaver or wallet account adds to net worth.

    The value Akahu reports, except that a brokerage account listing share
    positions leaves out the uninvested cash a wallet account of the same
    provider already carries (Hatch reports it on both). It always equals the
    sum of the account's holdings (``get_holdings``).
    """
    balance_obj = raw.get("balance") or {}
    balance = _to_decimal(balance_obj.get("current")) or Decimal("0")
    currency = _iso_currency(balance_obj.get("currency"), "NZD") or "NZD"
    positions = _share_positions((raw.get("meta") or {}).get("portfolio"), currency)
    if not positions:
        return balance
    cash = balance - sum((pos["value"] for pos in positions), Decimal("0"))
    if cash >= Decimal("0.01") and _wallet_holds(raw, currency, cash, raw_accounts):
        return balance - cash
    return balance


def _position_holdings(
    raw: dict,
    name: str,
    currency: str,
    balance: Decimal,
    positions: list[dict],
    raw_accounts: list[dict],
) -> list[HoldingData]:
    """One holding per share position, plus any cash the balance holds beyond them.

    The leftover is the account's uninvested cash. When the same connection
    lists that cash as its own WALLET account (Hatch does), the wallet already
    carries it, so it is not added a second time.
    """
    account_id = str(raw["_id"])
    provider = (raw.get("connection") or {}).get("name")
    holdings = []
    for pos in positions:
        returns = pos["returns"]
        shares = pos["shares"]
        holdings.append(
            HoldingData(
                external_id=f"{account_id}::{pos['symbol'] or pos.get('fund_id') or pos.get('name')}",
                name=str(pos.get("name") or pos["symbol"] or "Holding")[:255],
                currency=pos["currency"] or currency,
                current_value=pos["value"],
                quantity=shares,
                unit_price=pos["value"] / shares if shares else None,
                purchase_price=pos["value"] - returns if returns is not None else None,
                ticker=pos["symbol"],
                metadata={
                    "akahu_type": raw.get("type"),
                    "provider": provider,
                    "fund_ids": [f for f in pos["fund_ids"] if f],
                    "returns": float(returns) if returns is not None else None,
                },
                account_external_id=account_id,
                account_name=name,
            )
        )
    cash = balance - sum((pos["value"] for pos in positions), Decimal("0"))
    if cash >= Decimal("0.01") and not _wallet_holds(raw, currency, cash, raw_accounts):
        holdings.append(
            HoldingData(
                external_id=f"{account_id}::cash",
                name=f"{name} cash"[:255],
                currency=currency,
                current_value=cash,
                metadata={"akahu_type": raw.get("type"), "provider": provider},
                account_external_id=account_id,
                account_name=name,
            )
        )
    return holdings


class AkahuProvider(BankProvider):
    """Akahu Personal App connector."""

    base_url = "https://api.akahu.io/v1"

    def __init__(self) -> None:
        # Accounts Akahu has lost access to, found by the last get_accounts
        # (inactive_account_warnings); read by the sync layer.
        self.action_required_warnings: list[dict] = []
        # (own, investment payment) account numbers this connection reported
        # on its last account read, kept by the sync layer on the connection
        # so other connections in the workspace can recognise them.
        self.account_numbers: Optional[tuple[frozenset[str], frozenset[str]]] = None
        # The same sets from the workspace's other connections, set by the
        # sync layer: a payment to a partner's account on another connection
        # is still a transfer between the household's own accounts.
        self.workspace_account_numbers: tuple[frozenset[str], frozenset[str]] = (
            frozenset(),
            frozenset(),
        )
        # Accounts whose pending list could not be read this time: their
        # stored pending rows must not be taken as settled or dropped.
        self.pending_read_failed: set[str] = set()

    @property
    def name(self) -> str:
        return "akahu"

    @property
    def flow_type(self) -> str:
        return "token"

    @property
    def holdings_in_account_balance(self) -> bool:
        # Each holding belongs to an investment account whose balance is the
        # holdings' total (investment_balance).
        return True

    @property
    def names_accounts_by_institution(self) -> bool:
        # Akahu's account `name` is the user's nickname where the bank allows
        # one ("Spending Account"), else the product name; the institution
        # already shows with its logo next to the connection.
        return False

    @property
    def edits_transactions_in_place(self) -> bool:
        # Banks change a settled row's date, text or amount after posting;
        # Akahu updates its copy under the same _id ("Keeping transactions
        # synchronised", developers.akahu.nz).
        return True

    @property
    def reports_removed_transactions(self) -> bool:
        # A row the bank removes, Akahu removes too; one changed beyond
        # recognition comes back under a new _id. get_transactions records
        # each window it read completely.
        return True

    @property
    def replaces_pending_rows(self) -> bool:
        # Pending rows have no stable id ("delete then re-create each time",
        # developers.akahu.nz, Accessing transactional data); the sync layer
        # rebuilds them from every read.
        return True

    @property
    def balance_includes_pending(self) -> bool:
        # `balance.current` leaves pending rows out: on a card it equals the
        # limit less `available` less the pending total (measured on live
        # bank data, Oct 2026).
        return False

    # ----- credentials ------------------------------------------------------

    @staticmethod
    def _secret(credentials: dict, key: str) -> str:
        enc = (credentials or {}).get(f"{key}_enc")
        if enc:
            decoded = decrypt(enc)
            if decoded:
                return decoded
        return ""

    def _tokens(self, credentials: dict) -> tuple[str, str]:
        user_token = self._secret(credentials, "user_token")
        app_token = self._secret(credentials, "app_token")
        if not user_token or not app_token:
            raise SessionExpiredError("Akahu tokens are missing; reconnect with both tokens")
        return user_token, app_token

    async def _client(self, credentials: dict | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=AKAHU_HTTP_TIMEOUT,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "Securo"},
        )

    async def _request(
        self,
        credentials: dict,
        method: str,
        path: str,
        params: Optional[dict] = None,
    ) -> dict:
        user_token, app_token = self._tokens(credentials)
        headers = {"Authorization": f"Bearer {user_token}", "X-Akahu-Id": app_token}
        async with await self._client(credentials) as client:
            resp = await client.request(
                method, f"{self.base_url}{path}", params=params, headers=headers
            )
        if resp.status_code in (401, 403):
            raise ProviderUserActionRequired(
                f"Akahu refused the request ({resp.status_code}); check the tokens",
                code="credentials_invalid",
                help_url=AKAHU_HELP_URL,
            )
        if resp.status_code == 429:
            raise ProviderRateLimited("Akahu rate limit reached")
        if resp.status_code >= 400:
            # The body is Akahu's text; keep it out of the message.
            raise RuntimeError(f"Akahu {method} {path.split('?')[0]} returned {resp.status_code}")
        body = resp.json() if resp.content else {}
        if isinstance(body, dict) and body.get("success") is False:
            raise RuntimeError(f"Akahu reported a failure for {method} {path.split('?')[0]}")
        return body if isinstance(body, dict) else {}

    async def _list_accounts(self, credentials: dict) -> list[dict]:
        body = await self._request(credentials, "GET", "/accounts")
        items = body.get("items")
        if not isinstance(items, list):
            raise RuntimeError("Akahu returned no account list")
        return [item for item in items if isinstance(item, dict) and item.get("_id")]

    # ----- connection flow --------------------------------------------------

    def get_oauth_url(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError("Akahu uses the paste-a-token flow, not OAuth redirect")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Store the pasted tokens and read the linked accounts once."""
        user_token, app_token = parse_tokens(code)
        credentials: dict[str, Any] = {
            "user_token_enc": encrypt(user_token),
            "app_token_enc": encrypt(app_token),
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }
        if not credentials["user_token_enc"] or not credentials["app_token_enc"]:
            raise RuntimeError("Could not encrypt the Akahu tokens")

        me = (await self._request(credentials, "GET", "/me")).get("item") or {}
        raw_accounts = await self._list_accounts(credentials)
        self.account_numbers = account_number_sets(raw_accounts)
        connections = {
            str(name)
            for acc in raw_accounts
            if (name := (acc.get("connection") or {}).get("name"))
        }
        institution_name = connections.pop() if len(connections) == 1 else "Akahu"
        user_id = me.get("_id") or hashlib.sha256(user_token.encode()).hexdigest()[:24]
        return ConnectionData(
            external_id=f"akahu-{user_id}",
            institution_name=institution_name,
            credentials=credentials,
            accounts=self._parse_accounts(raw_accounts),
            logo_url=None,
        )

    # ----- accounts ---------------------------------------------------------

    @staticmethod
    def _parse_accounts(raw_accounts: list[dict]) -> list[AccountData]:
        accounts: list[AccountData] = []
        for raw in raw_accounts:
            akahu_type = str(raw.get("type") or "").upper()
            balance_obj = raw.get("balance") or {}
            balance = (
                investment_balance(raw, raw_accounts)
                if akahu_type in INVESTMENT_TYPES
                else _to_decimal(balance_obj.get("current")) or Decimal("0")
            )
            account_type = _ACCOUNT_TYPE_MAP.get(akahu_type, "checking")
            loan_details = loan_details_for(raw) if account_type == "loan" else None
            connection = raw.get("connection") or {}
            if account_type == "credit_card" and connection.get("connection_type") != "official":
                # Securo's convention is a positive number for card debt.
                # Akahu documents debt as negative, and classic connections
                # follow that (a card owing 1,234.56 reports -1234.56).
                # Official open-banking connections already report the debt
                # as positive (a card owing 250.00 reports +250.00
                # with 4,750.00 available), so only classic balances flip.
                balance = -balance
            credit_limit, limit_derived = credit_limit_for(raw, account_type, balance)
            due_day, minimum_payment = (
                card_bill_fields(raw) if account_type == "credit_card" else (None, None)
            )
            accounts.append(
                AccountData(
                    external_id=str(raw["_id"]),
                    name=str(raw.get("name") or "Account")[:255],
                    type=account_type,
                    balance=balance,
                    currency=_iso_currency(balance_obj.get("currency"), "NZD") or "NZD",
                    credit_limit=credit_limit,
                    credit_limit_derived=limit_derived,
                    payment_due_day=due_day,
                    minimum_payment=minimum_payment,
                    masked_number=mask_last4(raw.get("formatted_account")),
                    institution_external_id=str(connection["_id"]) if connection.get("_id") else None,
                    institution_name=connection.get("name"),
                    institution_logo_url=connection.get("logo"),
                    loan_details=loan_details,
                    loan_subtype=loan_subtype_for(loan_details),
                )
            )
        return accounts

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        raw_accounts = await self._list_accounts(credentials)
        self.action_required_warnings = inactive_account_warnings(raw_accounts)
        self.account_numbers = account_number_sets(raw_accounts)
        return self._parse_accounts(raw_accounts)

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        for raw in await self._list_accounts(credentials):
            logo = (raw.get("connection") or {}).get("logo")
            if logo:
                return logo
        return None

    # ----- transactions -----------------------------------------------------

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        raw_accounts = await self._list_accounts(credentials)
        own_accounts, investment_accounts = account_number_sets(raw_accounts)
        other_own, other_investment = getattr(
            self, "workspace_account_numbers", (frozenset(), frozenset())
        )
        own_accounts |= other_own
        investment_accounts |= other_investment
        accounts = {str(a["_id"]): a for a in raw_accounts}
        raw_acc = accounts.get(account_external_id)
        if raw_acc is None or not _has_transactions(raw_acc):
            return []
        if str(raw_acc.get("type") or "").upper() in INVESTMENT_TYPES:
            # Balance only: see the module docstring.
            return []
        if not _is_active(raw_acc):
            # Akahu lost access; get_accounts reports it for the user.
            return []

        today = datetime.now(AKAHU_TZ).date()
        if since is None:
            start_date = today - timedelta(days=AKAHU_INITIAL_HISTORY_DAYS)
        else:
            start_date = min(since, today - timedelta(days=AKAHU_LOOKBACK_DAYS))
        # `start` is exclusive and `end` inclusive; both are instants. Ask for
        # one extra day on each side and let the external-id dedup settle it.
        start = datetime.combine(start_date - timedelta(days=1), datetime.min.time(), AKAHU_TZ)
        end = datetime.combine(today + timedelta(days=1), datetime.min.time(), AKAHU_TZ)
        params: dict[str, Any] = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        currency = _iso_currency((raw_acc.get("balance") or {}).get("currency"), "NZD")
        akahu_account_type = str(raw_acc.get("type") or "").upper()
        transactions: list[TransactionData] = []
        seen: set[str] = set()
        path = f"/accounts/{account_external_id}/transactions"
        for _ in range(AKAHU_MAX_PAGES):
            body = await self._request(credentials, "GET", path, params=params)
            items = body.get("items")
            if not isinstance(items, list):
                raise RuntimeError("Akahu returned no transaction list")
            for raw in items:
                parsed = self._build_transaction(
                    raw, currency, payee_source, own_accounts, investment_accounts,
                    akahu_account_type,
                )
                if parsed and parsed.external_id not in seen:
                    seen.add(parsed.external_id)
                    transactions.append(parsed)
            cursor = (body.get("cursor") or {}).get("next")
            if not cursor:
                # Every page read: rows missing from this window are gone at
                # Akahu. Never vouched for when the account isn't ACTIVE.
                if str(raw_acc.get("status") or "ACTIVE").upper() == "ACTIVE":
                    self.record_complete_transaction_window(
                        account_external_id, start_date, today
                    )
                transactions.extend(
                    await self._pending_transactions(
                        credentials, account_external_id, currency, payee_source,
                        own_accounts, investment_accounts, akahu_account_type,
                    )
                )
                return transactions
            params = {**params, "cursor": cursor}
        raise RuntimeError(f"Akahu returned more than {AKAHU_MAX_PAGES} pages")

    def _pending_unread(self, account_external_id: str) -> None:
        failed = getattr(self, "pending_read_failed", None)
        if failed is None:
            failed = self.pending_read_failed = set()
        failed.add(account_external_id)

    async def _pending_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        currency: Optional[str],
        payee_source: str,
        own_accounts: frozenset[str],
        investment_accounts: frozenset[str],
        akahu_account_type: str,
    ) -> list[TransactionData]:
        """The account's pending rows, each with an id made from its content.

        The endpoint returns the whole current set every time, so a row that
        is no longer here has settled or been dropped by the bank.
        """
        try:
            body = await self._request(
                credentials, "GET", f"/accounts/{account_external_id}/transactions/pending"
            )
        except (RuntimeError, httpx.HTTPError):
            # The settled rows still count; the pending ones already stored
            # stay as they are until a read succeeds (pending_read_failed).
            logger.warning("Akahu pending read failed for an account; keeping its pending rows")
            self._pending_unread(account_external_id)
            return []
        items = body.get("items")
        if not isinstance(items, list):
            self._pending_unread(account_external_id)
            return []
        seen: Counter[str] = Counter()
        rows: list[TransactionData] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            key = pending_key(account_external_id, raw)
            seen[key] += 1
            parsed = self._build_transaction(
                {**raw, "_securo_pending_id": f"{key}:{seen[key]}"},
                currency, payee_source, own_accounts, investment_accounts,
                akahu_account_type,
            )
            if parsed:
                rows.append(parsed)
        return rows

    @staticmethod
    def _build_transaction(
        raw: Any,
        currency: Optional[str],
        payee_source: str,
        own_accounts: frozenset[str] = frozenset(),
        investment_accounts: frozenset[str] = frozenset(),
        account_type: Optional[str] = None,
    ) -> Optional[TransactionData]:
        if not isinstance(raw, dict):
            return None
        pending_id = raw.get("_securo_pending_id")
        if not raw.get("_id") and not pending_id:
            return None
        amount = _to_decimal(raw.get("amount"))
        txn_date = _local_date(raw.get("date"))
        if amount is None or txn_date is None:
            return None
        merchant_obj = raw.get("merchant") or {}
        merchant = merchant_obj.get("name")
        description = clean_description(raw.get("description")) or merchant or raw.get("type") or "Transaction"
        payee = None
        payee_website = None
        payee_logo_url = None
        if payee_source != "none" and isinstance(merchant, str) and merchant.strip():
            payee = merchant.strip()[:500]
            website = merchant_obj.get("website")
            if isinstance(website, str) and website.startswith(("http://", "https://")):
                payee_website = website[:255]
            payee_logo_url = merchant_logo_for(raw)

        # A purchase made abroad: Akahu's conversion holds the amount and
        # currency actually charged ("a payment of GBP 2.15"); the row's own
        # amount is what the account was debited in its currency.
        txn_amount = amount.copy_abs()
        txn_currency = currency
        in_account_currency = None
        conversion = (raw.get("meta") or {}).get("conversion") or {}
        foreign_amount = _to_decimal(conversion.get("amount"))
        foreign_currency = _iso_currency(conversion.get("currency"), None)
        if foreign_amount and foreign_currency and foreign_currency != currency:
            txn_amount = foreign_amount.copy_abs()
            txn_currency = foreign_currency
            in_account_currency = amount.copy_abs()

        return TransactionData(
            external_id=str(raw.get("_id") or pending_id),
            description=str(description)[:500],
            amount=txn_amount,
            date=txn_date,
            type="debit" if amount < 0 else "credit",
            currency=txn_currency,
            amount_in_account_currency=in_account_currency,
            status="pending" if pending_id and not raw.get("_id") else "posted",
            payee=payee,
            raw_data=raw,
            category_hint=category_hint_for(
                raw, own_accounts, investment_accounts, account_type=account_type
            ),
            notes=notes_for(raw),
            payee_website=payee_website,
            payee_logo_url=payee_logo_url,
        )

    # ----- investments ------------------------------------------------------

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        holdings: list[HoldingData] = []
        raw_accounts = await self._list_accounts(credentials)
        for raw in raw_accounts:
            if str(raw.get("type") or "").upper() not in INVESTMENT_TYPES:
                continue
            balance_obj = raw.get("balance") or {}
            value = _to_decimal(balance_obj.get("current"))
            if value is None:
                continue
            currency = _iso_currency(balance_obj.get("currency"), "NZD") or "NZD"
            meta = raw.get("meta") or {}
            name = str(raw.get("name") or "Investment")[:255]
            positions = _share_positions(meta.get("portfolio"), currency)
            if positions:
                holdings.extend(
                    _position_holdings(raw, name, currency, value, positions, raw_accounts)
                )
                continue
            holdings.append(
                HoldingData(
                    external_id=str(raw["_id"]),
                    name=name,
                    currency=currency,
                    current_value=value,
                    metadata={
                        "akahu_type": raw.get("type"),
                        "provider": (raw.get("connection") or {}).get("name"),
                        "portfolio": meta.get("portfolio"),
                        "breakdown": meta.get("breakdown"),
                        "profile": meta.get("profile"),
                    },
                    account_external_id=str(raw["_id"]),
                    account_name=name,
                )
            )
        return holdings


    # ----- refresh ----------------------------------------------------------

    async def trigger_refresh(self, credentials: dict | None) -> RefreshOutcome:
        """Ask Akahu to re-read the banks when its data is over an hour old.

        Only accounts with a ``refreshed.transactions`` stamp take part
        (``transaction_refresh_stamps``): ones Akahu never fetched
        transactions for, or has lost access to, would never move. The
        refresh is requested when one of them is older than
        AKAHU_REFRESH_REST, and it has worked once each of those stale
        accounts has a newer stamp. Fresher accounts are not waited for:
        Akahu skips accounts refreshed within its rest period (15 minutes by
        default, https://developers.akahu.nz/docs/data-refreshes).
        """
        if not credentials:
            return "skipped"
        try:
            before = transaction_refresh_stamps(await self._list_accounts(credentials))
            now = datetime.now(timezone.utc)
            stale = {
                acc_id: stamp
                for acc_id, stamp in before.items()
                if now - stamp >= AKAHU_REFRESH_REST
            }
            if not stale:
                return "skipped"
            await self._request(credentials, "POST", "/refresh")
            for _ in range(AKAHU_REFRESH_POLL_ATTEMPTS):
                await asyncio.sleep(AKAHU_REFRESH_POLL_SECONDS)
                after = transaction_refresh_stamps(await self._list_accounts(credentials))
                # An account that went away or lost access meanwhile is not
                # waited for either.
                if all(
                    acc_id not in after or after[acc_id] > stamp
                    for acc_id, stamp in stale.items()
                ):
                    return "refreshed"
            return "failed"
        except ProviderUserActionRequired:
            return "needs_user_action"
        except (ProviderRateLimited, httpx.HTTPError, RuntimeError):
            logger.warning("Akahu refresh did not complete", exc_info=True)
            return "failed"

    async def refresh_credentials(self, credentials: dict) -> dict:
        self._tokens(credentials)
        return credentials
