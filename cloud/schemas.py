"""Pydantic schemas for analytics and dashboard."""
from datetime import datetime, date
from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class DailySample(BaseModel):
    """A single day's AI usage snapshot."""
    date: date
    score: int = Field(ge=0, le=100)
    total_tokens: int = Field(ge=0)
    total_cost: float = Field(ge=0.0)
    rule_hits: Dict[str, bool] = Field(default_factory=dict)


class WeeklySummary(BaseModel):
    """Aggregated metrics for one week (Mon-Sun)."""
    week_start_date: date
    week_end_date: date
    avg_score: float = Field(ge=0.0, le=100.0)
    avg_tokens_per_day: int = Field(ge=0)
    total_cost: float = Field(ge=0.0)
    highest_score: int = Field(ge=0, le=100)
    lowest_score: int = Field(ge=0, le=100)
    days_with_data: int = Field(ge=0, le=7)
    most_frequent_rules_hit: List[str] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "week_start_date": "2026-07-28",
                "week_end_date": "2026-08-03",
                "avg_score": 75.5,
                "avg_tokens_per_day": 962321,
                "total_cost": 31.5,
                "highest_score": 85,
                "lowest_score": 68,
                "days_with_data": 7,
                "most_frequent_rules_hit": ["RULE_CONTEXT_BLOAT", "RULE_LOW_CACHE_HIT"]
            }
        }


class ClosedLoopComparison(BaseModel):
    """Before/after comparison for a specific recommendation."""
    rule_id: str
    rule_title: str
    recommendation_issued_date: date
    before_metric: str
    after_metric: str
    improvement_percent: float = Field(ge=-100.0, le=100.0)
    tokens_saved: int = Field(ge=0)
    verified: bool


class TrendAnalysis(BaseModel):
    """30-day trend snapshot."""
    period_start: date
    period_end: date
    weeks: List[WeeklySummary]
    overall_trend: str = Field(description="'improving', 'stable', or 'degrading'")
    trend_score: float = Field(ge=-1.0, le=1.0, description="-1.0 (degrading) to 1.0 (improving)")
    improvements_this_month: List[ClosedLoopComparison]
    summary_text: str = ""

    class Config:
        json_schema_extra = {
            "example": {
                "period_start": "2026-07-04",
                "period_end": "2026-08-03",
                "weeks": [],  # [WeeklySummary objects]
                "overall_trend": "improving",
                "trend_score": 0.15,
                "improvements_this_month": [],
                "summary_text": "You're on an upward trajectory. Score improved by 8 points over 4 weeks."
            }
        }


class AnalyticsExport(BaseModel):
    """Export format for historical data."""
    format: str = Field(description="'csv', 'json'")
    data: str = Field(description="Raw data content")
    generated_at: datetime
    filename: str
