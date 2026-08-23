"""Tests for analytics engine."""
import pytest
from datetime import date, datetime, timedelta

from cloud.analytics import AnalyticsEngine
from cloud.schemas import DailySample, WeeklySummary
from cloud.database import DatabaseEngine, ScoreRecord


@pytest.fixture
def test_db():
    """Create an in-memory test database."""
    db = DatabaseEngine("sqlite:///:memory:")
    db.init_db()
    return db


@pytest.fixture
def analytics(test_db):
    """Create an AnalyticsEngine with test database."""
    return AnalyticsEngine(test_db)


def test_daily_sample_model():
    """Test DailySample Pydantic model."""
    sample = DailySample(
        date=date(2026, 8, 1),
        score=78,
        total_tokens=6_736_252,
        total_cost=32.14,
        rule_hits={"RULE_CONTEXT_BLOAT": True}
    )
    assert sample.score == 78
    assert sample.total_tokens == 6_736_252


def test_weekly_summary_model():
    """Test WeeklySummary model."""
    summary = WeeklySummary(
        week_start_date=date(2026, 7, 28),
        week_end_date=date(2026, 8, 3),
        avg_score=75.5,
        avg_tokens_per_day=962321,
        total_cost=31.5,
        highest_score=85,
        lowest_score=68,
        days_with_data=7,
        most_frequent_rules_hit=["RULE_CONTEXT_BLOAT"]
    )
    assert summary.avg_score == 75.5
    assert len(summary.most_frequent_rules_hit) == 1


def test_empty_daily_samples(analytics):
    """Test getting daily samples from empty database."""
    samples = analytics.get_daily_samples(days_back=30)
    assert len(samples) == 0


def test_aggregate_weekly_empty(analytics):
    """Test weekly aggregation with empty samples."""
    weeks = analytics.aggregate_weekly([])
    assert len(weeks) == 0


def test_aggregate_weekly_single_day(analytics):
    """Test weekly aggregation with single day of data."""
    samples = [
        DailySample(
            date=date(2026, 7, 30),
            score=75,
            total_tokens=1_000_000,
            total_cost=5.0,
            rule_hits={}
        )
    ]
    weeks = analytics.aggregate_weekly(samples)
    assert len(weeks) == 1
    assert weeks[0].avg_score == 75.0
    assert weeks[0].highest_score == 75
    assert weeks[0].lowest_score == 75
    assert weeks[0].days_with_data == 1


def test_aggregate_weekly_full_week(analytics):
    """Test weekly aggregation with full week of data."""
    samples = [
        DailySample(date=date(2026, 7, 28), score=70, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        DailySample(date=date(2026, 7, 29), score=72, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        DailySample(date=date(2026, 7, 30), score=75, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        DailySample(date=date(2026, 7, 31), score=78, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        DailySample(date=date(2026, 8, 1), score=80, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
    ]
    weeks = analytics.aggregate_weekly(samples)
    assert len(weeks) == 1
    assert weeks[0].avg_score == 75.0
    assert weeks[0].highest_score == 80
    assert weeks[0].lowest_score == 70
    assert weeks[0].days_with_data == 5


def test_aggregate_weekly_multiple_weeks(analytics):
    """Test weekly aggregation spanning multiple weeks."""
    samples = [
        # Week 1 (Jul 28-Aug 3)
        DailySample(date=date(2026, 7, 28), score=60, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        DailySample(date=date(2026, 7, 30), score=65, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        # Week 2 (Aug 4-10)
        DailySample(date=date(2026, 8, 4), score=75, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
        DailySample(date=date(2026, 8, 6), score=80, total_tokens=1_000_000, total_cost=5.0, rule_hits={}),
    ]
    weeks = analytics.aggregate_weekly(samples)
    assert len(weeks) == 2
    assert weeks[0].avg_score == 62.5  # (60 + 65) / 2
    assert weeks[1].avg_score == 77.5  # (75 + 80) / 2


def test_trend_analysis_improving(analytics):
    """Test trend detection for improving scores."""
    weeks = [
        WeeklySummary(
            week_start_date=date(2026, 7, 28),
            week_end_date=date(2026, 8, 3),
            avg_score=60.0,
            avg_tokens_per_day=1_000_000,
            total_cost=5.0,
            highest_score=60,
            lowest_score=60,
            days_with_data=1,
            most_frequent_rules_hit=[]
        ),
        WeeklySummary(
            week_start_date=date(2026, 8, 4),
            week_end_date=date(2026, 8, 10),
            avg_score=75.0,
            avg_tokens_per_day=1_000_000,
            total_cost=5.0,
            highest_score=75,
            lowest_score=75,
            days_with_data=1,
            most_frequent_rules_hit=[]
        ),
    ]
    analysis = analytics.analyze_trend(weeks)
    assert analysis.overall_trend == "improving"
    assert analysis.trend_score > 0


def test_trend_analysis_degrading(analytics):
    """Test trend detection for degrading scores."""
    weeks = [
        WeeklySummary(
            week_start_date=date(2026, 7, 28),
            week_end_date=date(2026, 8, 3),
            avg_score=80.0,
            avg_tokens_per_day=1_000_000,
            total_cost=5.0,
            highest_score=80,
            lowest_score=80,
            days_with_data=1,
            most_frequent_rules_hit=[]
        ),
        WeeklySummary(
            week_start_date=date(2026, 8, 4),
            week_end_date=date(2026, 8, 10),
            avg_score=60.0,
            avg_tokens_per_day=1_000_000,
            total_cost=5.0,
            highest_score=60,
            lowest_score=60,
            days_with_data=1,
            most_frequent_rules_hit=[]
        ),
    ]
    analysis = analytics.analyze_trend(weeks)
    assert analysis.overall_trend == "degrading"
    assert analysis.trend_score < 0


def test_trend_analysis_stable(analytics):
    """Test trend detection for stable scores."""
    weeks = [
        WeeklySummary(
            week_start_date=date(2026, 7, 28),
            week_end_date=date(2026, 8, 3),
            avg_score=75.0,
            avg_tokens_per_day=1_000_000,
            total_cost=5.0,
            highest_score=75,
            lowest_score=75,
            days_with_data=1,
            most_frequent_rules_hit=[]
        ),
        WeeklySummary(
            week_start_date=date(2026, 8, 4),
            week_end_date=date(2026, 8, 10),
            avg_score=76.0,
            avg_tokens_per_day=1_000_000,
            total_cost=5.0,
            highest_score=76,
            lowest_score=76,
            days_with_data=1,
            most_frequent_rules_hit=[]
        ),
    ]
    analysis = analytics.analyze_trend(weeks)
    assert analysis.overall_trend == "stable"
    assert analysis.trend_score == 0.0


def test_export_as_csv(analytics):
    """Test CSV export."""
    csv = analytics.export_as_csv()
    lines = csv.split("\n")
    assert lines[0] == "date,score,total_tokens_7d,total_cost_7d"
    # Additional rows would depend on actual data


def test_export_as_json(analytics):
    """Test JSON export."""
    json_data = analytics.export_as_json()
    assert "overall_trend" in json_data
    assert "weeks" in json_data
    assert "summary_text" in json_data
