"""Tests for agentfit sync (local aggregation → cloud profile)."""
from datetime import datetime, timedelta

from agentfit.models.events import UsageEvent
from agentfit.sync import build_profile, classify_model, estimate_event_cost


def make_event(i: int, model: str = "claude-sonnet-5", hour: int = 10, **kw) -> UsageEvent:
    defaults = dict(
        event_id=f"e{i}",
        provider="claude",
        session_id="s1",
        project_hash="hash",
        timestamp=datetime(2026, 8, 7, hour, 0, 0).astimezone(),
        model=model,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        turn_index=i + 1,
    )
    defaults.update(kw)
    return UsageEvent(**defaults)


def test_classify_model_consistent_with_cloud():
    assert classify_model("claude-fable-5") == "premium"
    assert classify_model("claude-haiku-4-5") == "fast"
    assert classify_model("claude-sonnet-5") == "balanced"
    assert classify_model("gemini-2.5-pro") == "balanced"  # 不误伤 gemini


def test_estimate_cost_premium_higher_than_fast():
    e_premium = make_event(1, model="claude-opus-5")
    e_fast = make_event(2, model="claude-haiku-4-5")
    assert estimate_event_cost(e_premium) > estimate_event_cost(e_fast)


def test_build_profile_structure():
    events = (
        [make_event(i, model="claude-fable-5", hour=22) for i in range(5)]
        + [make_event(i + 10, model="claude-haiku-4-5", hour=10) for i in range(3)]
    )
    profile = build_profile(events, goal="测试目标")

    assert 0 <= profile["score"] <= 100
    assert profile["tier"] in ("S", "A", "B", "C")
    assert profile["total_tokens_7d"] == 8 * 1500
    assert profile["total_cost_7d"] > 0
    assert set(profile["usage_by_model"].keys()) == {"claude-fable-5", "claude-haiku-4-5"}
    assert profile["usage_by_model"]["claude-fable-5"]["requests"] == 5
    assert sum(profile["hourly_histogram"]) == 8
    assert profile["goal"] == "测试目标"


def test_build_profile_cache_hit_rate():
    events = [
        make_event(1, input_tokens=1000, cache_read_tokens=9000),
        make_event(2, input_tokens=1000, cache_read_tokens=9000),
    ]
    profile = build_profile(events)
    # 18000 cache / (2000 input + 18000 cache) = 0.9
    assert abs(profile["cache_hit_rate"] - 0.9) < 0.01


def test_build_profile_no_goal_omits_field():
    profile = build_profile([make_event(1)])
    assert "goal" not in profile


def test_build_profile_rule_hits_are_rule_ids():
    # 40+ 事件触发 RULE_MISSING_SKILL
    events = [make_event(i) for i in range(45)]
    profile = build_profile(events)
    for key in profile["rule_hits"]:
        assert key.startswith("RULE_")


def test_build_profile_billing_fields():
    profile = build_profile(
        [make_event(1)], billing_mode="subscription", monthly_subscription_usd=200.0
    )
    assert profile["billing_mode"] == "subscription"
    assert profile["monthly_subscription_usd"] == 200.0
    # 非法模式不进画像
    p2 = build_profile([make_event(1)], billing_mode="magic")
    assert "billing_mode" not in p2


def test_profile_is_valid_cloud_payload():
    """本地画像必须能通过云端 AggregatedScore 校验 (契约测试)。"""
    from cloud.models import AggregatedScore

    events = [make_event(i, model="claude-opus-5") for i in range(12)]
    profile = build_profile(events, goal="契约测试")
    parsed = AggregatedScore(**profile)
    assert parsed.score == profile["score"]
    assert parsed.usage_by_model


# --- device token (匿名身份, Blocker 1) ---
from agentfit.sync import get_or_create_device_token


def test_device_token_generated_when_absent():
    """空 config 首次调用: 生成一个 token 并写进 cfg。"""
    cfg = {}
    token = get_or_create_device_token(cfg)
    assert token
    assert len(token) >= 16
    assert cfg["device_token"] == token  # 已 mutate 进 cfg, 供调用方持久化


def test_device_token_stable_across_calls():
    """已有 token 的 config: 返回原值, 不重新生成。"""
    cfg = {"device_token": "existing-token-123"}
    assert get_or_create_device_token(cfg) == "existing-token-123"
    # 二次调用仍稳定
    assert get_or_create_device_token(cfg) == "existing-token-123"
