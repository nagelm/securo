"""How a payee is drawn next to its transactions.

A transaction row shows, in order: the icon the user picked for its payee,
the payee's logo, or (neither) its category icon. The logo is the merchant
logo a provider supplied, else a favicon built from the payee's website.
"""
from typing import Optional

from app.models.payee import Payee
from app.providers.favicon import favicon_url_for


def payee_logo_url(payee: Optional[Payee]) -> Optional[str]:
    """The logo to show for a payee, or None when only an icon can be shown."""
    if payee is None:
        return None
    return payee.logo_url or favicon_url_for(payee.website)


def apply_payee_display(tx) -> None:
    """Fill a loaded transaction's payee_name, payee_logo_url and payee_icon."""
    payee = tx.payee_entity
    tx.payee_name = payee.name if payee else None
    tx.payee_logo_url = payee_logo_url(payee)
    tx.payee_icon = payee.icon if payee else None
