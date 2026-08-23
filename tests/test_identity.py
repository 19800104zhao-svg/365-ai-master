"""身份隔离与隐私收敛测试 (Blocker 1)。

产品此前无 "用户" 概念: 所有读接口无鉴权返回全库最新一条提交,
任何匿名 POST 都能劫持首页。这组测试锁定修复后的行为:
- 每次提交带匿名 device_token, 数据按 token 归属
- /coach/mine?token= 只返回该 token 自己的报告
- 不同 token 互相隔离, 匿名写入无法覆盖他人首页
- 旧的无 token 全局读接口不再泄露任何真实数据
"""
import pytest
from fastapi.testclient import TestClient

from cloud.database import DatabaseEngine
from cloud.analytics import AnalyticsEngine
from cloud.coach import CoachEngine


@pytest.fixture
def client(monkeypatch):
    """隔离的内存库 + 连带 patch analytics/coach 单例 (它们持旧 db 引用)。"""
    test_db = DatabaseEngine("sqlite:///:memory:")
    test_db.init_db()

    import cloud.api
    cloud.api.db = test_db
    cloud.api.analytics = AnalyticsEngine(test_db)
    cloud.api.coach = CoachEngine(test_db)

    from cloud.main import app
    return TestClient(app)


def _payload(score, token=None):
    p = {
        "score": score,
        "tier": "A",
        "total_tokens_7d": 1_000_000,
        "total_cost_7d": 5.0,
        "rule_hits": {},
    }
    if token is not None:
        p["device_token"] = token
    return p


def test_mine_returns_own_report(client):
    """带 token 提交后, /coach/mine?token= 返回本人报告。"""
    client.post("/api/v1/coach/analyze", json=_payload(42, "tokenA"))
    r = client.get("/api/v1/coach/mine", params={"token": "tokenA"})
    assert r.status_code == 200
    assert r.json()["score"] == 42


def test_mine_unknown_token_is_404(client):
    """陌生 token (从未提交) 得到 404, 即真正的空状态。"""
    r = client.get("/api/v1/coach/mine", params={"token": "nobody"})
    assert r.status_code == 404


def test_mine_requires_token(client):
    """缺 token 参数是 422, 不允许无参拿数据。"""
    r = client.get("/api/v1/coach/mine")
    assert r.status_code == 422


def test_tokens_are_isolated(client):
    """A 和 B 各自提交, 互不覆盖 —— 匿名写入无法劫持他人首页。"""
    client.post("/api/v1/coach/analyze", json=_payload(30, "tokenA"))
    client.post("/api/v1/coach/analyze", json=_payload(90, "tokenB"))
    a = client.get("/api/v1/coach/mine", params={"token": "tokenA"}).json()
    b = client.get("/api/v1/coach/mine", params={"token": "tokenB"}).json()
    assert a["score"] == 30
    assert b["score"] == 90


def test_latest_no_longer_leaks_global(client):
    """有真实提交后, 旧的无 token /coach/latest 不再吐任何人的数据 (404)。"""
    client.post("/api/v1/coach/analyze", json=_payload(88, "tokenA"))
    r = client.get("/api/v1/coach/latest")
    assert r.status_code == 404


def test_optimize_requires_token(client):
    """/coach/optimize 无 token 不再泄露最新提交者的 CLAUDE.md 配置。"""
    client.post("/api/v1/coach/analyze", json=_payload(70, "tokenA"))
    r = client.get("/api/v1/coach/optimize")
    assert r.status_code == 422


def test_optimize_scoped_to_token(client):
    """带 token 的 /coach/optimize 正常返回该 token 的配置。"""
    client.post("/api/v1/coach/analyze", json=_payload(70, "tokenA"))
    r = client.get("/api/v1/coach/optimize", params={"token": "tokenA"})
    assert r.status_code == 200
    assert "markdown" in r.json()


def test_30day_no_token_does_not_leak(client):
    """无 token 的 /analytics/30day 不返回全库趋势 (空数据, 不泄露)。"""
    client.post("/api/v1/coach/analyze", json=_payload(70, "tokenA"))
    r = client.get("/api/v1/analytics/30day")
    assert r.status_code == 200
    # 无 token 时不得含任何真实周数据
    assert r.json().get("weeks") == []


def test_export_locked_without_api_key(client):
    """/analytics/export 不再对匿名访客开放全量 CSV 导出。"""
    client.post("/api/v1/coach/analyze", json=_payload(70, "tokenA"))
    r = client.get("/api/v1/analytics/export", params={"format": "csv"})
    assert r.status_code in (401, 403)


def test_submit_stores_device_token(client):
    """/submit 也接受并存储 device_token (与 /coach/analyze 一致)。"""
    r = client.post("/api/v1/submit", json=_payload(55, "tokenC"))
    assert r.status_code == 201
    mine = client.get("/api/v1/coach/mine", params={"token": "tokenC"})
    assert mine.status_code == 200
    assert mine.json()["score"] == 55
