"""Tests for 365 AI Master feed and subscription."""
from datetime import date

import pytest

from cloud.database import DatabaseEngine
from cloud.master import CONTENT_POOL, get_daily_feed


def test_daily_feed_structure():
    feed = get_daily_feed(date(2026, 8, 7))
    assert feed.date == "2026-08-07"
    assert feed.persona_line
    assert feed.disclaimer
    # 技巧 / 能力 / 安全 各一条
    kinds = [t.kind for t in feed.tips]
    assert len(feed.tips) == 3
    assert kinds[0] == "tip"
    assert kinds[1] in ("skill", "agent")
    assert kinds[2] == "security"


def test_daily_feed_rotates_by_date():
    a = get_daily_feed(date(2026, 8, 7))
    b = get_daily_feed(date(2026, 8, 8))
    # 相邻两天至少有一条不同 (池子远大于 1)
    assert [t.title for t in a.tips] != [t.title for t in b.tips]


def test_daily_feed_deterministic_same_day():
    a = get_daily_feed(date(2026, 8, 7))
    b = get_daily_feed(date(2026, 8, 7))
    assert [t.title for t in a.tips] == [t.title for t in b.tips]


def test_content_pool_quality_bar():
    """内容池质量底线: 每条必须有来源和可信依据;隐私敏感条目必须带提醒。"""
    assert len(CONTENT_POOL) >= 10
    for tip in CONTENT_POOL:
        assert tip.source, f"{tip.title} 缺来源"
        assert tip.why_trust, f"{tip.title} 缺可信依据"
        assert tip.kind in ("tip", "skill", "agent", "product", "security")


def test_subscriber_roundtrip():
    db = DatabaseEngine("sqlite:///:memory:")
    db.init_db()
    assert db.add_subscriber("test@example.com")
    # 幂等
    assert db.add_subscriber("test@example.com")


# ---------------------------------------------------------------------------
# 排行榜
# ---------------------------------------------------------------------------

def test_rankings_present_and_rotate():
    from cloud.master import AGENT_POOL, SKILL_POOL
    a = get_daily_feed(date(2026, 8, 7))
    assert len(a.skill_ranking) == 10
    assert len(a.agent_ranking) == 10
    # 相邻两天榜单顺序不同 (轮换)
    b = get_daily_feed(date(2026, 8, 8))
    assert [r.name for r in a.skill_ranking] != [r.name for r in b.skill_ranking]
    # 池子质量底线: 每条有 tagline/why/url
    for item in SKILL_POOL + AGENT_POOL:
        assert item.tagline and item.why and item.url.startswith("https://github.com/")


def test_coach_title_and_encourage():
    from cloud.coach import build_title_and_encourage
    title, enc = build_title_and_encourage("S", 95, 0)
    assert title == "AI 宗师"
    title, enc = build_title_and_encourage("C", 20, 5)
    assert title == "AI 探索者"
    assert "加油" in enc  # 低分要有打气话


# ---------------------------------------------------------------------------
# v2 动态内容池
# ---------------------------------------------------------------------------

VALID_TIP = {
    "kind": "tip",
    "title": "动态池测试条目",
    "detail": "这是采集 pipeline 提交的条目",
    "why_trust": "官方文档明确说明",
    "source": "官方文档",
}


def test_master_tip_roundtrip_and_idempotent():
    db = DatabaseEngine("sqlite:///:memory:")
    db.init_db()
    assert db.add_master_tip(VALID_TIP)
    assert db.add_master_tip(VALID_TIP)  # title 重复 → 幂等成功
    tips = db.get_active_master_tips()
    assert len(tips) == 1
    assert tips[0]["title"] == "动态池测试条目"


def test_daily_feed_merges_dynamic_pool():
    # 动态条目排在池前, 轮换到 offset 0 时应能选中动态 tip
    feed = get_daily_feed(date(2026, 8, 7), extra_pool=[VALID_TIP])
    all_titles = [t.title for t in feed.tips]
    # 动态池被合并 (不保证今天恰好轮到,但池大小变了轮换结果确定可复现)
    feed_same = get_daily_feed(date(2026, 8, 7), extra_pool=[VALID_TIP])
    assert all_titles == [t.title for t in feed_same.tips]
    # 坏数据不进池不报错
    feed_bad = get_daily_feed(date(2026, 8, 7), extra_pool=[{"kind": "tip"}])
    assert len(feed_bad.tips) == 3


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    import cloud.api
    import cloud.main

    db = DatabaseEngine("sqlite:///:memory:")
    db.init_db()
    cloud.api.db = db
    cloud.main.db = db
    from cloud.main import app
    return TestClient(app, raise_server_exceptions=False), db


