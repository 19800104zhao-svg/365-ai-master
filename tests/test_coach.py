"""Unit tests for the Coach Engine."""
import pytest

from cloud.coach import (
    CoachEngine,
    analyze_time_habits,
    build_action_items,
    build_routing_table,
    classify_model,
    diagnose_model_mix,
    infer_goal_and_path,
)
from cloud.database import DatabaseEngine
from cloud.models import AggregatedScore


# ---------------------------------------------------------------------------
# classify_model
# ---------------------------------------------------------------------------

def test_classify_model_tiers():
    assert classify_model("claude-opus-5") == "premium"
    assert classify_model("claude-fable-5") == "premium"
    assert classify_model("claude-haiku-4-5") == "fast"
    assert classify_model("gpt-4o-mini") == "fast"
    assert classify_model("claude-sonnet-5") == "balanced"
    assert classify_model("gemini-2.5-pro") == "balanced"


# ---------------------------------------------------------------------------
# diagnose_model_mix
# ---------------------------------------------------------------------------

def test_diagnose_empty_usage_returns_generic():
    text, saving, flags = diagnose_model_mix({}, 30.0)
    assert "还没同步" in text
    assert saving == 0.0
    assert not flags["model_overuse"] and not flags["fast_gap"]


def test_diagnose_premium_heavy_flags_saving():
    usage = {
        "claude-opus-5": {"tokens": 9_000_000, "cost": 60.0, "requests": 300},
        "claude-haiku-4-5": {"tokens": 500_000, "cost": 0.5, "requests": 50},
    }
    text, saving, flags = diagnose_model_mix(usage, 65.0)
    assert "旗舰档" in text
    assert saving > 0
    assert flags["model_overuse"]


def test_diagnose_cost_concentration_with_low_token_share():
    """回归: premium 成本占 97% 但 token 只占 30% — 必须报超配(闸门看成本不看 token)。"""
    usage = {
        "claude-opus-5": {"tokens": 3_000_000, "cost": 60.0, "requests": 100},
        "claude-haiku-4-5": {"tokens": 7_000_000, "cost": 2.0, "requests": 500},
    }
    text, saving, flags = diagnose_model_mix(usage, 62.0)
    assert flags["model_overuse"]
    assert saving > 0


def test_diagnose_healthy_mix_no_saving():
    usage = {
        "claude-opus-5": {"tokens": 1_000_000, "cost": 8.0, "requests": 30},
        "claude-sonnet-5": {"tokens": 4_000_000, "cost": 12.0, "requests": 200},
        "claude-haiku-4-5": {"tokens": 5_000_000, "cost": 3.0, "requests": 400},
    }
    text, saving, flags = diagnose_model_mix(usage, 25.0)
    assert saving == 0.0
    assert not flags["model_overuse"]


def test_diagnose_no_fast_model_flags_gap():
    usage = {
        "claude-sonnet-5": {"tokens": 5_000_000, "cost": 20.0, "requests": 300},
    }
    text, saving, flags = diagnose_model_mix(usage, 20.0)
    assert "轻量模型" in text
    assert saving > 0
    assert flags["fast_gap"]
    assert not flags["model_overuse"]


# ---------------------------------------------------------------------------
# analyze_time_habits
# ---------------------------------------------------------------------------

def test_time_habits_no_data():
    insights, peaks, flags = analyze_time_habits(None)
    assert peaks == []
    assert "暂无" in insights[0].pattern
    assert flags["no_data"]


def test_time_habits_late_night_detected():
    histogram = [20, 25, 30, 15, 10, 5] + [2] * 18  # 深夜重度
    insights, peaks, flags = analyze_time_habits(histogram)
    assert any("深夜" in i.pattern for i in insights)
    assert flags["late_night"]


def test_time_habits_concentrated():
    histogram = [0] * 24
    histogram[9] = 50
    histogram[10] = 60
    histogram[14] = 40
    insights, peaks, flags = analyze_time_habits(histogram)
    assert peaks == [9, 10, 14]
    assert any("集中" in i.pattern for i in insights)
    assert flags["concentrated"]


def test_time_habits_fragmented():
    histogram = [3] * 24  # 全天均匀碎片化
    insights, peaks, flags = analyze_time_habits(histogram)
    assert any("碎片" in i.pattern for i in insights)
    assert flags["fragmented"]


# ---------------------------------------------------------------------------
# compute_checkup (360 式体检)
# ---------------------------------------------------------------------------

def test_checkup_clean_profile_high_scores():
    from cloud.coach import compute_checkup
    dims, issues = compute_checkup({}, {}, {}, None, 0.0)
    assert len(dims) == 4
    assert all(d.score >= 85 for d in dims)
    assert issues == []


