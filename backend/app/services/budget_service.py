import uuid
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.budget import Budget
from app.models.budget_group import BudgetGroup, BudgetGroupCategory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.budget import (
    BudgetCreate,
    BudgetGroupCreate,
    BudgetGroupUpdate,
    BudgetGroupVsActual,
    BudgetUpdate,
    BudgetVsActual,
)
from app.services._query_filters import (
    counts_as_user_pnl,
    owner_split_offset_by_category,
    reporting_date_col,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.dashboard_service import (
    _counts_as_user_pnl_row,
    _get_forecast_transactions,
    _get_recurring_projections,
)
from app.services.fx_rate_service import convert
from app.core.config import get_settings


def _primary_amount_expr():
    """Amount in primary currency: uses amount_primary when available, falls back to amount."""
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


def resolve_budgets(budgets: Iterable[Budget], month_start: date) -> dict[str, Budget]:
    """The budget that applies to each category (by id string) in a month.

    Resolution order:
    1. Month-specific override (is_recurring=false, month=M) takes priority
    2. Most recent recurring default (is_recurring=true, month<=M) as fallback

    Fork: a recurring default whose ``end_month`` is before M no longer
    applies, and an earlier default does not come back in its place; a
    later default still takes over from its own month.
    """
    latest: dict[str, Budget] = {}
    overrides: dict[str, Budget] = {}
    for b in budgets:
        cat_id = str(b.category_id)
        if b.is_recurring:
            if b.month <= month_start and (cat_id not in latest or b.month > latest[cat_id].month):
                latest[cat_id] = b
        elif b.month == month_start:
            overrides[cat_id] = b
    resolved = {
        cat_id: b for cat_id, b in latest.items()
        if b.end_month is None or month_start <= b.end_month
    }
    resolved.update(overrides)
    return resolved


async def _budgets_up_to(session: AsyncSession, workspace_id: uuid.UUID, month_start: date) -> list[Budget]:
    """Every budget that could apply in or after ``month_start``'s month."""
    result = await session.execute(
        select(Budget).where(Budget.workspace_id == workspace_id, Budget.month <= month_start)
    )
    return list(result.scalars().all())


async def _build_budget_map(
    session: AsyncSession, workspace_id: uuid.UUID, month_start: date
) -> dict[str, tuple[Decimal, bool]]:
    """Build a map of category_id -> (amount, is_recurring) for the given month
    (see ``resolve_budgets``)."""
    resolved = resolve_budgets(await _budgets_up_to(session, workspace_id, month_start), month_start)
    return {cat_id: (b.amount, b.is_recurring) for cat_id, b in resolved.items()}


async def get_budgets(
    session: AsyncSession, workspace_id: uuid.UUID, month: Optional[date] = None
) -> list[Budget]:
    if not month:
        query = select(Budget).where(Budget.workspace_id == workspace_id)
        result = await session.execute(query.order_by(Budget.month.desc()))
        return list(result.scalars().all())

    month_start = month.replace(day=1)
    resolved = resolve_budgets(await _budgets_up_to(session, workspace_id, month_start), month_start)
    return sorted(resolved.values(), key=lambda b: b.month, reverse=True)


def _check_end_month(month: date, is_recurring: bool, end_month: Optional[date]) -> Optional[date]:
    """Fork: an end month belongs to a recurring budget and is not before its start."""
    if end_month is None:
        return None
    end_month = end_month.replace(day=1)
    if not is_recurring:
        raise ValueError("Only a recurring budget has an end month")
    if end_month < month:
        raise ValueError("End month is before the start month")
    return end_month


async def get_budget(
    session: AsyncSession, budget_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Budget]:
    result = await session.execute(
        select(Budget).where(Budget.id == budget_id, Budget.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def create_budget(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BudgetCreate,
) -> Budget:
    month = data.month.replace(day=1)
    budget = Budget(
        user_id=user_id,
        workspace_id=workspace_id,
        category_id=data.category_id,
        amount=data.amount,
        month=month,
        is_recurring=data.is_recurring,
        end_month=_check_end_month(month, data.is_recurring, data.end_month),
    )
    session.add(budget)
    await session.commit()
    await session.refresh(budget)
    return budget


async def update_budget(
    session: AsyncSession, budget_id: uuid.UUID, workspace_id: uuid.UUID, data: BudgetUpdate
) -> Optional[Budget]:
    budget = await get_budget(session, budget_id, workspace_id)
    if not budget:
        return None

    end_given = "end_month" in data.model_fields_set
    if budget.is_recurring and data.effective_month:
        effective = data.effective_month.replace(day=1)
        if effective != budget.month:
            # Create a new recurring record with new effective-from month
            new_budget = Budget(
                user_id=budget.user_id,
                workspace_id=budget.workspace_id,
                category_id=budget.category_id,
                amount=data.amount if data.amount is not None else budget.amount,
                month=effective,
                is_recurring=True,
                # Fork: the end month carries over unless one is given.
                end_month=_check_end_month(
                    effective, True, data.end_month if end_given else budget.end_month
                ),
            )
            session.add(new_budget)
            await session.commit()
            await session.refresh(new_budget)
            return new_budget

    # Update in place (non-recurring, or same effective-from month)
    changes = data.model_dump(exclude_unset=True, exclude={"effective_month"})
    if "end_month" in changes:
        changes["end_month"] = _check_end_month(budget.month, budget.is_recurring, changes["end_month"])
    for key, value in changes.items():
        setattr(budget, key, value)

    await session.commit()
    await session.refresh(budget)
    return budget


async def delete_budget(
    session: AsyncSession, budget_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    budget = await get_budget(session, budget_id, workspace_id)
    if not budget:
        return False

    await session.delete(budget)
    await session.commit()
    return True


def _month_bounds(month: Optional[date]) -> tuple[date, date]:
    """First day of ``month`` (default: this month) and of the month after."""
    if not month:
        month = app_today().replace(day=1)
    month_start = month.replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
    return month_start, month_end


async def _spending_by_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    accounting_mode: str,
    range_start: date,
    range_end: date,
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """Net spending per category (id string) over ``[range_start, range_end)``:
    the actual amount, and the projected one (actual plus the recurring and
    pending/future rows still to come). Fork: shared by category budgets and
    budget groups."""
    from app.services._query_filters import viewer_shared_spending_by_category

    report_date = reporting_date_col(accounting_mode)

    # Get actual spending by category for this month (exclude transfer pairs)
    # Net spending = Debits - Credits (refunds offset expenses)
    # Use amount_primary for multi-currency support
    spending_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(
                case(
                    (Transaction.type == "debit", _primary_amount_expr()),
                    (Transaction.type == "credit", -_primary_amount_expr()),
                    else_=0,
                )
            ),
        )
        .where(
            Transaction.workspace_id == workspace_id,
            report_date >= range_start,
            report_date < range_end,
            Transaction.category_id.isnot(None),
            report_date <= app_today(),
            Transaction.status == "posted",
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id)
    )
    spending_map: dict[str, Decimal] = {}
    for row in spending_result.all():
        net = row[1] or Decimal("0")
        if net > 0:
            spending_map[str(row[0])] = net

    # Subtract non-owner shares of own splits — only the user's share counts.
    own_offset = await owner_split_offset_by_category(
        session, user_id, range_start, range_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
        workspace_id=workspace_id,
    )
    for cat_uuid, total in own_offset.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        if cat_id in spending_map:
            spending_map[cat_id] -= Decimal(str(total))
            if spending_map[cat_id] <= 0:
                spending_map.pop(cat_id)

    # Layer in the user's share from group splits — concert tickets
    # paid by a friend are still the user's expense in the budget
    # picture for the matching category. FX-convert per currency to
    # match the rest of the budget (everything else is in primary).
    shared_by_cat = await viewer_shared_spending_by_category(
        session, user_id, range_start, range_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
    )
    for cat_uuid, total in shared_by_cat.items():
        if cat_uuid is None:
            continue
        cat_id = str(cat_uuid)
        spending_map[cat_id] = spending_map.get(cat_id, Decimal("0")) + Decimal(str(total))

    projected_spending_map = dict(spending_map)

    # Add projected recurring transactions for this month (converted to primary currency)
    projections = await _get_recurring_projections(session, workspace_id, range_start, range_end)
    for proj in projections:
        if proj["type"] != "debit" or not proj["category_id"]:
            continue
        cat_id = str(proj["category_id"])
        converted, _ = await convert(
            session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
        )
        projected_spending_map[cat_id] = projected_spending_map.get(cat_id, Decimal("0")) + converted

    forecast_transactions = await _get_forecast_transactions(
        session, workspace_id, range_start, range_end,
        range_date_col=report_date,
    )
    for tx in forecast_transactions:
        if tx.type != "debit" or not tx.category_id or not _counts_as_user_pnl_row(tx):
            continue
        cat_id = str(tx.category_id)
        amount = tx.amount_primary
        if amount is not None:
            converted = abs(Decimal(str(amount)))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
            )
        projected_spending_map[cat_id] = projected_spending_map.get(cat_id, Decimal("0")) + converted

    return spending_map, projected_spending_map


async def _primary_and_mode(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, str]:
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    return primary_currency, await get_credit_card_accounting_mode(session)


async def get_budget_vs_actual(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
) -> list[BudgetVsActual]:
    month_start, month_end = _month_bounds(month)

    # Previous month range
    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1)
    prev_month_end = month_start

    # Get all categories for this workspace with their groups
    cats_result = await session.execute(
        select(Category, CategoryGroup)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(Category.workspace_id == workspace_id)
    )
    all_categories = cats_result.all()

    if not all_categories:
        return []

    # Get budgets for this month (with recurring resolution)
    budget_map = resolve_budgets(await _budgets_up_to(session, workspace_id, month_start), month_start)

    # Get user's primary currency for FX conversion + reporting mode
    primary_currency, accounting_mode = await _primary_and_mode(session, user_id)

    spending_map, projected_spending_map = await _spending_by_category(
        session, workspace_id, user_id, primary_currency, accounting_mode, month_start, month_end,
    )
    # The previous month the same way, so the trend comparison is apples-to-apples.
    prev_spending_map, projected_prev_spending_map = await _spending_by_category(
        session, workspace_id, user_id, primary_currency, accounting_mode, prev_month_start, prev_month_end,
    )

    comparisons = []
    for category, group in all_categories:
        cat_id = str(category.id)
        actual = spending_map.get(cat_id, Decimal("0"))
        projected = projected_spending_map.get(cat_id, Decimal("0"))
        prev_actual = prev_spending_map.get(cat_id, Decimal("0"))
        projected_prev = projected_prev_spending_map.get(cat_id, Decimal("0"))
        budget_entry = budget_map.get(cat_id)
        budget_amount = budget_entry.amount if budget_entry else None
        is_recurring = budget_entry.is_recurring if budget_entry else False

        # Skip categories with no spending in either month and no budget
        if actual == 0 and projected == 0 and prev_actual == 0 and projected_prev == 0 and budget_amount is None:
            continue

        percentage = None
        if budget_amount and budget_amount > 0:
            percentage = round(float(projected / budget_amount * 100), 1)

        comparisons.append(BudgetVsActual(
            category_id=category.id,
            category_name=category.name,
            category_icon=category.icon,
            category_color=category.color,
            group_id=group.id if group else None,
            group_name=group.name if group else None,
            budget_amount=budget_amount,
            actual_amount=actual,
            projected_amount=projected,
            prev_month_amount=prev_actual,
            projected_prev_month_amount=projected_prev,
            percentage_used=percentage,
            is_recurring=is_recurring,
            end_month=budget_entry.end_month if budget_entry else None,
        ))

    return sorted(comparisons, key=lambda x: float(x.actual_amount), reverse=True)


