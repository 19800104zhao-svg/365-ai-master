"""上线前加固回归 (2026-08 审计后)。

锁定这些行为:
- 排名/统计按「设备」而不是「提交次数」计数, 无令牌的旧记录不计入
- 写接口有按 IP 的频控
- 设备令牌可走 X-Device-Token 请求头 (不进 URL / 访问日志)
- /billing/status 不再是匿名可用的付费邮箱枚举接口
- Stripe webhook 真实签名的开通/取消路径
- 静态 og 图可访问, 首页域名占位符已被替换
"""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from cloud.analytics import AnalyticsEngine
from cloud.coach import CoachEngine
from cloud.database import DatabaseEngine
from cloud.models import AggregatedScore


@pytest.fixture
def env(monkeypatch):
    test_db = DatabaseEngine("sqlite:///:memory:")
    test_db.init_db()

    import cloud.api
    cloud.api.db = test_db
    cloud.api.analytics = AnalyticsEngine(test_db)
    cloud.api.coach = CoachEngine(test_db)
    cloud.api._rate_buckets.clear()

    from cloud.main import app
    return TestClient(app), test_db


def _submit(client, score, token, **extra):
    payload = {
        "score": score,
        "tier": "B",
        "total_tokens_7d": 100_000,
        "total_cost_7d": 1.0,
        "rule_hits": {},
        "device_token": token,
    }
    payload.update(extra)
    return client.post("/api/v1/submit", json=payload)


# ---------------------------------------------------------------------------
# 排名口径: 每设备只算最近一次
# ---------------------------------------------------------------------------

def test_percentile_counts_devices_not_submissions(env):
    client, db = env
    # 设备 A 每天自动 sync 五次 (同一个人), 四台别的设备各一次
    for _ in range(5):
        assert _submit(client, 90, "dev-a").status_code == 201
    for i, s in enumerate([50, 50, 50, 50]):
        assert _submit(client, s, f"dev-{i}").status_code == 201

    # 人头口径: [90, 50, 50, 50, 50] → 60 分排在 4/5 之上 = 80
    # 行数口径会是 4/9 ≈ 44, 那是被自动 sync 灌出来的假排名
    assert db.get_percentile_for_score(60) == 80
    assert db.get_statistics()["total_submissions"] == 5
    assert db.get_rank_for_score(60) == 2


def test_percentile_uses_latest_submission_per_device(env):
    client, db = env
    _submit(client, 30, "dev-a")
    _submit(client, 95, "dev-a")  # 改善后重测, 应以这条为准
    _submit(client, 60, "dev-b")
    assert db.get_rank_for_score(80) == 2  # 只有 dev-a 的 95 在上面


def test_legacy_rows_without_token_are_excluded(env):
    client, db = env
    # 身份修复前的旧记录 / 审计探针: 没有 device_token
    for s in (1, 1, 1):
        db.save_aggregated_score(
            AggregatedScore(score=s, tier="C", total_tokens_7d=1, total_cost_7d=0.0)
        )
    _submit(client, 70, "dev-real")
    stats = db.get_statistics()
    assert stats["total_submissions"] == 1
    assert stats["avg_score"] == 70.0
    assert db.get_score_distribution()["0-20"] == 0


# ---------------------------------------------------------------------------
# 频控
# ---------------------------------------------------------------------------

def test_rate_limit_trips_on_write_endpoints(env, monkeypatch):
    client, _ = env
    from cloud.config import settings
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_requests_per_minute", 3)
    import cloud.api
    cloud.api._rate_buckets.clear()

    for _ in range(3):
        assert _submit(client, 70, "dev-x").status_code == 201
    assert _submit(client, 70, "dev-x").status_code == 429
    # 读接口不受影响
    assert client.get("/api/v1/health").status_code == 200


# ---------------------------------------------------------------------------
# 令牌走请求头
# ---------------------------------------------------------------------------

def test_device_token_header_is_accepted(env):
    client, _ = env
    _submit(client, 77, "dev-h", usage_by_model={"claude-sonnet-5": {"tokens": 1000, "cost": 1.0, "requests": 3}})
    h = {"X-Device-Token": "dev-h"}

    r = client.get("/api/v1/coach/mine", headers=h)
    assert r.status_code == 200 and r.json()["score"] == 77

    r = client.get("/api/v1/analytics/30day", headers=h)
    assert r.status_code == 200 and isinstance(r.json()["weeks"], list)

    r = client.get("/api/v1/coach/optimize", headers=h)
    assert r.status_code == 200 and "365-ai-master:optimization:start" in r.json()["markdown"]

    # 没有令牌: 422 (客户端错), 而不是 404 误导成"你没数据"
    assert client.get("/api/v1/coach/mine").status_code == 422
    # 别人的令牌看不到我的数据
    assert client.get("/api/v1/coach/mine", headers={"X-Device-Token": "someone-else"}).status_code == 404


# ---------------------------------------------------------------------------
# 付费邮箱不可枚举
# ---------------------------------------------------------------------------

def test_billing_status_requires_api_key(env, monkeypatch):
    client, db = env
    from cloud.config import settings
    db.upsert_pro_subscription("vip@example.com", "cus_1", "sub_1", "active")

    assert client.get("/api/v1/billing/status?email=vip@example.com").status_code == 401

    monkeypatch.setattr(settings, "api_key", "real-key")
    r = client.get("/api/v1/billing/status?email=vip@example.com", headers={"X-API-Key": "real-key"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    # 常量时间比较也得是精确匹配
    assert client.get("/api/v1/billing/status?email=vip@example.com", headers={"X-API-Key": "real-ke"}).status_code == 401


# ---------------------------------------------------------------------------
# Stripe webhook: 真实签名的 happy path
# ---------------------------------------------------------------------------

def _signed(payload: dict, secret: str):
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def test_webhook_activates_and_cancels_pro(env, monkeypatch):
    client, db = env
    from cloud.config import settings
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_unit")

    body, headers = _signed({
        "id": "evt_1", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_1", "object": "checkout.session",
            "customer": "cus_9", "subscription": "sub_9",
            "customer_details": {"email": "Buyer@Example.com"},
        }},
    }, "whsec_unit")
    r = client.post("/api/v1/billing/webhook", content=body, headers=headers)
    assert r.status_code == 200, r.text
    assert db.get_pro_status("buyer@example.com") == "active"

    body, headers = _signed({
        "id": "evt_2", "object": "event", "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_9", "object": "subscription", "customer": "cus_9"}},
    }, "whsec_unit")
    r = client.post("/api/v1/billing/webhook", content=body, headers=headers)
    assert r.status_code == 200
    assert db.get_pro_status("buyer@example.com") == "canceled"


# ---------------------------------------------------------------------------
# 静态资源与首页占位符
# ---------------------------------------------------------------------------

def test_static_og_image_and_site_url_substitution(env):
    client, _ = env
    r = client.get("/static/og.jpg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")

    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "__SITE_URL__" not in html
    assert 'property="og:image"' in html
    assert "pipx install git+https://github.com/19800104zhao-svg/365-ai-master.git" in html
    assert "X-Device-Token" in html  # 前端走请求头
