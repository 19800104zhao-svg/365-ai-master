import pytest
from datetime import datetime
import tempfile
import os

from cloud.models import AggregatedScore, PercentileResult
from cloud.database import DatabaseEngine


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = DatabaseEngine(f"sqlite:///{db_path}")
    db.init_db()
    yield db

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


def test_aggregated_score_model():
    """Test AggregatedScore Pydantic model validation."""
    score = AggregatedScore(
        score=78,
        tier="A",
        total_tokens_7d=6_736_252,
        total_cost_7d=32.14,
        rule_hits={
            "RULE_CONTEXT_BLOAT": True,
            "RULE_LOW_CACHE_HIT": False,
        }
    )
    assert score.score == 78
    assert score.tier == "A"
    assert len(score.rule_hits) == 2


def test_aggregated_score_invalid_score():
    """Test that scores outside 0-100 are rejected."""
    with pytest.raises(ValueError):
        AggregatedScore(
            score=150,  # Invalid
            tier="A",
            total_tokens_7d=1_000_000,
            total_cost_7d=5.0
        )


def test_database_init_and_event_insertion(temp_db):
    """Test database initialization and event insertion."""
    event = AggregatedScore(
        score=78,
        tier="A",
        total_tokens_7d=6_736_252,
        total_cost_7d=32.14,
        rule_hits={"RULE_CONTEXT_BLOAT": True}
    )

    inserted = temp_db.save_aggregated_score(event)
    assert inserted is True


def test_database_insertion_and_query(temp_db):
    """Test that inserted scores can be queried."""
    score = AggregatedScore(
        score=78,
        tier="A",
        total_tokens_7d=6_736_252,
        total_cost_7d=32.14,
        rule_hits={}
    )

    inserted = temp_db.save_aggregated_score(score)
    assert inserted is True

    percentile = temp_db.get_percentile_for_score(78)
    assert percentile > 0 and percentile <= 100


def test_percentile_boundary_cases(temp_db):
    """Test percentile calculation across the score range."""
    # Insert scores across the range
    for s in [10, 30, 50, 70, 90]:
        score = AggregatedScore(
            score=s,
            tier="B",
            total_tokens_7d=1_000_000,
            total_cost_7d=5.0,
            rule_hits={}, device_token=f"dev-{s}"
        )
        assert temp_db.save_aggregated_score(score) is True

    # Query percentiles
    p10 = temp_db.get_percentile_for_score(10)
    p50 = temp_db.get_percentile_for_score(50)
    p90 = temp_db.get_percentile_for_score(90)

    # Percentiles should increase with score
    assert p10 < p50 < p90
    assert p10 < 50
    assert p90 > 50


def test_percentile_with_duplicate_scores(temp_db):
    """Test percentile calculation when multiple entries have the same score."""
    # Insert multiple records with the same score
    for _ in range(5):
        score = AggregatedScore(score=50, tier="B", total_tokens_7d=1_000_000, total_cost_7d=5.0)
        assert temp_db.save_aggregated_score(score) is True

    # Add scores above and below
    for s in [30, 70]:
        score = AggregatedScore(score=s, tier="B", total_tokens_7d=1_000_000, total_cost_7d=5.0)
        assert temp_db.save_aggregated_score(score) is True

    # Score 50 should be around 50th percentile (with duplicates)
    p50 = temp_db.get_percentile_for_score(50)
    assert 30 < p50 < 70


def test_statistics_calculation(temp_db):
    """Test that statistics are calculated correctly."""
    # Insert test data
    scores_data = [
        (60, 3_000_000, 15.0),
        (70, 4_000_000, 20.0),
        (80, 5_000_000, 25.0),
    ]
    for score, tokens, cost in scores_data:
        agg = AggregatedScore(
            score=score,
            tier="B",
            total_tokens_7d=tokens,
            total_cost_7d=cost, device_token=f"dev-{score}"
        )
        assert temp_db.save_aggregated_score(agg) is True

    stats = temp_db.get_statistics()
    assert stats["total_submissions"] == 3
    assert stats["avg_score"] == 70.0
    assert stats["avg_tokens_7d"] == 4_000_000


def test_percentile_result_description(temp_db):
    """Test that PercentileResult generates appropriate descriptions."""
    # Low percentile
    result_low = PercentileResult(
        score=20,
        percentile=15,
        total_samples=100,
        ranking_tier="Bottom 25%"
    )
    assert "保持学习" in result_low.description

    # High percentile
    result_high = PercentileResult(
        score=90,
        percentile=92,
        total_samples=100,
        ranking_tier="Top 25%"
    )
    assert "🏆" in result_high.description


def test_empty_database_statistics(temp_db):
    """Test that statistics gracefully handle empty database."""
    stats = temp_db.get_statistics()
    assert stats["total_submissions"] == 0
    assert stats["avg_score"] == 0.0


def test_empty_database_percentile(temp_db):
    """Test percentile calculation on empty database defaults to 50."""
    percentile = temp_db.get_percentile_for_score(50)
    assert percentile == 50  # Default for insufficient data


def test_score_distribution(temp_db):
    """Test score distribution bucketing."""
    # Insert scores in different buckets
    for score in [15, 35, 55, 75, 95]:
        agg = AggregatedScore(score=score, tier="B", total_tokens_7d=1_000_000, total_cost_7d=5.0, device_token=f"dev-{score}")
        assert temp_db.save_aggregated_score(agg) is True

    dist = temp_db.get_score_distribution()
    assert dist["0-20"] == 1
    assert dist["20-40"] == 1
    assert dist["40-60"] == 1
    assert dist["60-80"] == 1
    assert dist["80-100"] == 1


def test_tier_distribution(temp_db):
    """Test tier distribution calculation."""
    tiers_data = [("S", 5), ("A", 3), ("B", 2), ("C", 1)]

    for tier, count in tiers_data:
        for _ in range(count):
            agg = AggregatedScore(
                score=90 if tier == "S" else 75 if tier == "A" else 60 if tier == "B" else 45,
                tier=tier,
                total_tokens_7d=1_000_000,
                total_cost_7d=5.0
            )
            assert temp_db.save_aggregated_score(agg) is True

    dist = temp_db.get_tier_distribution()
    assert dist["S"] == 5
    assert dist["A"] == 3
    assert dist["B"] == 2
    assert dist["C"] == 1