# ---------------------------------------------------------------------------
# Fork: budget groups
# ---------------------------------------------------------------------------


class BudgetGroupConflict(ValueError):
    """A category is already in another budget group."""


async def list_budget_groups(
    session: AsyncSession, workspace_id: uuid.UUID, month: Optional[date] = None
) -> list[BudgetGroup]:
    """Every group, or with ``month`` the groups whose cap holds that month."""
    result = await session.execute(
        select(BudgetGroup)
        .where(BudgetGroup.workspace_id == workspace_id)
        .order_by(BudgetGroup.name, BudgetGroup.created_at)
    )
    groups = list(result.scalars().all())
    if month is not None:
        groups = [g for g in groups if g.applies_to(month.replace(day=1))]
    return groups


async def get_budget_group(
    session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[BudgetGroup]:
    result = await session.execute(
        select(BudgetGroup).where(BudgetGroup.id == group_id, BudgetGroup.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def _check_group_categories(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    category_ids: list[uuid.UUID],
    group_id: Optional[uuid.UUID],
) -> list[uuid.UUID]:
    """The members, deduplicated: each a category of the workspace and in no
    other group."""
    ids = list(dict.fromkeys(category_ids))
    if not ids:
        raise ValueError("A budget group needs at least one category")
    found = set(
        (
            await session.execute(
                select(Category.id).where(Category.id.in_(ids), Category.workspace_id == workspace_id)
            )
        ).scalars()
    )
    if len(found) != len(ids):
        raise ValueError("Category not found")
    stmt = (
        select(Category.name, BudgetGroup.name)
        .join(BudgetGroupCategory, BudgetGroupCategory.category_id == Category.id)
        .join(BudgetGroup, BudgetGroup.id == BudgetGroupCategory.group_id)
        .where(BudgetGroupCategory.category_id.in_(ids))
    )
    if group_id is not None:
        stmt = stmt.where(BudgetGroupCategory.group_id != group_id)
    taken = (await session.execute(stmt)).first()
    if taken is not None:
        raise BudgetGroupConflict(f"{taken[0]} is already in the budget group {taken[1]}")
    return ids


async def create_budget_group(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, data: BudgetGroupCreate
) -> BudgetGroup:
    ids = await _check_group_categories(session, workspace_id, data.category_ids, None)
    month = data.month.replace(day=1)
    group = BudgetGroup(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        amount=data.amount,
        month=month,
        is_recurring=data.is_recurring,
        end_month=_check_end_month(month, data.is_recurring, data.end_month),
        members=[BudgetGroupCategory(category_id=cat_id) for cat_id in ids],
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def update_budget_group(
    session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID, data: BudgetGroupUpdate
) -> Optional[BudgetGroup]:
    """Changes the group in place (no effective-from copy as category budgets
    have): a category is in one group only, so a group is one row."""
    group = await get_budget_group(session, group_id, workspace_id)
    if group is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    for required in ("name", "amount", "month", "is_recurring"):
        if required in changes and changes[required] is None:
            raise ValueError(f"{required} is required")
    category_ids = changes.pop("category_ids", None)
    if category_ids is not None:
        ids = await _check_group_categories(session, workspace_id, category_ids, group.id)
        keep = {m.category_id: m for m in group.members}
        group.members = [keep.get(cat_id) or BudgetGroupCategory(category_id=cat_id) for cat_id in ids]
    month = changes["month"].replace(day=1) if changes.get("month") else group.month
    is_recurring = changes.get("is_recurring", group.is_recurring)
    end_month = changes.get("end_month", group.end_month)
    if not is_recurring and "end_month" not in changes:
        end_month = None  # a single month has no end month
    changes.update(month=month, end_month=_check_end_month(month, is_recurring, end_month))
    for key, value in changes.items():
        setattr(group, key, value)
    await session.commit()
    await session.refresh(group)
    return group


async def delete_budget_group(session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
    group = await get_budget_group(session, group_id, workspace_id)
    if group is None:
        return False
    await session.delete(group)
    await session.commit()
    return True


async def get_budget_group_vs_actual(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
) -> list[BudgetGroupVsActual]:
    """Each group whose cap holds in the month, against the summed spending of
    its member categories (the same figures as their category rows)."""
    month_start, month_end = _month_bounds(month)
    groups = await list_budget_groups(session, workspace_id, month_start)
    if not groups:
        return []
    primary_currency, accounting_mode = await _primary_and_mode(session, user_id)
    spending_map, projected_spending_map = await _spending_by_category(
        session, workspace_id, user_id, primary_currency, accounting_mode, month_start, month_end,
    )
    out: list[BudgetGroupVsActual] = []
    for group in groups:
        members = [str(cat_id) for cat_id in group.category_ids]
        actual = sum((spending_map.get(c, Decimal("0")) for c in members), Decimal("0"))
        projected = sum((projected_spending_map.get(c, Decimal("0")) for c in members), Decimal("0"))
        out.append(BudgetGroupVsActual(
            id=group.id,
            name=group.name,
            amount=group.amount,
            month=group.month,
            is_recurring=group.is_recurring,
            end_month=group.end_month,
            category_ids=group.category_ids,
            actual_amount=actual,
            projected_amount=projected,
            percentage_used=(
                round(float(projected / group.amount * 100), 1) if group.amount and group.amount > 0 else None
            ),
        ))
    return out


async def load_budget_plan(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[list[Budget], list[BudgetGroup]]:
    """Fork: every category budget and budget group of the workspace, for
    resolving month by month (the Forecast's spending estimate)."""
    budgets = list(
        (await session.execute(select(Budget).where(Budget.workspace_id == workspace_id))).scalars()
    )
    return budgets, await list_budget_groups(session, workspace_id)
