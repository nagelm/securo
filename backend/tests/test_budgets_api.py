"""Tests for recurring budgets API."""
from datetime import date, timedelta

import pytest


def _current_month_str() -> str:
    return date.today().replace(day=1).isoformat()


def _prev_month_str() -> str:
    first = date.today().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    return prev.isoformat()


def _next_month_str() -> str:
    first = date.today().replace(day=1)
    if first.month == 12:
        return first.replace(year=first.year + 1, month=1).isoformat()
    return first.replace(month=first.month + 1).isoformat()


def _month_str(months_offset: int) -> str:
    """Return 1st of month N months from now (negative = past)."""
    d = date.today().replace(day=1)
    for _ in range(abs(months_offset)):
        if months_offset > 0:
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)
        else:
            if d.month == 1:
                d = d.replace(year=d.year - 1, month=12)
            else:
                d = d.replace(month=d.month - 1)
    return d.isoformat()


@pytest.mark.asyncio
async def test_create_budget_defaults_non_recurring(client, auth_headers, test_categories):
    """Creating a budget without is_recurring defaults to false (backward compat)."""
    cat = test_categories[0]
    response = await client.post(
        "/api/budgets",
        json={"category_id": str(cat.id), "amount": 500, "month": _current_month_str()},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["is_recurring"] is False
    assert float(data["amount"]) == 500.0


@pytest.mark.asyncio
async def test_create_recurring_budget(client, auth_headers, test_categories):
    """Creating a recurring budget sets is_recurring=true."""
    cat = test_categories[1]
    response = await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 300,
            "month": _current_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["is_recurring"] is True


@pytest.mark.asyncio
async def test_recurring_budget_appears_in_future_months(client, auth_headers, test_categories):
    """A recurring budget effective from current month should appear in future month listings."""
    cat = test_categories[0]
    # Create recurring budget for current month
    create_resp = await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 1000,
            "month": _current_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201

    # Check it appears for next month
    list_resp = await client.get(
        "/api/budgets",
        params={"month": _next_month_str()},
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    budgets = list_resp.json()
    cat_budgets = [b for b in budgets if b["category_id"] == str(cat.id)]
    assert len(cat_budgets) == 1
    assert float(cat_budgets[0]["amount"]) == 1000.0
    assert cat_budgets[0]["is_recurring"] is True


@pytest.mark.asyncio
async def test_override_wins_over_recurring(client, auth_headers, test_categories):
    """A month-specific override should take priority over a recurring budget."""
    cat = test_categories[1]
    # Create recurring
    await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 500,
            "month": _current_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )

    next_month = _next_month_str()

    # Create override for next month
    await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 750,
            "month": next_month,
            "is_recurring": False,
        },
        headers=auth_headers,
    )

    # List for next month — override should win
    list_resp = await client.get(
        "/api/budgets",
        params={"month": next_month},
        headers=auth_headers,
    )
    budgets = list_resp.json()
    cat_budgets = [b for b in budgets if b["category_id"] == str(cat.id)]
    assert len(cat_budgets) == 1
    assert float(cat_budgets[0]["amount"]) == 750.0
    assert cat_budgets[0]["is_recurring"] is False


@pytest.mark.asyncio
async def test_recurring_not_visible_before_effective_month(client, auth_headers, test_categories):
    """A recurring budget should NOT appear for months before its effective-from month."""
    cat = test_categories[0]
    # Create recurring budget effective from next month
    await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 800,
            "month": _next_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )

    # Should not appear for current month
    list_resp = await client.get(
        "/api/budgets",
        params={"month": _current_month_str()},
        headers=auth_headers,
    )
    budgets = list_resp.json()
    cat_budgets = [b for b in budgets if b["category_id"] == str(cat.id)]
    assert len(cat_budgets) == 0


