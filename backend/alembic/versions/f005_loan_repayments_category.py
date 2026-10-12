"""seed the Loan repayments default category in existing workspaces

Revision ID: f005
Revises: f004
Create Date: 2026-10-13

New workspaces get "Loan repayments" from the default category template
(`category_service.DEFAULT_CATEGORIES_I18N["loan_repayments"]`). Workspaces
seeded before it existed get it here, the way 033 seeded Investments:

  * flagged `treat_as_transfer`, so principal in it never reaches P&L, even
    on a leg that has no pair;
  * named in the language the workspace's defaults were seeded in (guessed
    from the names of its default categories, English when unclear);
  * in the same group as the workspace's Transfers category, else its
    "Other" group, else none.

Skipped: a workspace with no categories at all (it has not been seeded yet,
and a category here would stop the defaults being seeded later) and one that
already has a category with any of the translated names. No transaction is
touched; recategorising existing repayment legs is left to the owner.

Each category made here has a fixed id derived from its workspace, so the
downgrade removes exactly those, and only the ones nothing refers to.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f005"
down_revision: Union[str, None] = "f004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Snapshot of the template at this revision. A migration must not follow
# later edits to the app's template.
NAMES = {
    "en": "Loan repayments",
    "pt-BR": "Pagamentos de empréstimos",
    "pt-PT": "Amortizações de empréstimos",
    "de": "Kredittilgungen",
    "fr": "Remboursements de prêts",
    "nl": "Aflossingen",
    "sk": "Splátky úverov",
}
ICON = "trending-down"
COLOR = "#64748B"

# Default names (Transfers, Housing, Groceries, Other) that tell the seeding
# language apart. Transfers comes first: it is also the group anchor.
LANGUAGE_MARKERS = {
    "en": ("Transfers", "Housing", "Groceries", "Other"),
    "pt-BR": ("Transferências", "Moradia", "Mercado", "Outros"),
    "pt-PT": ("Transferências", "Habitação", "Supermercado", "Outros"),
    "de": ("Umbuchungen", "Wohnen", "Lebensmittel", "Sonstiges"),
    "fr": ("Virements", "Logement", "Courses", "Autres"),
    "nl": ("Overboekingen", "Wonen", "Boodschappen", "Overig"),
    "sk": ("Prevody", "Bývanie", "Potraviny", "Ostatné"),
}
OTHER_GROUP_NAMES = (
    "Other", "Outros", "Sonstiges", "Autres", "Overig", "Ostatné",
    "Altro", "Inne", "Otros", "Другое", "Інше",
)

_NAMESPACE = uuid.UUID("5d0c3a52-6f35-4e1f-9a43-2f0f7b3c6a05")

_uuid = postgresql.UUID(as_uuid=True)
workspaces = sa.table("workspaces", sa.column("id", _uuid), sa.column("created_by_user_id", _uuid))
members = sa.table(
    "workspace_members", sa.column("workspace_id", _uuid), sa.column("user_id", _uuid), sa.column("role", sa.String)
)
categories = sa.table(
    "categories",
    sa.column("id", _uuid),
    sa.column("user_id", _uuid),
    sa.column("workspace_id", _uuid),
    sa.column("group_id", _uuid),
    sa.column("name", sa.String),
    sa.column("icon", sa.String),
    sa.column("color", sa.String),
    sa.column("is_system", sa.Boolean),
    sa.column("is_hidden", sa.Boolean),
    sa.column("treat_as_transfer", sa.Boolean),
    sa.column("is_ignored", sa.Boolean),
)
groups = sa.table(
    "category_groups", sa.column("id", _uuid), sa.column("workspace_id", _uuid), sa.column("name", sa.String)
)
transactions = sa.table("transactions", sa.column("category_id", _uuid))
budgets = sa.table("budgets", sa.column("category_id", _uuid))
recurring = sa.table(
    "recurring_transactions", sa.column("category_id", _uuid), sa.column("interest_category_id", _uuid)
)
rules = sa.table("rules", sa.column("actions", sa.Text))


def category_id_for(workspace_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"loan-repayments:{workspace_id}")


def _language(names: set[str]) -> str:
    scores = {lang: len(names & set(markers)) for lang, markers in LANGUAGE_MARKERS.items()}
    best = max(scores.values())
    if best == 0 or scores["en"] == best:
        return "en"
    return next(lang for lang, score in scores.items() if score == best)


def upgrade() -> None:
    bind = op.get_bind()
    wanted = {name.lower() for name in NAMES.values()}
    by_workspace: dict[uuid.UUID, list] = {}
    for row in bind.execute(
        sa.select(categories.c.workspace_id, categories.c.name, categories.c.user_id, categories.c.group_id)
    ):
        by_workspace.setdefault(row.workspace_id, []).append(row)

    for ws in bind.execute(sa.select(workspaces.c.id, workspaces.c.created_by_user_id)).all():
        rows = by_workspace.get(ws.id)
        if not rows or any(r.name.lower() in wanted for r in rows):
            continue
        names = {r.name for r in rows}
        lang = _language(names)
        transfers_names = {markers[0] for markers in LANGUAGE_MARKERS.values()}
        transfers = next((r for r in rows if r.name in transfers_names), None)

        group_id = transfers.group_id if transfers is not None else None
        if group_id is None:
            group_id = bind.execute(
                sa.select(groups.c.id)
                .where(groups.c.workspace_id == ws.id, groups.c.name.in_(OTHER_GROUP_NAMES))
                .order_by(groups.c.id)
                .limit(1)
            ).scalar()

        user_id = transfers.user_id if transfers is not None else ws.created_by_user_id
        if user_id is None:
            user_id = bind.execute(
                sa.select(members.c.user_id)
                .where(members.c.workspace_id == ws.id, members.c.role == "owner")
                .order_by(members.c.user_id)
                .limit(1)
            ).scalar()
        if user_id is None:
            continue

        bind.execute(
            categories.insert().values(
                id=category_id_for(ws.id),
                user_id=user_id,
                workspace_id=ws.id,
                group_id=group_id,
                name=NAMES[lang],
                icon=ICON,
                color=COLOR,
                is_system=True,
                is_hidden=False,
                treat_as_transfer=True,
                is_ignored=False,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for (ws_id,) in bind.execute(sa.select(workspaces.c.id)).all():
        cat_id = category_id_for(ws_id)
        used = bind.execute(
            sa.select(
                sa.exists().where(transactions.c.category_id == cat_id)
                | sa.exists().where(budgets.c.category_id == cat_id)
                | sa.exists().where(
                    (recurring.c.category_id == cat_id) | (recurring.c.interest_category_id == cat_id)
                )
                | sa.exists().where(sa.cast(rules.c.actions, sa.Text).like(f"%{cat_id}%"))
            )
        ).scalar()
        if not used:
            bind.execute(categories.delete().where(categories.c.id == cat_id))
