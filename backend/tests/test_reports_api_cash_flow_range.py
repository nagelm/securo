"""Fork: the cash-flow report takes up to ten years ahead."""
import pytest


@pytest.mark.asyncio
async def test_cash_flow_takes_ten_years(client, auth_headers):
    ok = await client.get("/api/reports/cash-flow?months=120&interval=monthly", headers=auth_headers)
    assert ok.status_code == 200
    too_long = await client.get("/api/reports/cash-flow?months=121&interval=monthly", headers=auth_headers)
    assert too_long.status_code == 422
