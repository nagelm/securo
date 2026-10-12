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

* **Settled transactions only.** Akahu's pending transactions have no stable
  id, and their date and text still change; Securo's pending/posted twin
  match needs the same date, so an imported pending row would usually
  linger next to its settled version. Settled rows arrive within a day or two.
* **Amounts stay in the account currency.** ``meta.conversion`` (the foreign
  amount of a card purchase) is kept in ``raw_data`` only.
* **Investment, KiwiSaver and wallet accounts become holdings, not accounts.**
  Their balance is a market value, and listing them as accounts as well would
  count them twice in net worth.
* **NZFCC categories** become a ``category_hint`` (a default-category key),
  resolved by the sync layer to the workspace's category of that name.

All Akahu-specific shapes are contained in this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
    "LOAN": "checking",
    "FOREIGN": "checking",
    "TAX": "checking",
    "REWARDS": "checking",
}

_USER_TOKEN_RE = re.compile(r"user_token_[A-Za-z0-9_\-]+")
_APP_TOKEN_RE = re.compile(r"app_token_[A-Za-z0-9_\-]+")

# NZFCC (https://nzfcc.org) category or personal-finance group -> Securo
# default-category key (app.services.category_service.DEFAULT_CATEGORIES_I18N).
# Specific categories first; the group catches the rest. Income categories
# have no personal-finance group, so they are matched by name.
NZFCC_CATEGORY_HINTS = {
    "Cafes and restaurants": "food",
    "Bars, pubs, nightclubs": "food",
    "Fast food stores": "food",
    "Bakeries": "food",
    "Fuel stations": "transport",
    "Parking services": "transport",
    "Toll fees": "transport",
    "Taxi, rideshare, and on-demand transport services": "transport",
    "Bus and shuttle transport services": "transport",
    "Train and rail transport services": "transport",
    "Automotive repair and servicing": "transport",
    "Internet services": "housing",
    "Telecommunication services": "housing",
    "Pharmacies": "health",
    "Gyms, fitness, aquatic facilities, yoga, pilates": "health",
    "Media and entertainment streaming services": "subscriptions",
    "Hotels, motels, and other temporary accommodation": "leisure",
    "Air transport services": "leisure",
    "Travel agencies and tour operators": "leisure",
    "Insurance": "other",
    "Tax payments": "taxes",
    "Debt repayments": "other",
    "Lending services": "other",
    "Salary or wages": "salary",
    "Self employed income": "salary",
    "Bonuses or commissions": "salary",
    "Redundancy payouts": "salary",
    "Expense reimbursements": "salary",
    "Interest": "investments",
    "Dividends": "investments",
    "Investment withdrawals": "investments",
    "Kiwisaver withdrawals": "investments",
    "Property rental income": "salary",
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


def category_hint_for(raw: dict) -> Optional[str]:
    """Securo default-category key for an Akahu transaction, or None."""
    if raw.get("type") == "TRANSFER":
        return "transfers"
    category = raw.get("category") or {}
    name = category.get("name")
    if name in NZFCC_CATEGORY_HINTS:
        return NZFCC_CATEGORY_HINTS[name]
    group = ((category.get("groups") or {}).get("personal_finance") or {}).get("name")
    return NZFCC_GROUP_HINTS.get(group)


def _has_transactions(raw_acc: dict) -> bool:
    attributes = raw_acc.get("attributes")
    if isinstance(attributes, list):
        return "TRANSACTIONS" in attributes
    return True


class AkahuProvider(BankProvider):
    """Akahu Personal App connector."""

    base_url = "https://api.akahu.io/v1"

    @property
    def name(self) -> str:
        return "akahu"

    @property
    def flow_type(self) -> str:
        return "token"

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
            if akahu_type in INVESTMENT_TYPES:
                continue
            balance_obj = raw.get("balance") or {}
            balance = _to_decimal(balance_obj.get("current")) or Decimal("0")
            account_type = _ACCOUNT_TYPE_MAP.get(akahu_type, "checking")
            if account_type == "credit_card":
                # Akahu reports card debt as a negative balance; Securo's
                # convention is a positive number for debt.
                balance = -balance
            connection = raw.get("connection") or {}
            accounts.append(
                AccountData(
                    external_id=str(raw["_id"]),
                    name=str(raw.get("name") or "Account")[:255],
                    type=account_type,
                    balance=balance,
                    currency=_iso_currency(balance_obj.get("currency"), "NZD") or "NZD",
                    credit_limit=_to_decimal(balance_obj.get("limit"))
                    if account_type == "credit_card"
                    else None,
                    masked_number=mask_last4(raw.get("formatted_account")),
                    institution_external_id=str(connection["_id"]) if connection.get("_id") else None,
                    institution_name=connection.get("name"),
                    institution_logo_url=connection.get("logo"),
                )
            )
        return accounts

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        return self._parse_accounts(await self._list_accounts(credentials))

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
        accounts = {str(a["_id"]): a for a in await self._list_accounts(credentials)}
        raw_acc = accounts.get(account_external_id)
        if raw_acc is None or not _has_transactions(raw_acc):
            return []
        if str(raw_acc.get("status") or "ACTIVE").upper() != "ACTIVE":
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
        transactions: list[TransactionData] = []
        seen: set[str] = set()
        path = f"/accounts/{account_external_id}/transactions"
        for _ in range(AKAHU_MAX_PAGES):
            body = await self._request(credentials, "GET", path, params=params)
            items = body.get("items")
            if not isinstance(items, list):
                raise RuntimeError("Akahu returned no transaction list")
            for raw in items:
                parsed = self._build_transaction(raw, currency, payee_source)
                if parsed and parsed.external_id not in seen:
                    seen.add(parsed.external_id)
                    transactions.append(parsed)
            cursor = (body.get("cursor") or {}).get("next")
            if not cursor:
                return transactions
            params = {**params, "cursor": cursor}
        raise RuntimeError(f"Akahu returned more than {AKAHU_MAX_PAGES} pages")

    @staticmethod
    def _build_transaction(
        raw: Any, currency: Optional[str], payee_source: str
    ) -> Optional[TransactionData]:
        if not isinstance(raw, dict) or not raw.get("_id"):
            return None
        amount = _to_decimal(raw.get("amount"))
        txn_date = _local_date(raw.get("date"))
        if amount is None or txn_date is None:
            return None
        merchant = (raw.get("merchant") or {}).get("name")
        description = clean_description(raw.get("description")) or merchant or raw.get("type") or "Transaction"
        payee = None
        if payee_source != "none" and isinstance(merchant, str) and merchant.strip():
            payee = merchant.strip()[:500]
        return TransactionData(
            external_id=str(raw["_id"]),
            description=str(description)[:500],
            amount=amount.copy_abs(),
            date=txn_date,
            type="debit" if amount < 0 else "credit",
            currency=currency,
            status="posted",
            payee=payee,
            raw_data=raw,
            category_hint=category_hint_for(raw),
        )

    # ----- investments ------------------------------------------------------

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        holdings: list[HoldingData] = []
        for raw in await self._list_accounts(credentials):
            if str(raw.get("type") or "").upper() not in INVESTMENT_TYPES:
                continue
            balance_obj = raw.get("balance") or {}
            value = _to_decimal(balance_obj.get("current"))
            if value is None:
                continue
            currency = _iso_currency(balance_obj.get("currency"), "NZD") or "NZD"
            meta = raw.get("meta") or {}
            name = str(raw.get("name") or "Investment")[:255]
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
                    },
                    account_external_id=str(raw["_id"]),
                    account_name=name,
                )
            )
        return holdings

    # ----- refresh ----------------------------------------------------------

    async def trigger_refresh(self, credentials: dict | None) -> RefreshOutcome:
        """Ask Akahu to re-read the banks when its data is over an hour old."""
        if not credentials:
            return "skipped"
        try:
            before = await self._list_accounts(credentials)
            stamps = [
                _parse_timestamp((acc.get("refreshed") or {}).get("transactions"))
                for acc in before
            ]
            stamps = [s for s in stamps if s is not None]
            if not stamps:
                return "skipped"
            oldest = min(stamps)
            if datetime.now(timezone.utc) - oldest < AKAHU_REFRESH_REST:
                return "skipped"
            await self._request(credentials, "POST", "/refresh")
            for _ in range(AKAHU_REFRESH_POLL_ATTEMPTS):
                await asyncio.sleep(AKAHU_REFRESH_POLL_SECONDS)
                after = await self._list_accounts(credentials)
                fresh = [
                    _parse_timestamp((acc.get("refreshed") or {}).get("transactions"))
                    for acc in after
                ]
                if all(s is not None and s > oldest for s in fresh):
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
