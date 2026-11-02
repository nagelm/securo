"""a foreign row's amount in its account's currency; TOTP secrets encrypted

Revision ID: f022
Revises: f021
Create Date: 2026-11-02

A row in another currency moved its account by `amount_primary`, which is in
the user's primary currency, not the account's. `amount_account` holds the
account-currency amount: the bank's conversion when the provider sends one,
else converted at the row's date. Existing foreign rows are backfilled here:
the stored primary amount when the account is in the owner's primary currency
(that is where bank conversions were kept), else from the stored rates.

`users.totp_secret` is stored encrypted, so the column widens and existing
secrets are encrypted in place.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.core.crypto import TOTP_SALT, decrypt, encrypt
from app.core.config import get_settings

revision: str = "f022"
down_revision: Union[str, None] = "f021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RATE = """(
    CASE WHEN {currency} = 'USD' THEN 1 ELSE (
        SELECT f.rate FROM fx_rates f
        WHERE f.base_currency = 'USD' AND f.quote_currency = {currency}
        ORDER BY (f.date > t.date), abs(f.date - t.date)
        LIMIT 1
    ) END
)"""
_BACKFILL = f"""
UPDATE transactions AS t
SET amount_account = CASE
    WHEN a.currency = COALESCE(u.preferences ->> 'currency_display', :default_currency)
         AND t.amount_primary IS NOT NULL
        THEN t.amount_primary
    ELSE round(t.amount * {_RATE.format(currency="a.currency")} / {_RATE.format(currency="t.currency")}, 2)
END
FROM accounts AS a
JOIN users AS u ON u.id = a.user_id
WHERE a.id = t.account_id AND t.currency <> a.currency
"""


def _rewrite_totp_secrets(bind, transform) -> None:
    rows = bind.execute(
        sa.text("SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL")
    ).fetchall()
    for user_id, secret in rows:
        bind.execute(
            sa.text("UPDATE users SET totp_secret = :secret WHERE id = :id"),
            {"secret": transform(secret, TOTP_SALT), "id": user_id},
        )


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("amount_account", sa.Numeric(precision=15, scale=2), nullable=True),
    )
    op.alter_column(
        "users", "totp_secret", type_=sa.String(255), existing_type=sa.String(32), existing_nullable=True
    )
    bind = op.get_bind()
    bind.execute(sa.text(_BACKFILL), {"default_currency": get_settings().default_currency})
    _rewrite_totp_secrets(bind, encrypt)


def downgrade() -> None:
    _rewrite_totp_secrets(op.get_bind(), decrypt)
    op.alter_column(
        "users", "totp_secret", type_=sa.String(32), existing_type=sa.String(255), existing_nullable=True
    )
    op.drop_column("transactions", "amount_account")