def test_checkup_overuse_lowers_routing_and_creates_critical_issue():
    from cloud.coach import compute_checkup
    dims, issues = compute_checkup(
        {}, {"model_overuse": True, "fast_gap": True}, {}, None, 100.0
    )
    routing = next(d for d in dims if d.key == "routing")
    assert routing.score <= 40
    assert any(i.severity == "critical" and i.dimension == "模型路由" for i in issues)
    # critical 排在最前
    assert issues[0].severity == "critical"


def test_checkup_context_issues():
    from cloud.coach import compute_checkup
    dims, issues = compute_checkup(
        {"RULE_CONTEXT_BLOAT": True, "RULE_ERROR_RETRY_LOOP": True},
        {}, {}, 0.3, 0.0,
    )
    context = next(d for d in dims if d.key == "context")
    assert context.score <= 30
    assert sum(1 for i in issues if i.dimension == "上下文卫生") == 3


def test_checkup_every_issue_has_fix():
    from cloud.coach import compute_checkup
    _, issues = compute_checkup(
        {"RULE_CONTEXT_BLOAT": True, "RULE_MISSING_SKILL": True},
        {"model_overuse": True},
        {"late_night": True, "fragmented": True},
        0.2, 50.0,
    )
    assert len(issues) >= 5
    for i in issues:
        assert i.fix and i.impact  # 360 原则: 每个问题必须带修复方案


def test_verdict_text():
    from cloud.coach import build_verdict
    assert "优秀" in build_verdict(90, 0)
    assert "良好" in build_verdict(75, 3)
    assert "发现 3 个" in build_verdict(75, 3)
    assert "急需优化" in build_verdict(30, 5)


# ---------------------------------------------------------------------------
# build_money_texts — 计费口径 (回归: 订阅用户不得虚报省美元)
# ---------------------------------------------------------------------------

def test_money_texts_subscription_talks_quota_not_dollars():
    """回归: $200/月订阅用户,折算节省 $958 时绝不能说'每月可省 $958'。"""
    from cloud.coach import build_money_texts
    value, saving = build_money_texts(958.0, 626.0, "subscription", 200.0)
    combined = value + saving
    assert "每月可省" not in combined and "账单约省" not in combined
    assert "订阅" in value and "$200" in value  # 划算度一句
    assert "多干约" in saving and "限流" in saving  # 优化收益一句
    # 两句都要短 (一句话说一件事)
    assert len(value) < 60 and len(saving) < 55


def test_money_texts_api_mode_reports_real_dollars():
    from cloud.coach import build_money_texts
    value, saving = build_money_texts(100.0, 70.0, "api", None)
    assert value == ""
    assert "账单约省 $100" in saving


def test_money_texts_unknown_mode_is_conditional():
    from cloud.coach import build_money_texts
    value, saving = build_money_texts(100.0, 70.0, None, None)
    # 未知计费模式: 必须区分按量付费和订阅两种情况,不能一口咬定省钱
    assert "按量付费" in saving and "订阅" in saving


def test_money_texts_zero_saving():
    from cloud.coach import build_money_texts
    value, saving = build_money_texts(0.0, 70.0, "subscription", 200.0)
    assert saving == ""
    assert "订阅" in value  # 划算度与节省无关,仍然给


def test_report_carries_billing_fields(engine):
    coach, db = engine
    report = coach.generate_report(
        score=70, tier="B", total_tokens_7d=1_000_000, total_cost_7d=70.0,
        usage_by_model={"claude-opus-5": {"tokens": 900_000, "cost": 65.0, "requests": 100}},
        billing_mode="subscription", monthly_subscription_usd=200.0,
    )
    assert report.billing_mode == "subscription"
    assert report.value_text and report.saving_text
    # checkup 行动项里不允许出现绝对美元
    for issue in report.issues:
        assert "$" not in issue.impact


def test_small_sample_beat_text_uses_rank(engine):
    """回归: 小样本时说'N 位用户中排第几名',不说'百分位'术语。"""
    coach, db = engine
    for s in [40, 60, 90]:
        db.save_aggregated_score(
            AggregatedScore(score=s, tier="B", total_tokens_7d=100, total_cost_7d=1.0)
        )
    report = coach.generate_report(
        score=70, tier="B", total_tokens_7d=100, total_cost_7d=1.0
    )
    assert "排第 2 名" in report.beat_ratio_text  # 90 分在前,70 排第 2
    assert "百分位" not in report.beat_ratio_text


# ---------------------------------------------------------------------------
# optimizer — 一键优化
# ---------------------------------------------------------------------------

