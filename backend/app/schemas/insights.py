"""Fork: the Insights page's monthly briefing."""
from typing import Any, Literal, Optional

from pydantic import BaseModel


class InsightDriver(BaseModel):
    payee_id: Optional[str] = None
    payee: str
    diff: float


class InsightCategoryRow(BaseModel):
    category_id: Optional[str] = None
    name: str
    amount: float
    usual: float
    diff: float
    # The first month of the usual when the category changed level inside
    # the six months before; None when all six count.
    usual_since: Optional[str] = None
    drivers: list[InsightDriver] = []
    # The same month a year earlier; None until the data reaches back a year.
    last_year: Optional[float] = None


class InsightExcluded(BaseModel):
    category_id: Optional[str] = None
    name: str
    reason: Literal["one_off", "more_payments", "fewer_payments"]


class InsightCard(BaseModel):
    kind: str
    direction: Literal["better", "worse"]
    params: dict[str, Any]
    series: list[float]
    category_id: Optional[str] = None
    payee_id: Optional[str] = None
    link_from: Optional[str] = None
    link_to: Optional[str] = None


class InsightsBriefing(BaseModel):
    month: str
    currency: str
    first_month: Optional[str] = None
    last_month: str
    everyday_spend: float
    usual_spend: float
    last_year_spend: Optional[float] = None
    income: float
    savings_rate: Optional[float] = None
    savings_rate_before: Optional[float] = None
    one_off_count: int
    one_off_total: float
    one_off_threshold: float
    categories: list[InsightCategoryRow]
    excluded: list[InsightExcluded]
    better: list[InsightCard]
    worse: list[InsightCard]
