import re
import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.fiscal.registry import TaxIdKind
from app.providers.favicon import favicon_url_for
from app.schemas.category import CategoryRead

#: A counterparty's legal nature, or null when it is not known. Closed, and
#: closed on *natures* only: a role like "merchant" is something a
#: counterparty does, and belongs nowhere in this list.
PayeeType = Literal["person", "company"]

#: Where a row came from. Never accepted from a request body: the server
#: knows which code path inserted the row, and a client claiming otherwise
#: would defeat the point.
PayeeSource = Literal["manual", "sync", "import"]


#: Longest logo URL the payees.logo_url column holds.
LOGO_URL_MAX_LENGTH = 500

#: A host typed without a scheme, optionally with a port and a path
#: ("shop.example", "cdn.shop.example/logo.png"). Needs a dot, so a
#: "javascript:..." or "data:..." value can never pass for one.
_SCHEMELESS_URL = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?(/\S*)?$")


def normalize_logo_url(value: Optional[str]) -> Optional[str]:
    """The logo URL a user typed, as the image URL to store.

    Blank clears it. Only http(s) is accepted, so a ``javascript:`` or
    ``data:`` URL can never reach an ``<img>``. A website rather than an
    image (a bare domain, or a URL with nothing after the host) becomes that
    site's favicon, the same image the website field would give. Anything
    with a path is taken as the image itself: plenty of logo URLs have no
    file extension, so the extension can't decide it.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > LOGO_URL_MAX_LENGTH:
        raise ValueError(f"logo_url must be at most {LOGO_URL_MAX_LENGTH} characters")
    if any(ch.isspace() for ch in value):
        raise ValueError("logo_url must be an http or https URL")
    if "://" not in value:
        if not _SCHEMELESS_URL.match(value):
            raise ValueError("logo_url must be an http or https URL")
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("logo_url must be an http or https URL")
    if parsed.path in ("", "/") and not parsed.query:
        return favicon_url_for(value)
    return value


class TaxIdInput(BaseModel):
    """One fiscal document on the way in.

    `kind` is the closed enum, so an unknown document type is a 422 rather
    than an uninterpretable row. The value arrives however the user typed it
    and is normalised per kind before storage; an empty value means "remove
    this document".
    """

    kind: TaxIdKind
    value: str = Field(..., max_length=120)


class TaxIdRead(BaseModel):
    kind: str
    value: str

    model_config = ConfigDict(from_attributes=True)


class PayeeCreate(BaseModel):
    name: str = Field(..., max_length=255)
    # Unset by default. A caller who does not know the legal nature says so
    # by omitting it, rather than being pushed into picking one.
    type: Optional[PayeeType] = None
    notes: Optional[str] = Field(None, max_length=1000)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    website: Optional[str] = Field(None, max_length=255)
    # An image URL the user chose; a website typed here becomes its favicon.
    # Wins over the provider's logo, which sync only fills in when empty.
    logo_url: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=50)
    tax_ids: list[TaxIdInput] = []

    @field_validator("logo_url")
    @classmethod
    def check_logo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_logo_url(value)


class PayeeUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    type: Optional[PayeeType] = None
    is_favorite: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=1000)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    website: Optional[str] = Field(None, max_length=255)
    # An image URL, or a website to take the favicon of; null or "" clears
    # it. Sync only fills an empty logo, so a URL set here stays.
    logo_url: Optional[str] = None
    # A name from the category icon set; null clears it, so the logo shows.
    icon: Optional[str] = Field(None, max_length=50)
    # Absent leaves the documents alone. Present replaces the whole set, so
    # the caller sends what should remain rather than a diff.
    tax_ids: Optional[list[TaxIdInput]] = None

    @field_validator("logo_url")
    @classmethod
    def check_logo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_logo_url(value)


class PayeeRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    type: Optional[PayeeType] = None

    @field_validator("type", mode="before")
    @classmethod
    def coerce_legacy_type(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ("person", "company"):
            return None
        return value

    # Read-only. Exposed so a client picker can tell the handful of
    # counterparties somebody entered on purpose from the hundreds sync
    # created from card descriptors.
    source: PayeeSource = "manual"
    is_favorite: bool
    notes: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    # The logo image: one the user set, else the merchant logo a provider
    # supplied.
    logo_url: Optional[str] = None
    icon: Optional[str] = None
    tax_ids: list[TaxIdRead] = []
    created_at: datetime
    transaction_count: int = 0

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_logo_url(self) -> Optional[str]:
        """The logo shown for this payee: its logo URL, else the website's favicon."""
        return self.logo_url or favicon_url_for(self.website)


class PayeeSummary(BaseModel):
    payee: PayeeRead
    total_spent: Decimal = Decimal("0")
    total_received: Decimal = Decimal("0")
    transaction_count: int = 0
    most_common_category: Optional[CategoryRead] = None
    last_transaction_date: Optional[_Date] = None


class PayeeMergeRequest(BaseModel):
    source_ids: list[uuid.UUID]
    target_id: uuid.UUID


class PayeeBulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID]
