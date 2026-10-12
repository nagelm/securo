"""payee logo and icon

Two nullable columns on `payees`:

- logo_url: the merchant logo a provider supplied (Akahu `meta.logo` on a
  row it matched to a merchant). Sync fills it on payees that have none.
- icon: an icon the user picked; it wins over the logo on transaction rows.

Payees that already have synced transactions get their logo from the most
recent one carrying a real merchant logo, using the same rule as
`app.providers.akahu.merchant_logo_for`: a merchant id, an https URL, and
not Akahu's `default.png` placeholder.

Revision ID: 097
Revises: 096
"""
import sqlalchemy as sa
from alembic import op

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payees", sa.Column("logo_url", sa.String(500), nullable=True))
    op.add_column("payees", sa.Column("icon", sa.String(50), nullable=True))
    op.execute(
        """
        UPDATE payees p
        SET logo_url = left(src.logo, 500)
        FROM (
            SELECT DISTINCT ON (t.payee_id)
                t.payee_id, t.raw_data -> 'meta' ->> 'logo' AS logo
            FROM transactions t
            WHERE t.payee_id IS NOT NULL
              AND t.raw_data -> 'merchant' ->> '_id' IS NOT NULL
              AND t.raw_data -> 'meta' ->> 'logo' LIKE 'https://%'
              AND lower(split_part(t.raw_data -> 'meta' ->> 'logo', '?', 1))
                  NOT LIKE '%/default.png'
              AND lower(split_part(t.raw_data -> 'meta' ->> 'logo', '?', 1))
                  NOT LIKE '%/default'
            ORDER BY t.payee_id, t.date DESC
        ) src
        WHERE p.id = src.payee_id AND p.logo_url IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("payees", "icon")
    op.drop_column("payees", "logo_url")
