"""Integration tests for analytics API endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date, timedelta

from cloud.database import DatabaseEngine, Base
from cloud.models import AggregatedScore


# Pre-create the app with test database BEFORE importing
def _create_test_app_with_data():
    """Create app with in-memory database."""
    # Create isolated database (StaticPool inside DatabaseEngine keeps the
    # in-memory schema visible across threads)
    test_db = DatabaseEngine("sqlite:///:memory:")
    test_db.init_db()

    # Populate with test data
    base_date = date(2026, 7, 28)
    scores = [60, 62, 65, 70, 72, 75, 75, 78, 80, 80, 80, 85, 85, 85, 82, 82, 80, 78, 75, 75, 72, 70, 68, 65, 65, 62, 60, 60]

    for i, score in enumerate(scores):
        submit_date = base_date + timedelta(days=i)
        if score >= 80:
            tier = "S"
        elif score >= 70:
            tier = "A"
        elif score >= 50:
            tier = "B"
        else:
            tier = "C"

        payload = AggregatedScore(
            score=score,
            tier=tier,
            total_tokens_7d=6_000_000 + (score * 10_000),
            total_cost_7d=30.0 + (score * 0.5),
            rule_hits={
                "RULE_CONTEXT_BLOAT": i % 3 == 0,
                "RULE_LOW_CACHE_HIT": i % 4 == 0,
                "RULE_ERROR_RETRY_LOOP": i % 5 == 0,
            },
            submitted_at=submit_date,
            device_token="seed",
        )
        test_db.save_aggregated_score(payload)

    # Patch BEFORE creating the app
    import cloud.api
    cloud.api.db = test_db

    # Now create the analytics engine with the patched db
    from cloud.analytics import AnalyticsEngine
    cloud.api.analytics = AnalyticsEngine(test_db)

    from cloud.main import app
    return TestClient(app)


@pytest.fixture
def analytics_client_with_data():
    """Create client with pre-populated analytics data."""
    return _create_test_app_with_data()


def test_get_30day_analytics(analytics_client_with_data):
    """Test GET /api/v1/analytics/30day endpoint."""
    response = analytics_client_with_data.get("/api/v1/analytics/30day?token=seed")
    assert response.status_code == 200

    data = response.json()
    assert "weeks" in data
    assert "overall_trend" in data
    assert "summary_text" in data
    assert "improvements_this_month" in data
    assert len(data["weeks"]) > 0


def test_30day_analytics_trend_detection(analytics_client_with_data):
    """Test trend detection in analytics."""
    response = analytics_client_with_data.get("/api/v1/analytics/30day?token=seed")
    assert response.status_code == 200

    data = response.json()
    assert data["overall_trend"] in ["improving", "stable", "degrading", "insufficient_data"]


def test_30day_analytics_improvements(analytics_client_with_data):
    """Test closed-loop improvement detection."""
    response = analytics_client_with_data.get("/api/v1/analytics/30day?token=seed")
    assert response.status_code == 200

    data = response.json()
    improvements = data["improvements_this_month"]
    # Improvements is a list (may be empty)
    assert isinstance(improvements, list)


def test_export_analytics_csv(analytics_client_with_data, monkeypatch):
    """Test CSV export of analytics (需 API key,per-token)。"""
    from cloud.config import settings
    monkeypatch.setattr(settings, "api_key", "testkey")
    response = analytics_client_with_data.get(
        "/api/v1/analytics/export?format=csv&token=seed",
        headers={"X-API-Key": "testkey"},
    )
    assert response.status_code == 200

    # Parse CSV
    lines = response.text.strip().split("\n")
    assert lines[0] == "date,score,total_tokens_7d,total_cost_7d"
    assert len(lines) > 1


def test_export_analytics_json(analytics_client_with_data, monkeypatch):
    """Test JSON export of analytics (需 API key,per-token)。"""
    from cloud.config import settings
    monkeypatch.setattr(settings, "api_key", "testkey")
    response = analytics_client_with_data.get(
        "/api/v1/analytics/export?format=json&token=seed",
        headers={"X-API-Key": "testkey"},
    )
    assert response.status_code == 200

    data = response.json()
    assert "overall_trend" in data
    assert "weeks" in data


def test_export_analytics_invalid_format(analytics_client_with_data):
    """Test invalid export format."""
    response = analytics_client_with_data.get("/api/v1/analytics/export?format=xml")
    assert response.status_code == 422
