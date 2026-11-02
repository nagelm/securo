"""Fork: the net worth equivalence tests again on real PostgreSQL, where the
balance sums are exact numerics and UUIDs order natively. Every figure is
made up."""
import pytest
import pytest_asyncio

from tests.test_net_worth_batch import (  # noqa: F401  (collected here on PostgreSQL)
    fx_history_mode,
    test_report_matches_the_per_point_reference,
    test_single_snapshot_matches_the_reference,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user/data fixtures start in a fresh per-test PostgreSQL schema."""