def test_library_returns_all_grouped(api_client):
    client, _ = api_client
    res = client.get("/api/v1/master/library")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == len(CONTENT_POOL)
    kinds = {g["kind"] for g in data["groups"]}
    assert {"tip", "skill", "agent", "security"} <= kinds
    # 每组条目数之和 == total
    assert sum(len(g["items"]) for g in data["groups"]) == data["total"]
    # 每条都有来源与可信依据 (质量底线)
    for g in data["groups"]:
        for item in g["items"]:
            assert item["source"] and item["why_trust"]


def test_library_includes_dynamic_pool(api_client):
    client, db = api_client
    before = client.get("/api/v1/master/library").json()["total"]
    db.add_master_tip(VALID_TIP)
    after = client.get("/api/v1/master/library").json()
    assert after["total"] == before + 1
    titles = [i["title"] for g in after["groups"] for i in g["items"]]
    assert VALID_TIP["title"] in titles


def test_retire_removes_from_library(api_client, monkeypatch):
    """下架后不再出现在全库与今日推荐里 (记录保留,只置 active=0)。"""
    client, db = api_client
    from cloud.config import settings
    monkeypatch.setattr(settings, "api_key", "real-key")

    db.add_master_tip(VALID_TIP)
    assert VALID_TIP["title"] in [
        i["title"] for g in client.get("/api/v1/master/library").json()["groups"] for i in g["items"]
    ]

    res = client.post(
        "/api/v1/master/retire",
        json={"title": VALID_TIP["title"]},
        headers={"X-API-Key": "real-key"},
    )
    assert res.status_code == 200
    assert VALID_TIP["title"] not in [
        i["title"] for g in client.get("/api/v1/master/library").json()["groups"] for i in g["items"]
    ]


def test_retire_requires_key_and_existing_title(api_client, monkeypatch):
    client, db = api_client
    from cloud.config import settings

    monkeypatch.setattr(settings, "api_key", "dev-key-change-in-production")
    assert client.post("/api/v1/master/retire", json={"title": "x"}).status_code == 503

    monkeypatch.setattr(settings, "api_key", "real-key")
    assert client.post("/api/v1/master/retire", json={"title": "x"}).status_code == 401
    assert client.post(
        "/api/v1/master/retire", json={"title": "不存在"}, headers={"X-API-Key": "real-key"}
    ).status_code == 404
    assert client.post(
        "/api/v1/master/retire", json={}, headers={"X-API-Key": "real-key"}
    ).status_code == 422


def test_content_pool_titles_unique():
    """回归: 种子池不得有重复标题 (把动态池条目并入代码时容易撞车)。"""
    titles = [t.title for t in CONTENT_POOL]
    assert len(titles) == len(set(titles))


def test_master_submit_requires_configured_key(api_client, monkeypatch):
    client, _ = api_client
    from cloud.config import settings

    # 默认 key → 端点关闭
    monkeypatch.setattr(settings, "api_key", "dev-key-change-in-production")
    res = client.post("/api/v1/master/submit", json=VALID_TIP)
    assert res.status_code == 503

    # 配置了 key 但请求没带 → 401
    monkeypatch.setattr(settings, "api_key", "real-key")
    res = client.post("/api/v1/master/submit", json=VALID_TIP)
    assert res.status_code == 401

    # 带对 key → 201
    res = client.post(
        "/api/v1/master/submit", json=VALID_TIP, headers={"X-API-Key": "real-key"}
    )
    assert res.status_code == 201


def test_master_submit_validates_required_fields(api_client, monkeypatch):
    client, _ = api_client
    from cloud.config import settings
    monkeypatch.setattr(settings, "api_key", "real-key")

    bad = dict(VALID_TIP)
    bad.pop("why_trust")
    res = client.post("/api/v1/master/submit", json=bad, headers={"X-API-Key": "real-key"})
    assert res.status_code == 422

    bad2 = dict(VALID_TIP)
    bad2["kind"] = "marketing"
    res = client.post("/api/v1/master/submit", json=bad2, headers={"X-API-Key": "real-key"})
    assert res.status_code == 422
