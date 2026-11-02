"""remember which default each seeded category came from

Revision ID: f009
Revises: f008
Create Date: 2026-10-13

`categories.default_key` is the DEFAULT_CATEGORIES_I18N key a category was
seeded from ("transfers", "loan_repayments", ...), NULL for one the user made.
Code that needs a default (provider sync hints, transfer-pair and loan
repayment categorisation, rule packs) finds it by this key, so renaming the
category no longer loses it. One category per key in a workspace (a partial
unique index on the keyed rows).

Existing categories get the key whose translated name they carry, matched
exactly as the app matched defaults until now: per workspace and key, the
first such category by id. A renamed default has already lost its name, so it
gets no key and stays a plain category. Downgrade drops the column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f009"
down_revision: Union[str, None] = "f008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX = "ux_categories_workspace_default_key"

# Snapshot of every translated name per default key at this revision. A
# migration must not follow later edits to the app's template.
NAMES: dict[str, tuple[str, ...]] = {
    "housing": ("Housing", "Moradia", "Habitação", "Wohnen", "Logement", "Wonen", "Bývanie"),
    "food": ("Food & Dining", "Alimentação", "Essen & Trinken", "Alimentation & Restaurants", "Eten & Drinken", "Jedlo a reštaurácie"),
    "transport": ("Transport", "Transporte", "Transportes", "Doprava"),
    "groceries": ("Groceries", "Mercado", "Supermercado", "Lebensmittel", "Courses", "Boodschappen", "Potraviny"),
    "health": ("Health", "Saúde", "Gesundheit", "Santé", "Gezondheid", "Zdravie"),
    "leisure": ("Leisure", "Lazer", "Freizeit", "Loisirs", "Vrije tijd", "Voľný čas"),
    "subscriptions": ("Subscriptions", "Assinaturas", "Subscrições", "Abonnements", "Abonnementen", "Predplatné"),
    "education": ("Education", "Educação", "Bildung", "Éducation", "Educatie", "Vzdelávanie"),
    "transfers": ("Transfers", "Transferências", "Umbuchungen", "Virements", "Overboekingen", "Prevody"),
    "loan_repayments": ("Loan repayments", "Pagamentos de empréstimos", "Amortizações de empréstimos", "Kredittilgungen", "Remboursements de prêts", "Aflossingen", "Splátky úverov"),
    "investments": ("Investments", "Investimentos", "Investitionen", "Investissements", "Investeringen", "Investície"),
    "salary": ("Salary & Income", "Salário & Renda", "Salário & Rendimentos", "Gehalt & Einnahmen", "Salaire & Revenus", "Salaris & Inkomen", "Mzda a príjmy"),
    "shopping": ("Shopping", "Compras", "Achats", "Winkelen", "Nákupy"),
    "donations": ("Donations", "Doações", "Donativos", "Spenden", "Dons", "Donaties", "Dary"),
    "personal_care": ("Personal Care", "Cuidados Pessoais", "Körperpflege", "Soins personnels", "Persoonlijke verzorging", "Osobná starostlivosť"),
    "taxes": ("Taxes & Fees", "Impostos & Taxas", "Steuern & Gebühren", "Impôts & Taxes", "Belastingen & Heffingen", "Dane a poplatky"),
    "other": ("Other", "Outros", "Sonstiges", "Autres", "Overig", "Ostatné"),
}

_uuid = postgresql.UUID(as_uuid=True)
categories = sa.table(
    "categories",
    sa.column("id", _uuid),
    sa.column("workspace_id", _uuid),
    sa.column("name", sa.String),
    sa.column("default_key", sa.String),
)


def upgrade() -> None:
    op.add_column("categories", sa.Column("default_key", sa.String(length=50), nullable=True))

    bind = op.get_bind()
    key_by_name = {name: key for key, names in NAMES.items() for name in names}
    claimed: set[tuple] = set()
    for row in bind.execute(
        sa.select(categories.c.id, categories.c.workspace_id, categories.c.name)
        .where(categories.c.name.in_(list(key_by_name)))
        .order_by(categories.c.id)
    ):
        key = key_by_name[row.name]
        if (row.workspace_id, key) in claimed:
            continue
        claimed.add((row.workspace_id, key))
        bind.execute(categories.update().where(categories.c.id == row.id).values(default_key=key))

    op.create_index(
        INDEX,
        "categories",
        ["workspace_id", "default_key"],
        unique=True,
        postgresql_where=sa.text("default_key IS NOT NULL"),
        sqlite_where=sa.text("default_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="categories")
    op.drop_column("categories", "default_key")