@pytest.mark.asyncio
async def test_budget_comparison_resolves_recurring(client, auth_headers, test_categories, test_transactions):
    """Budget comparison endpoint should resolve recurring budgets correctly."""
    cat = test_categories[0]  # Alimentação — has spending in test_transactions

    # Create recurring budget
    await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 200,
            "month": _current_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )

    # Check comparison
    comp_resp = await client.get(
        "/api/budgets/comparison",
        params={"month": _current_month_str()},
        headers=auth_headers,
    )
    assert comp_resp.status_code == 200
    comparisons = comp_resp.json()
    cat_comp = [c for c in comparisons if c["category_id"] == str(cat.id)]
    assert len(cat_comp) == 1
    assert float(cat_comp[0]["budget_amount"]) == 200.0
    assert cat_comp[0]["is_recurring"] is True


@pytest.mark.asyncio
async def test_update_recurring_creates_new_record(client, auth_headers, test_categories):
    """Editing a recurring budget from a later month creates a new record; old one stays."""
    cat = test_categories[0]

    # Create recurring budget for current month
    create_resp = await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 400,
            "month": _current_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )
    original_id = create_resp.json()["id"]

    next_month = _next_month_str()

    # Update with a new effective month
    update_resp = await client.patch(
        f"/api/budgets/{original_id}",
        json={"amount": 600, "effective_month": next_month},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    new_data = update_resp.json()
    assert new_data["id"] != original_id  # New record created
    assert float(new_data["amount"]) == 600.0
    assert new_data["is_recurring"] is True

    # Current month should still show original amount
    list_resp = await client.get(
        "/api/budgets",
        params={"month": _current_month_str()},
        headers=auth_headers,
    )
    cat_budgets = [b for b in list_resp.json() if b["category_id"] == str(cat.id)]
    assert len(cat_budgets) == 1
    assert float(cat_budgets[0]["amount"]) == 400.0

    # Next month should show new amount
    list_resp2 = await client.get(
        "/api/budgets",
        params={"month": next_month},
        headers=auth_headers,
    )
    cat_budgets2 = [b for b in list_resp2.json() if b["category_id"] == str(cat.id)]
    assert len(cat_budgets2) == 1
    assert float(cat_budgets2[0]["amount"]) == 600.0


@pytest.mark.asyncio
async def test_delete_recurring_previous_takes_effect(client, auth_headers, test_categories):
    """Deleting a newer recurring budget makes an older one take effect again."""
    cat = test_categories[1]

    # Create recurring budget for current month
    await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 100,
            "month": _current_month_str(),
            "is_recurring": True,
        },
        headers=auth_headers,
    )

    next_month = _next_month_str()

    # Create newer recurring for next month
    create_resp = await client.post(
        "/api/budgets",
        json={
            "category_id": str(cat.id),
            "amount": 200,
            "month": next_month,
            "is_recurring": True,
        },
        headers=auth_headers,
    )
    newer_id = create_resp.json()["id"]

    # Next month should show 200
    list_resp = await client.get(
        "/api/budgets",
        params={"month": next_month},
        headers=auth_headers,
    )
    cat_budgets = [b for b in list_resp.json() if b["category_id"] == str(cat.id)]
    assert float(cat_budgets[0]["amount"]) == 200.0

    # Delete the newer one
    del_resp = await client.delete(f"/api/budgets/{newer_id}", headers=auth_headers)
    assert del_resp.status_code == 204

    # Next month should now fall back to 100
    list_resp2 = await client.get(
        "/api/budgets",
        params={"month": next_month},
        headers=auth_headers,
    )
    cat_budgets2 = [b for b in list_resp2.json() if b["category_id"] == str(cat.id)]
    assert len(cat_budgets2) == 1
    assert float(cat_budgets2[0]["amount"]) == 100.0


# ---------------------------------------------------------------------------
# Fork: end month and budget groups. Every figure is made up.
# ---------------------------------------------------------------------------