def test_optimizer_only_includes_hit_rules():
    from cloud.optimizer import build_optimization_md
    md = build_optimization_md(
        rule_hits={"RULE_CONTEXT_BLOAT": True},
        mix_flags={"model_overuse": True},
        time_flags={},
        cache_hit_rate=None,
    )
    assert "模型路由规则" in md
    assert "上下文纪律" in md
    assert "失败重试纪律" not in md  # 未命中不写
    assert "提示词缓存优化" not in md


def test_optimizer_clean_checkup_gives_minimal_block():
    from cloud.optimizer import build_optimization_md
    md = build_optimization_md({}, {}, {}, None)
    assert "保持当前用法" in md


def test_optimizer_apply_idempotent():
    from cloud.optimizer import apply_to_claude_md, build_optimization_md
    block1 = build_optimization_md({"RULE_CONTEXT_BLOAT": True}, {}, {}, None)
    existing = "# 我的规则\n\n自定义内容\n"
    v1 = apply_to_claude_md(existing, block1)
    assert "自定义内容" in v1 and "上下文纪律" in v1

    # 重跑: 替换区块而不是重复追加
    block2 = build_optimization_md({}, {"fast_gap": True}, {}, None)
    v2 = apply_to_claude_md(v1, block2)
    assert "自定义内容" in v2
    assert "模型路由规则" in v2
    assert "上下文纪律" not in v2  # 旧区块被替换
    assert v2.count("365-ai-master:optimization:start") == 1


# ---------------------------------------------------------------------------
# infer_goal_and_path
# ---------------------------------------------------------------------------

def test_goal_inference_coding_dominant():
    inference, path = infer_goal_and_path({"coding": 90, "writing": 10}, None, {})
    assert "工程开发" in inference
    assert "skill" in path


def test_goal_inference_includes_user_goal():
    inference, path = infer_goal_and_path({"writing": 80}, "做出爆款内容", {})
    assert "做出爆款内容" in inference


def test_goal_inference_scattered_usage():
    inference, path = infer_goal_and_path(
        {"coding": 30, "writing": 30, "research": 25, "data": 15}, None, {}
    )
    assert "分散" in inference or "分散" in path


def test_goal_inference_no_data():
    inference, path = infer_goal_and_path({}, None, {})
    assert "尚未上报" in inference


# ---------------------------------------------------------------------------
# build_action_items
# ---------------------------------------------------------------------------

def test_action_items_from_rules():
    items = build_action_items(
        {"RULE_CONTEXT_BLOAT": True, "RULE_MISSING_SKILL": True}, None, None
    )
    titles = [i.title for i in items]
    assert "精简会话上下文" in titles
    assert "沉淀高频任务为 skill" in titles


def test_action_items_max_three():
    all_rules = {
        "RULE_CONTEXT_BLOAT": True,
        "RULE_LOW_CACHE_HIT": True,
        "RULE_ERROR_RETRY_LOOP": True,
        "RULE_MODEL_OVERUSE": True,
        "RULE_MISSING_SKILL": True,
    }
    items = build_action_items(all_rules, {"model_overuse": True}, 0.2)
    assert len(items) == 3
    # 高优先级在前
    assert items[0].priority <= items[-1].priority


def test_action_items_default_when_clean():
    items = build_action_items({}, None, None)
    assert len(items) == 1
    assert "保持当前节奏" in items[0].title


def test_action_items_low_cache_added():
    items = build_action_items({}, None, 0.3)
    assert any("缓存" in i.title for i in items)


def test_action_items_fast_gap_not_conflated_with_overuse():
    """回归: 只缺轻量档(fast_gap)时不能出现'高配模型下放'这种自相矛盾的建议。"""
    items = build_action_items({}, {"model_overuse": False, "fast_gap": True}, None)
    titles = [i.title for i in items]
    assert "让轻量模型干杂活" in titles
    assert "高配模型下放" not in titles


def test_action_items_overuse_flag_drives_downgrade_advice():
    items = build_action_items({}, {"model_overuse": True, "fast_gap": False}, None)
    assert any("高配模型下放" in i.title for i in items)


# ---------------------------------------------------------------------------
# CoachEngine end-to-end (in-memory DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def engine():
    db = DatabaseEngine(db_url="sqlite:///:memory:")
    db.init_db()
    return CoachEngine(db), db


