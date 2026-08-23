from typing import Literal
from pydantic import BaseModel

class InsightItem(BaseModel):
    rule_id: str
    title: str
    severity: Literal["low", "medium", "high", "critical"]
    deduction_points: int
    evidence: str
    recommendation: str
    expected_savings_tokens: int = 0
    expected_savings_usd: float = 0.0

class HealthReport(BaseModel):
    score: int
    tier: Literal["S", "A", "B", "C"]
    percentile_text: str
    total_tokens_7d: int
    total_cost_7d: float
    insights: list[InsightItem]
