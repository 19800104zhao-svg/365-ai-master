"""Tests for Stripe billing integration (no real Stripe calls)."""
import pytest
from fastapi.testclient import TestClient

from cloud.database import DatabaseEngine


@pytest.fixture
def test_db():
    db = DatabaseEngine("sqlite:///:memory:")
    db.init_db()
    return db


@pytest.fixture
def client(test_db, monkeypatch):
    import cloud.api
    import cloud.main

    cloud.api.db = test_db
    cloud.main.db = test_db
    from cloud.main import app
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------

def test_pro_subscription_lifecycle(test_db):
    # 开通
    assert test_db.upsert_pro_subscription(
        email="pro@test.dev",
        stripe_customer_id="cus_123",
        stripe_subscription_id="sub_123",
        status="active",
    )
    assert test_db.get_pro_status("pro@test.dev") == "active"

    # webhook 重发幂等
    assert test_db.upsert_pro_subscription(
        email="pro@test.dev",
        stripe_customer_id="cus_123",
        stripe_subscription_id="sub_123",
        status="active",
    )
    assert test_db.get_pro_status("pro@test.dev") == "active"

    # 取消 (deleted 事件里没有 email, 按 subscription_id 定位)
    assert test_db.upsert_pro_subscription(
        email="",
        stripe_customer_id="cus_123",
        stripe_subscription_id="sub_123",
        status="canceled",
    )
    assert test_db.get_pro_status("pro@test.dev") == "canceled"


def test_pro_status_unknown_email(test_db):
    assert test_db.get_pro_status("nobody@test.dev") == "none"


# ---------------------------------------------------------------------------
# API 层 (Stripe 未配置时的降级行为)
# ---------------------------------------------------------------------------

def test_checkout_returns_503_when_stripe_not_configured(client, monkeypatch):
    from cloud.config import settings
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    monkeypatch.setattr(settings, "stripe_price_id", "")
    res = client.post("/api/v1/billing/checkout", json={})
    assert res.status_code == 503


def test_webhook_returns_503_when_not_configured(client, monkeypatch):
    from cloud.config import settings
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    res = client.post("/api/v1/billing/webhook", content=b"{}")
    assert res.status_code == 503


def test_webhook_rejects_bad_signature(client, monkeypatch):
    from cloud.config import settings
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    res = client.post(
        "/api/v1/billing/webhook",
        content=b'{"type":"checkout.session.completed"}',
        headers={"stripe-signature": "bad"},
    )
    assert res.status_code == 400


def test_billing_status_endpoint(client, test_db):
    test_db.upsert_pro_subscription(
        email="user@test.dev",
        stripe_customer_id="cus_9",
        stripe_subscription_id="sub_9",
        status="active",
    )
    res = client.get("/api/v1/billing/status?email=USER@test.dev")
    assert res.status_code == 200
    assert res.json()["status"] == "active"