def test_full_report_generation(engine):
    coach, db = engine
    # 造一些同行数据
    for s in [40, 55, 60, 70, 90]:
        db.save_aggregated_score(
            AggregatedScore(score=s, tier="B", total_tokens_7d=1_000_000, total_cost_7d=10.0)
        )

    report = coach.generate_report(
        score=85,
        tier="A",
        total_tokens_7d=8_000_000,
        total_cost_7d=70.0,
        rule_hits={"RULE_MODEL_OVERUSE": True},
        usage_by_model={
            "claude-opus-5": {"tokens": 7_000_000, "cost": 60.0, "requests": 250},
            "claude-haiku-4-5": {"tokens": 1_000_000, "cost": 1.0, "requests": 100},
        },
        hourly_histogram=[10, 8, 6, 4, 2, 1] + [0] * 12 + [5, 10, 20, 30, 20, 10],
        task_types={"coding": 200, "writing": 40},
        goal="用 AI 建一人公司体系",
        cache_hit_rate=0.35,
    )

    assert report.global_percentile > 50
    # 样本 <20 时说名次,人人看得懂
    assert "位用户提交了数据" in report.beat_ratio_text
    assert "排第" in report.beat_ratio_text
    assert len(report.model_routing) == 4
    assert report.est_monthly_saving_usd > 0
    assert len(report.time_insights) >= 1
    assert "用 AI 建一人公司体系" in report.goal_inference
    assert "工程开发" in report.goal_inference
    assert 1 <= len(report.action_items) <= 3
    assert report.hourly_histogram is not None


def test_report_minimal_payload(engine):
    """老客户端只交核心字段也能生成报告(向后兼容)。"""
    coach, db = engine
    db.save_aggregated_score(
        AggregatedScore(score=50, tier="B", total_tokens_7d=100, total_cost_7d=1.0)
    )
    report = coach.generate_report(
        score=60, tier="B", total_tokens_7d=500_000, total_cost_7d=5.0
    )
    assert report.beat_ratio_text
    assert len(report.model_routing) == 4
    assert report.action_items


def test_migration_adds_columns_to_legacy_table():
    """回归(blocker): 旧 7 列 scores 表上 init_db 必须补齐新列,否则全线 500。"""
    from sqlalchemy import text

    db = DatabaseEngine("sqlite:///:memory:")
    # 模拟加列前部署建出的旧表
    with db.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE scores (
                id INTEGER PRIMARY KEY,
                score INTEGER NOT NULL,
                tier VARCHAR(1) NOT NULL,
                total_tokens_7d INTEGER NOT NULL,
                total_cost_7d FLOAT NOT NULL,
                rule_hits JSON,
                submitted_at DATETIME NOT NULL
            )
        """))
    db.init_db()  # 应通过 ALTER TABLE 补列

    # 旧客户端最小 payload 与富 payload 都必须能写入
    assert db.save_aggregated_score(
        AggregatedScore(score=70, tier="B", total_tokens_7d=100, total_cost_7d=1.0)
    )
    assert db.save_aggregated_score(
        AggregatedScore(
            score=80, tier="A", total_tokens_7d=200, total_cost_7d=2.0,
            task_types={"coding": 5}, goal="迁移测试",
        )
    )
    latest = db.get_latest_submission()
    assert latest["goal"] == "迁移测试"


def test_closed_loop_analysis_regression(engine):
    """回归: 规则命中下降时 analyze_closed_loop 必须能构造对比对象(曾缺必填字段)。"""
    from datetime import date
    from cloud.analytics import AnalyticsEngine
    from cloud.schemas import WeeklySummary

    coach, db = engine
    analytics = AnalyticsEngine(db)
    weeks = [
        WeeklySummary(
            week_start_date=date(2026, 7, 27), week_end_date=date(2026, 8, 2),
            avg_score=70.0, avg_tokens_per_day=100, total_cost=5.0,
            highest_score=75, lowest_score=65, days_with_data=7,
            most_frequent_rules_hit=["RULE_CONTEXT_BLOAT"],
        ),
        WeeklySummary(
            week_start_date=date(2026, 8, 3), week_end_date=date(2026, 8, 9),
            avg_score=80.0, avg_tokens_per_day=90, total_cost=4.0,
            highest_score=85, lowest_score=75, days_with_data=7,
            most_frequent_rules_hit=[],
        ),
    ]
    improvements = analytics.analyze_closed_loop(weeks)
    assert len(improvements) == 1
    assert improvements[0].rule_id == "RULE_CONTEXT_BLOAT"
    assert improvements[0].verified is True


def test_latest_submission_roundtrip(engine):
    """富字段入库后能完整取回。"""
    coach, db = engine
    payload = AggregatedScore(
        score=75,
        tier="A",
        total_tokens_7d=2_000_000,
        total_cost_7d=25.0,
        usage_by_model={"claude-sonnet-5": {"tokens": 2_000_000, "cost": 25.0, "requests": 90}},
        hourly_histogram=[1] * 24,
        task_types={"research": 50},
        goal="测试目标",
        cache_hit_rate=0.8,
    )
    assert db.save_aggregated_score(payload)
    latest = db.get_latest_submission()
    assert latest is not None
    assert latest["score"] == 75
    assert latest["goal"] == "测试目标"
    assert latest["task_types"] == {"research": 50}
    assert len(latest["hourly_histogram"]) == 24
