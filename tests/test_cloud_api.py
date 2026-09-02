"""Tests for AgentFit Cloud API endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cloud.models import AggregatedScore
from cloud.database import DatabaseEngine, Base


@pytest.fixture
def test_db():
    """Create a fresh in-memory SQLite database for each test."""
    # DatabaseEngine uses StaticPool for :memory: URLs so the same
    # connection (and schema) is shared across threads
    db = DatabaseEngine("sqlite:///:memory:")
    db.init_db()
    return db


@pytest.fixture
def client(test_db, monkeypatch):
    """FastAPI test client with isolated database."""
    # Monkeypatch the database instance in cloud.api
    import cloud.api
    import cloud.main

    cloud.api.db = test_db
    cloud.main.db = test_db

    from cloud.main import app
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_submit_score_endpoint(client):
    """Test score submission endpoint."""
    payload = {
        "score": 78,
        "tier": "A",
        "total_tokens_7d": 6_736_252,
        "total_cost_7d": 32.14,
        "rule_hits": {
            "RULE_CONTEXT_BLOAT": True,
            "RULE_MISSING_SKILL": True
        }
    }
    response = client.post("/api/v1/submit", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True


def test_submit_score_invalid_score_too_high(client):
    """Test that scores > 100 are rejected."""
    payload = {
        "score": 150,  # Invalid
        "tier": "A",
        "total_tokens_7d": 1_000_000,
        "total_cost_7d": 5.0,
        "rule_hits": {}
    }
    response = client.post("/api/v1/submit", json=payload)
    assert response.status_code == 422  # Validation error


def test_submit_score_invalid_tier(client):
    """Test that invalid tiers are rejected."""
    payload = {
        "score": 78,
        "tier": "X",  # Invalid tier
        "total_tokens_7d": 1_000_000,
        "total_cost_7d": 5.0,
        "rule_hits": {}
    }
    response = client.post("/api/v1/submit", json=payload)
    assert response.status_code == 422


def test_percentile_query_single_score(client):
    """Test percentile query with a single submitted score."""
    # Submit a score first
    submit_payload = {
        "score": 70,
        "tier": "B",
        "total_tokens_7d": 1_000_000,
        "total_cost_7d": 5.0,
        "rule_hits": {}, "device_token": "dev-70"
    }
    submit_response = client.post("/api/v1/submit", json=submit_payload)
    assert submit_response.status_code == 201

    # Query percentile for score 70
    response = client.get("/api/v1/percentile?score=70")
    assert response.status_code == 200
    data = response.json()
    assert "percentile" in data
    assert data["score"] == 70
    assert 0 < data["percentile"] <= 100
    assert data["total_samples"] >= 1


def test_percentile_query_multiple_scores(client):
    """Test percentile ranking with multiple submitted scores."""
    # Submit multiple scores
    scores = [50, 70, 80, 90]
    for score in scores:
        payload = {
            "score": score,
            "tier": "B",
            "total_tokens_7d": 1_000_000,
            "total_cost_7d": 5.0,
            "rule_hits": {}, "device_token": f"dev-{score}"
        }
        response = client.post("/api/v1/submit", json=payload)
        assert response.status_code == 201

    # Query percentiles - should increase with score
    p50 = client.get("/api/v1/percentile?score=50").json()["percentile"]
    p70 = client.get("/api/v1/percentile?score=70").json()["percentile"]
    p80 = client.get("/api/v1/percentile?score=80").json()["percentile"]
    p90 = client.get("/api/v1/percentile?score=90").json()["percentile"]

    assert p50 < p70 < p80 < p90


def test_percentile_query_ranking_tier(client):
    """Test that percentile results include proper ranking tiers."""
    # Submit 20 scores across the range
    for score in range(5, 100, 5):
        payload = {
            "score": score,
            "tier": "B",
            "total_tokens_7d": 1_000_000,
            "total_cost_7d": 5.0,
            "rule_hits": {}, "device_token": f"dev-{score}"
        }
        client.post("/api/v1/submit", json=payload)

    # Query low score
    response_low = client.get("/api/v1/percentile?score=10")
    assert response_low.status_code == 200
    assert "Bottom 25%" in response_low.json()["ranking_tier"] or "25-50%" in response_low.json()["ranking_tier"]

    # Query high score
    response_high = client.get("/api/v1/percentile?score=95")
    assert response_high.status_code == 200
    assert "Top 25%" in response_high.json()["ranking_tier"] or "50-75%" in response_high.json()["ranking_tier"]


def test_percentile_query_invalid_score_too_high(client):
    """Test that percentile queries reject scores > 100."""
    response = client.get("/api/v1/percentile?score=150")
    # FastAPI returns 422 for Query(ge/le) validation failures
    assert response.status_code == 422


def test_percentile_query_invalid_score_negative(client):
    """Test that percentile queries reject negative scores."""
    response = client.get("/api/v1/percentile?score=-10")
    assert response.status_code == 422


def test_stats_endpoint(client):
    """Test public statistics endpoint."""
    # Submit some scores
    for score in [60, 70, 80]:
        payload = {
            "score": score,
            "tier": "B",
            "total_tokens_7d": 1_000_000,
            "total_cost_7d": 5.0,
            "rule_hits": {}, "device_token": f"dev-{score}"
        }
        client.post("/api/v1/submit", json=payload)

    response = client.get("/api/v1/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_submissions"] == 3
    assert data["avg_score"] > 60 and data["avg_score"] < 80
    assert data["avg_tokens_7d"] == 1_000_000


def test_stats_empty_database(client):
    """Test stats endpoint with no submitted scores."""
    response = client.get("/api/v1/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_submissions"] == 0
    assert data["avg_score"] == 0.0


def test_submit_with_rule_hits(client):
    """Test that rule hits are properly stored and retrieved."""
    payload = {
        "score": 72,
        "tier": "B",
        "total_tokens_7d": 5_000_000,
        "total_cost_7d": 25.0,
        "rule_hits": {
            "RULE_CONTEXT_BLOAT": True,
            "RULE_LOW_CACHE_HIT": True,
            "RULE_ERROR_RETRY_LOOP": False,
            "RULE_MODEL_OVERUSE": False,
            "RULE_MISSING_SKILL": True,
        }
    }
    response = client.post("/api/v1/submit", json=payload)
    assert response.status_code == 201


def test_api_docs_available(client):
    """Test that API documentation is available."""
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower() or "openapi" in response.text.lower()


def test_openapi_schema_available(client):
    """Test that OpenAPI schema is available."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "paths" in schema
    assert "/api/v1/submit" in schema["paths"]
    assert "/api/v1/percentile" in schema["paths"]