async def _post_budget(client, headers, cat, amount, month, **extra):
    resp = await client.post(
        "/api/budgets",
        json={"category_id": str(cat.id), "amount": amount, "month": month, **extra},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _budget_for(client, headers, cat, month):
    resp = await client.get("/api/budgets", params={"month": month}, headers=headers)
    return [b for b in resp.json() if b["category_id"] == str(cat.id)]


@pytest.mark.asyncio
async def test_recurring_budget_ends_after_its_end_month(client, auth_headers, test_categories):
    cat = test_categories[0]
    body = await _post_budget(client, auth_headers, cat, 200, _month_str(0), is_recurring=True,
                              end_month=_month_str(2))
    assert body["end_month"] == _month_str(2)

    assert [b["amount"] for b in await _budget_for(client, auth_headers, cat, _month_str(2))] == ["200.00"]
    assert await _budget_for(client, auth_headers, cat, _month_str(3)) == []
    # A later default still takes over from its own month.
    await _post_budget(client, auth_headers, cat, 300, _month_str(4), is_recurring=True)
    assert await _budget_for(client, auth_headers, cat, _month_str(3)) == []
    assert [b["amount"] for b in await _budget_for(client, auth_headers, cat, _month_str(5))] == ["300.00"]

    comp = await client.get("/api/budgets/comparison", params={"month": _month_str(1)}, headers=auth_headers)
    row = next(c for c in comp.json() if c["category_id"] == str(cat.id))
    assert row["end_month"] == _month_str(2)


@pytest.mark.asyncio
async def test_an_open_ended_budget_comes_back_after_a_temporary_one(client, auth_headers, test_categories):
    cat = test_categories[0]
    await _post_budget(client, auth_headers, cat, 500, _month_str(-1), is_recurring=True)
    await _post_budget(client, auth_headers, cat, 150, _month_str(1), is_recurring=True, end_month=_month_str(2))

    amounts = [
        [b["amount"] for b in await _budget_for(client, auth_headers, cat, _month_str(n))] for n in range(0, 5)
    ]
    assert amounts == [["500.00"], ["150.00"], ["150.00"], ["500.00"], ["500.00"]]
    comp = await client.get("/api/budgets/comparison", params={"month": _month_str(3)}, headers=auth_headers)
    row = next(c for c in comp.json() if c["category_id"] == str(cat.id))
    assert float(row["budget_amount"]) == 500 and row["end_month"] is None


@pytest.mark.asyncio
async def test_an_edit_from_a_later_month_with_an_end_month_ends_the_budget(client, auth_headers, test_categories):
    """The record an edit copies stops where the copy starts, so it does not
    come back after the copy's end month."""
    cat = test_categories[0]
    body = await _post_budget(client, auth_headers, cat, 200, _month_str(0), is_recurring=True)
    resp = await client.patch(
        f"/api/budgets/{body['id']}",
        json={"amount": 250, "effective_month": _month_str(2), "end_month": _month_str(3)},
        headers=auth_headers,
    )
    assert resp.status_code == 200 and resp.json()["id"] != body["id"]

    amounts = [
        [b["amount"] for b in await _budget_for(client, auth_headers, cat, _month_str(n))] for n in range(0, 5)
    ]
    assert amounts == [["200.00"], ["200.00"], ["250.00"], ["250.00"], []]


@pytest.mark.asyncio
async def test_end_month_validation_and_update(client, auth_headers, test_categories):
    cat = test_categories[0]
    for extra, message in [
        ({"is_recurring": False, "end_month": _month_str(1)}, "Only a recurring budget has an end month"),
        ({"is_recurring": True, "end_month": _month_str(-1)}, "End month is before the start month"),
    ]:
        resp = await client.post(
            "/api/budgets",
            json={"category_id": str(cat.id), "amount": 1, "month": _month_str(0), **extra},
            headers=auth_headers,
        )
        assert resp.status_code == 400 and resp.json()["detail"] == message

    body = await _post_budget(client, auth_headers, cat, 200, _month_str(0), is_recurring=True)
    resp = await client.patch(f"/api/budgets/{body['id']}", json={"end_month": _month_str(1)}, headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["end_month"] == _month_str(1)
    resp = await client.patch(f"/api/budgets/{body['id']}", json={"end_month": _month_str(-2)}, headers=auth_headers)
    assert resp.status_code == 400
    # A new amount from a later month keeps the end month; null clears it.
    resp = await client.patch(f"/api/budgets/{body['id']}", json={"amount": 250, "effective_month": _month_str(1)},
                              headers=auth_headers)
    assert resp.json()["end_month"] == _month_str(1) and resp.json()["id"] != body["id"]
    resp = await client.patch(f"/api/budgets/{body['id']}", json={"end_month": None}, headers=auth_headers)
    assert resp.json()["end_month"] is None


@pytest.mark.asyncio
async def test_budget_group_crud_and_comparison(client, auth_headers, test_categories, test_transactions):
    food, transport, _ = test_categories
    resp = await client.post("/api/budget-groups", json={
        "name": "Everyday", "amount": 100, "month": _month_str(0), "is_recurring": True,
        "category_ids": [str(food.id), str(transport.id)],
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    group = resp.json()
    assert set(group) >= {"id", "name", "amount", "month", "is_recurring", "end_month", "category_ids"}
    assert set(group["category_ids"]) == {str(food.id), str(transport.id)}

    # Spending this month: 45.00 food + 25.50 transport.
    comp = await client.get("/api/budget-groups/comparison", params={"month": _month_str(0)}, headers=auth_headers)
    assert comp.status_code == 200
    [row] = comp.json()
    assert row["id"] == group["id"] and float(row["amount"]) == 100
    assert float(row["actual_amount"]) == pytest.approx(70.5)
    assert row["percentage_used"] == pytest.approx(70.5)
    # A category's own budget still shows on its own row.
    await _post_budget(client, auth_headers, food, 60, _month_str(0), is_recurring=True)
    cat_rows = (await client.get("/api/budgets/comparison", params={"month": _month_str(0)},
                                 headers=auth_headers)).json()
    assert float(next(c for c in cat_rows if c["category_id"] == str(food.id))["budget_amount"]) == 60

    resp = await client.patch(f"/api/budget-groups/{group['id']}", json={
        "amount": 80, "category_ids": [str(transport.id)], "end_month": _month_str(1),
    }, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["category_ids"] == [str(transport.id)] and float(resp.json()["amount"]) == 80
    assert (await client.get("/api/budget-groups/comparison", params={"month": _month_str(2)},
                             headers=auth_headers)).json() == []
    assert len((await client.get("/api/budget-groups", headers=auth_headers)).json()) == 1
    assert len((await client.get("/api/budget-groups", params={"month": _month_str(2)},
                                 headers=auth_headers)).json()) == 0

    assert (await client.delete(f"/api/budget-groups/{group['id']}", headers=auth_headers)).status_code == 204
    assert (await client.delete(f"/api/budget-groups/{group['id']}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_a_group_cap_changed_from_a_later_month_applies_from_that_month(client, auth_headers, test_categories):
    food, transport, _ = test_categories
    group = (await client.post("/api/budget-groups", json={
        "name": "Everyday", "amount": 100, "month": _month_str(0), "is_recurring": True,
        "category_ids": [str(food.id), str(transport.id)],
    }, headers=auth_headers)).json()

    async def caps():
        out = []
        for n in range(0, 4):
            rows = (await client.get("/api/budget-groups/comparison", params={"month": _month_str(n)},
                                     headers=auth_headers)).json()
            out.append([float(r["amount"]) for r in rows])
        return out

    resp = await client.patch(f"/api/budget-groups/{group['id']}",
                              json={"amount": 80, "effective_month": _month_str(2)}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert await caps() == [[100], [100], [80], [80]]
    # Again from the same month: that month's cap changes, not a second one.
    await client.patch(f"/api/budget-groups/{group['id']}",
                       json={"amount": 90, "effective_month": _month_str(2)}, headers=auth_headers)
    assert await caps() == [[100], [100], [90], [90]]
    # From the group's own month, the first cap changes; later caps stay.
    await client.patch(f"/api/budget-groups/{group['id']}",
                       json={"amount": 120, "effective_month": _month_str(0)}, headers=auth_headers)
    assert await caps() == [[120], [120], [90], [90]]
    # Made a single month, the group has one cap.
    await client.patch(f"/api/budget-groups/{group['id']}",
                       json={"is_recurring": False}, headers=auth_headers)
    assert await caps() == [[120], [], [], []]


@pytest.mark.asyncio
async def test_a_category_is_in_one_budget_group_only(client, auth_headers, test_categories):
    food, transport, _ = test_categories
    base = {"amount": 100, "month": _month_str(0)}
    resp = await client.post("/api/budget-groups", json={**base, "name": "A", "category_ids": [str(food.id)]},
                             headers=auth_headers)
    assert resp.status_code == 201
    resp = await client.post("/api/budget-groups", json={
        **base, "name": "B", "category_ids": [str(transport.id), str(food.id)],
    }, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == f"{food.name} is already in the budget group A"
    b = (await client.post("/api/budget-groups", json={**base, "name": "B", "category_ids": [str(transport.id)]},
                           headers=auth_headers)).json()
    resp = await client.patch(f"/api/budget-groups/{b['id']}", json={"category_ids": [str(food.id)]},
                              headers=auth_headers)
    assert resp.status_code == 400
    for payload in (
        {**base, "name": "C", "category_ids": []},
        {**base, "name": "", "category_ids": [str(transport.id)]},
    ):
        assert (await client.post("/api/budget-groups", json=payload, headers=auth_headers)).status_code == 422
    resp = await client.post("/api/budget-groups", json={
        **base, "name": "C", "category_ids": ["00000000-0000-0000-0000-000000000000"],
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Category not found"
    resp = await client.post("/api/budget-groups", json={
        **base, "name": "C", "is_recurring": False, "end_month": _month_str(1),
        "category_ids": [str(test_categories[2].id)],
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Only a recurring budget has an end month"


@pytest.mark.asyncio
async def test_budget_groups_viewer_can_read_but_not_write(client, viewer_auth_headers):
    assert (await client.get("/api/budget-groups", headers=viewer_auth_headers)).status_code == 200
    resp = await client.post("/api/budget-groups", json={
        "name": "X", "amount": 1, "month": _month_str(0), "category_ids": ["00000000-0000-0000-0000-000000000000"],
    }, headers=viewer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_deleting_a_category_moves_or_drops_its_group_membership(client, auth_headers, test_categories):
    food, transport, income = test_categories
    group = (await client.post("/api/budget-groups", json={
        "name": "Everyday", "amount": 100, "month": _month_str(0), "category_ids": [str(food.id)],
    }, headers=auth_headers)).json()

    # Moved into a category in no group: it joins the group.
    resp = await client.delete(f"/api/categories/{food.id}", params={"transfer_to_category_id": str(transport.id)},
                               headers=auth_headers)
    assert resp.status_code == 204, resp.text
    groups = (await client.get("/api/budget-groups", headers=auth_headers)).json()
    assert groups[0]["category_ids"] == [str(transport.id)]

    # Moved into a category already in a group: the membership goes.
    other = (await client.post("/api/budget-groups", json={
        "name": "Income", "amount": 1, "month": _month_str(0), "category_ids": [str(income.id)],
    }, headers=auth_headers)).json()
    resp = await client.delete(f"/api/categories/{transport.id}", params={"transfer_to_category_id": str(income.id)},
                               headers=auth_headers)
    assert resp.status_code == 204
    by_id = {g["id"]: g for g in (await client.get("/api/budget-groups", headers=auth_headers)).json()}
    assert by_id[group["id"]]["category_ids"] == []
    assert by_id[other["id"]]["category_ids"] == [str(income.id)]
