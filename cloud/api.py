"""FastAPI routes for AgentFit Cloud API."""
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from typing import Optional
import hmac
import logging
import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from cloud.models import (
    AggregatedScore,
    PercentileQuery,
    PercentileResult,
    APIResponse,
    StatisticsResponse,
    CoachReport,
)
from cloud.database import DatabaseEngine
from cloud.config import settings
from cloud.analytics import AnalyticsEngine
from cloud.coach import CoachEngine
from cloud.schemas import TrendAnalysis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["agentfit"])
db = DatabaseEngine(db_url=settings.database_url)
analytics = AnalyticsEngine(db)
coach = CoachEngine(db)


def _api_key_ok(x_api_key: Optional[str]) -> bool:
    """运营接口鉴权: 必须配置了非默认 API_KEY 且常量时间比较相等。"""
    if settings.api_key in ("", "dev-key-change-in-production"):
        return False
    return hmac.compare_digest(x_api_key or "", settings.api_key)


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> bool:
    """Verify API key if required."""
    if not settings.require_api_key:
        return True
    return _api_key_ok(x_api_key)


# ---------------------------------------------------------------------------
# 写接口频控 (进程内滑动窗口, 按客户端 IP)。
# 单 worker 部署下够用; 目的是挡脚本灌分, 不是防 DDoS。
# 阈值走 settings.rate_limit_requests_per_minute (默认 60/min)。
# ---------------------------------------------------------------------------
_rate_buckets: dict = defaultdict(deque)
_rate_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    if not settings.rate_limit_enabled:
        return
    limit = settings.rate_limit_requests_per_minute
    now = time.monotonic()
    ip = _client_ip(request)
    with _rate_lock:
        bucket = _rate_buckets[ip]
        while bucket and now - bucket[0] > 60.0:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests, slow down")
        bucket.append(now)


def _resolve_token(query_token: Optional[str], header_token: Optional[str]) -> Optional[str]:
    """设备令牌来源: X-Device-Token 头优先, 兼容 ?token= 查询参数。

    头部不会进 uvicorn access log, 前端一律走头部; 查询参数保留给
    CLI 打印的一次性绑定链接与旧客户端。
    """
    token = (header_token or query_token or "").strip()
    if not token:
        return None
    if len(token) > 64:
        raise HTTPException(status_code=422, detail="Invalid device token")
    return token


@router.post("/submit", status_code=201, response_model=APIResponse, dependencies=[Depends(rate_limit)])
async def submit_score(
    payload: AggregatedScore,
    x_api_key: Optional[str] = Header(None),
) -> APIResponse:
    """
    Submit anonymized AI Health Score to the cloud benchmark dataset.

    **Privacy:** Only score, tier, token counts, and rule hits are accepted.
    No prompt text, file paths, or user identifiers are stored.

    **Authentication (optional):** Include X-API-Key header if API_KEY is set.
    """
    if not settings.enable_submissions:
        raise HTTPException(status_code=503, detail="Submissions currently disabled")

    if settings.require_api_key and not verify_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    success = db.save_aggregated_score(payload)

    if not success:
        logger.error(f"Failed to save score: {payload.score}")
        raise HTTPException(status_code=500, detail="Failed to save score")

    logger.info(f"Score submitted: {payload.score} ({payload.tier})")

    return APIResponse(
        success=True,
        message="Score submitted successfully"
    )


@router.get("/percentile", response_model=PercentileResult)
async def get_percentile(
    score: int = Query(..., ge=0, le=100, description="Your AI Health Score (0-100)"),
    x_api_key: Optional[str] = Header(None),
) -> PercentileResult:
    """
    Query the percentile ranking for your AI Health Score.

    Returns your peer percentile (0-100) and ranking tier.
    No authentication required; queries are not logged or stored.
    """
    if not settings.enable_percentile_queries:
        raise HTTPException(status_code=503, detail="Percentile queries currently disabled")

    if score < 0 or score > 100:
        raise HTTPException(status_code=400, detail="Score must be between 0 and 100")

    try:
        percentile = db.get_percentile_for_score(score)
        stats = db.get_statistics()
        total_samples = stats.get("total_submissions", 0)

        # Classify into quartiles
        if percentile < 25:
            tier = "Bottom 25%"
        elif percentile < 50:
            tier = "25-50%"
        elif percentile < 75:
            tier = "50-75%"
        else:
            tier = "Top 25%"

        logger.info(f"Percentile query: score={score}, percentile={percentile}")

        return PercentileResult(
            score=score,
            percentile=percentile,
            total_samples=total_samples,
            ranking_tier=tier,
        )
    except Exception as e:
        logger.error(f"Error calculating percentile for score {score}: {e}")
        raise HTTPException(status_code=500, detail="Error calculating percentile")


@router.get("/stats", response_model=StatisticsResponse)
async def get_stats():
    """
    Get public statistics about the dataset.

    Returns aggregated metrics: total submissions, average score, average tokens.
    No authentication required.
    """
    if not settings.enable_stats:
        raise HTTPException(status_code=503, detail="Statistics currently disabled")

    try:
        stats = db.get_statistics()
        distribution = db.get_score_distribution()

        logger.info("Stats query")

        return StatisticsResponse(
            total_submissions=stats.get("total_submissions", 0),
            avg_score=stats.get("avg_score", 0.0),
            avg_tokens_7d=stats.get("avg_tokens_7d", 0),
            score_distribution=distribution,
        )
    except Exception as e:
        logger.error(f"Error retrieving stats: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving statistics")


@router.get("/analytics/30day", response_model=dict)
async def get_30day_analytics(
    token: Optional[str] = Query(None, max_length=64),
    x_device_token: Optional[str] = Header(None),
):
    """
    Get 30-day trend analysis with weekly breakdowns and closed-loop improvements.

    需带设备令牌 (X-Device-Token 头或 ?token=) 才返回本设备的趋势;
    无 token 返回空趋势, 绝不把全库分数曲线泄露给匿名访客。
    """
    if not settings.enable_analytics:
        raise HTTPException(status_code=503, detail="Analytics currently disabled")

    token = _resolve_token(token, x_device_token)
    if not token:
        return {
            "weeks": [],
            "overall_trend": "insufficient_data",
            "trend_score": 0.0,
            "improvements_this_month": [],
            "summary_text": "",
        }

    try:
        analysis = analytics.get_30day_analysis(device_token=token)
        data = analysis.model_dump()

        # Ensure weeks are properly formatted for frontend
        data['weeks'] = [
            {
                'week': f"周 {i+1}",
                'week_start_date': str(w.week_start_date),
                'avg_score': round(w.avg_score, 1),
                'avg_tokens_per_day': w.avg_tokens_per_day,
                'total_cost': round(w.total_cost, 2),
                'highest_score': w.highest_score,
                'lowest_score': w.lowest_score,
                'days_with_data': w.days_with_data,
                'rules': w.most_frequent_rules_hit
            }
            for i, w in enumerate(analysis.weeks)
        ]

        logger.info(f"Analytics query: trend={analysis.overall_trend}, weeks={len(analysis.weeks)}")

        return data
    except Exception as e:
        logger.error(f"Error retrieving analytics: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving analytics")


@router.get("/analytics/export", response_class=Response)
async def export_analytics(
    format: str = Query("csv", regex="^(csv|json)$", description="Export format: csv or json"),
    token: Optional[str] = Query(None, max_length=64),
    x_api_key: Optional[str] = Header(None),
):
    """
    Export analytics data in CSV or JSON format.

    [已收敛] 全量导出锁死: 仅配置了 API_KEY 的运营方可用,匿名访客拿不到任何 CSV。
    带 ?token= 时只导出该设备自己的数据。
    """
    if not settings.enable_analytics:
        raise HTTPException(status_code=503, detail="Analytics currently disabled")

    if not _api_key_ok(x_api_key):
        raise HTTPException(status_code=401, detail="Export requires API key")

    try:
        if format == "csv":
            csv_data = analytics.export_as_csv(device_token=token)
            return Response(
                content=csv_data,
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=agentfit-analytics.csv"}
            )
        else:  # json
            json_data = analytics.export_as_json(device_token=token)
            return Response(
                content=json.dumps(json_data, indent=2, default=str),
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=agentfit-analytics.json"}
            )
    except Exception as e:
        logger.error(f"Error exporting analytics: {e}")
        raise HTTPException(status_code=500, detail="Error exporting analytics")


@router.post("/coach/analyze", response_model=CoachReport, dependencies=[Depends(rate_limit)])
async def coach_analyze(
    payload: AggregatedScore,
    x_api_key: Optional[str] = Header(None),
) -> CoachReport:
    """
    提交完整使用画像并获取教练报告。

    在 /submit 基础上支持富字段: usage_by_model (模型级用量)、
    hourly_histogram (24 小时分布)、task_types (任务类型)、goal (目标)、
    cache_hit_rate。数据入库后返回:
    - 全球排名 (打败 X% 的 AI 用户)
    - 模型路由建议 (什么任务用什么模型) 与预估节省
    - 时间习惯洞察 (什么时间怎么用)
    - 目标推断与路径建议
    - 优先级行动清单
    """
    if not settings.enable_submissions:
        raise HTTPException(status_code=503, detail="Submissions currently disabled")

    if settings.require_api_key and not verify_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not db.save_aggregated_score(payload):
        raise HTTPException(status_code=500, detail="Failed to save profile")

    try:
        report = coach.generate_report(
            score=payload.score,
            tier=payload.tier,
            total_tokens_7d=payload.total_tokens_7d,
            total_cost_7d=payload.total_cost_7d,
            rule_hits=payload.rule_hits,
            usage_by_model=payload.usage_by_model,
            hourly_histogram=payload.hourly_histogram,
            task_types=payload.task_types,
            goal=payload.goal,
            cache_hit_rate=payload.cache_hit_rate,
            billing_mode=payload.billing_mode,
            monthly_subscription_usd=payload.monthly_subscription_usd,
        )
        logger.info(f"Coach report generated: score={payload.score}, percentile={report.global_percentile}")
        return report
    except Exception as e:
        logger.error(f"Error generating coach report: {e}")
        raise HTTPException(status_code=500, detail="Error generating coach report")


def _report_for_token(token: str) -> CoachReport:
    """按 device_token 生成该设备自己的教练报告。

    只读该 token 的最近一次提交 — 从根上杜绝把陌生人 (或作者本人) 的
    真实数据当成「你的」返回给任意访客。无该 token 数据时 404。
    """
    latest = db.get_latest_submission(device_token=token)
    if latest is None:
        raise HTTPException(status_code=404, detail="No submissions yet for this device")
    try:
        return coach.generate_report(
            score=latest["score"],
            tier=latest["tier"],
            total_tokens_7d=latest["total_tokens_7d"],
            total_cost_7d=latest["total_cost_7d"],
            rule_hits=latest["rule_hits"],
            usage_by_model=latest["usage_by_model"],
            hourly_histogram=latest["hourly_histogram"],
            task_types=latest["task_types"],
            goal=latest["goal"],
            cache_hit_rate=latest["cache_hit_rate"],
            billing_mode=latest.get("billing_mode"),
            monthly_subscription_usd=latest.get("monthly_subscription_usd"),
        )
    except Exception as e:
        logger.error(f"Error generating coach report: {e}")
        raise HTTPException(status_code=500, detail="Error generating coach report")


@router.get("/coach/mine", response_model=CoachReport)
async def coach_mine(
    token: Optional[str] = Query(None, max_length=64, description="匿名设备令牌 (兼容参数)"),
    x_device_token: Optional[str] = Header(None),
) -> CoachReport:
    """获取本设备自己的教练报告 (首页主接口)。

    令牌来自 X-Device-Token 头 (推荐) 或 ?token=。
    404 表示该设备尚无提交 (真正的空状态,前端据此进入引导)。
    """
    token = _resolve_token(token, x_device_token)
    if not token:
        raise HTTPException(status_code=422, detail="Device token required (X-Device-Token header or ?token=)")
    return _report_for_token(token)


@router.get("/coach/latest", response_model=CoachReport)
async def coach_latest(
    token: Optional[str] = Query(None, max_length=64),
    x_device_token: Optional[str] = Header(None),
) -> CoachReport:
    """[已收敛] 旧的无鉴权「全局最新」接口已关闭。

    必须带设备令牌 (等价 /coach/mine);不带一律 404,
    不再向任意访客泄露任何人的真实体检数据。
    """
    token = _resolve_token(token, x_device_token)
    if not token:
        raise HTTPException(status_code=404, detail="Provide a device token or use /coach/mine")
    return _report_for_token(token)


@router.get("/coach/optimize")
async def coach_optimize(
    token: Optional[str] = Query(None, max_length=64, description="匿名设备令牌 (兼容参数)"),
    x_device_token: Optional[str] = Header(None),
):
    """一键优化: 把本设备最近一次体检编译成 CLAUDE.md 配置块。

    需带设备令牌 (X-Device-Token 头或 ?token=);返回 {"markdown": ...};
    404 表示该设备尚无体检数据。
    """
    token = _resolve_token(token, x_device_token)
    if not token:
        raise HTTPException(status_code=422, detail="Device token required (X-Device-Token header or ?token=)")
    latest = db.get_latest_submission(device_token=token)
    if latest is None:
        raise HTTPException(status_code=404, detail="No submissions yet for this device")

    try:
        from cloud.coach import analyze_time_habits, diagnose_model_mix
        from cloud.optimizer import build_optimization_md

        _, _, mix_flags = diagnose_model_mix(
            latest["usage_by_model"], latest["total_cost_7d"]
        )
        _, _, time_flags = analyze_time_habits(latest["hourly_histogram"])
        task_types = latest["task_types"] or {}
        dominant = max(task_types, key=task_types.get) if task_types else None

        markdown = build_optimization_md(
            rule_hits=latest["rule_hits"],
            mix_flags=mix_flags,
            time_flags=time_flags,
            cache_hit_rate=latest["cache_hit_rate"],
            dominant_task=dominant,
        )
        return {"markdown": markdown}
    except Exception as e:
        logger.error(f"Error building optimization: {e}")
        raise HTTPException(status_code=500, detail="Error building optimization")


@router.get("/master/daily")
async def master_daily():
    """365 AI Master 今日推荐: 技巧 / 能力 / 安全提醒各一条,按日轮换。

    内容池 = 静态种子池 + 采集 pipeline 入库的动态条目;
    每条带来源与可信依据,涉及隐私的条目带显式 privacy_note。
    """
    from cloud.master import get_daily_feed
    try:
        extra = db.get_active_master_tips()
    except Exception:
        extra = []
    return get_daily_feed(extra_pool=extra).model_dump()


@router.get("/master/library")
async def master_library():
    """全部技能库: 静态种子池 + 动态入库条目,按类型分组。

    用于「今日精选看完了,想看更多」的浏览场景。
    返回 {"total": n, "groups": [{"kind","label","items":[...]}]}
    """
    from cloud.master import CONTENT_POOL, MasterTip

    items = [t.model_dump() for t in CONTENT_POOL]
    try:
        for extra in db.get_active_master_tips():
            try:
                items.append(MasterTip(**extra).model_dump())
            except Exception:
                continue  # 坏数据不进库
    except Exception:
        pass

    labels = [
        ("tip", "使用技巧"),
        ("skill", "Skill 推荐"),
        ("agent", "Agent 用法"),
        ("security", "安全提醒"),
        ("product", "新品甄别"),
    ]
    groups = []
    for kind, label in labels:
        group_items = [i for i in items if i.get("kind") == kind]
        if group_items:
            groups.append({"kind": kind, "label": label, "items": group_items})

    return {"total": len(items), "groups": groups}


@router.post("/master/retire", response_model=APIResponse)
async def master_retire(
    payload: dict,
    x_api_key: Optional[str] = Header(None),
):
    """下架动态库中的一条内容 (按 title,需要 X-API-Key)。

    保留记录只置 active=0,便于追溯;不做物理删除。
    """
    if settings.api_key in ("", "dev-key-change-in-production"):
        raise HTTPException(status_code=503, detail="Retire disabled: API_KEY not configured")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid api key")

    title = str(payload.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=422, detail="缺少 title")

    if not db.retire_master_tip(title):
        raise HTTPException(status_code=404, detail="未找到该条目")

    logger.info(f"Master tip retired: {title[:40]}")
    return APIResponse(success=True, message="已下架")


@router.post("/master/submit", status_code=201, response_model=APIResponse, dependencies=[Depends(rate_limit)])
async def master_submit(
    payload: dict,
    x_api_key: Optional[str] = Header(None),
):
    """采集 pipeline 提交甄别后的推荐条目 (需要 X-API-Key)。

    质量底线在此强制: kind/title/detail/why_trust/source 必填;
    API_KEY 未配置或为默认值时端点关闭。
    """
    if settings.api_key in ("", "dev-key-change-in-production"):
        raise HTTPException(status_code=503, detail="Submit disabled: API_KEY not configured")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    required = ["kind", "title", "detail", "why_trust", "source"]
    missing = [f for f in required if not str(payload.get(f, "")).strip()]
    if missing:
        raise HTTPException(status_code=422, detail=f"缺少必填字段: {', '.join(missing)}")
    if payload["kind"] not in ("tip", "skill", "agent", "product", "security"):
        raise HTTPException(status_code=422, detail="kind 不合法")

    if not db.add_master_tip(payload):
        raise HTTPException(status_code=500, detail="入池失败")

    logger.info(f"Master tip submitted: {payload['title'][:40]}")
    return APIResponse(success=True, message="已入池")


@router.post("/subscribe", status_code=201, response_model=APIResponse, dependencies=[Depends(rate_limit)])
async def subscribe(payload: dict):
    """订阅 AI 资讯与新品推荐 (邮箱)。幂等: 重复订阅返回成功。"""
    import re as _re

    email = str(payload.get("email", "")).strip().lower()
    if not _re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email) or len(email) > 320:
        raise HTTPException(status_code=422, detail="邮箱格式不正确")

    if not db.add_subscriber(email):
        raise HTTPException(status_code=500, detail="订阅失败,请稍后再试")

    logger.info(f"New subscriber: {email[:3]}***")
    return APIResponse(success=True, message="已加入早鸟名单,日报上线第一时间通知你")


@router.get("/master/stats")
async def master_stats(x_api_key: Optional[str] = Header(None)):
    """运营统计 (需 X-API-Key): 订阅数 + 动态池条目数。30 天退出闸用。"""
    if not _api_key_ok(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {
        "subscribers": db.count_subscribers(),
        "dynamic_tips": len(db.get_active_master_tips()),
    }


@router.post("/billing/checkout", dependencies=[Depends(rate_limit)])
async def billing_checkout(payload: Optional[dict] = None):
    """创建 Stripe Checkout 会话 (Pro $1/月 订阅),返回跳转 URL。

    未配置 Stripe (STRIPE_SECRET_KEY / STRIPE_PRICE_ID) 时返回 503,
    前端据此回退到早鸟名单。
    """
    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="Billing not configured yet")

    import stripe
    stripe.api_key = settings.stripe_secret_key

    email = (payload or {}).get("email") or None
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
            success_url=f"{settings.site_url}/?upgraded=1#pro",
            cancel_url=f"{settings.site_url}/#pro",
            customer_email=email,
            allow_promotion_codes=True,
        )
        logger.info("Stripe checkout session created")
        return {"url": session.url}
    except Exception as e:
        logger.error(f"Stripe checkout error: {e}")
        raise HTTPException(status_code=502, detail="支付服务暂时不可用")


@router.post("/billing/webhook")
async def billing_webhook(request: Request):
    """Stripe webhook: 维护 Pro 订阅状态。

    处理 checkout.session.completed (开通) 与
    customer.subscription.deleted (取消)。验签失败返回 400。
    """
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    import stripe

    raw = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(
            raw, signature, settings.stripe_webhook_secret
        )
    except Exception as e:
        logger.warning(f"Stripe webhook signature failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event["type"]
    obj = event["data"]["object"]
    # stripe SDK 返回的是 StripeObject (Session/Subscription), 不是 dict —
    # 直接 .get() 会抛 AttributeError。统一转成纯 dict 再读字段。
    for conv in ("to_dict_recursive", "to_dict"):
        if hasattr(obj, conv):
            obj = getattr(obj, conv)()
            break
    else:
        obj = dict(obj)

    if etype == "checkout.session.completed":
        details = obj.get("customer_details") or {}
        email = details.get("email") or obj.get("customer_email") or ""
        db.upsert_pro_subscription(
            email=email.lower(),
            stripe_customer_id=obj.get("customer"),
            stripe_subscription_id=obj.get("subscription"),
            status="active",
        )
        logger.info("Pro subscription activated")
    elif etype == "customer.subscription.deleted":
        db.upsert_pro_subscription(
            email="",
            stripe_customer_id=obj.get("customer"),
            stripe_subscription_id=obj.get("id"),
            status="canceled",
        )
        logger.info("Pro subscription canceled")

    return {"received": True}


@router.get("/billing/status")
async def billing_status(
    email: str = Query(..., max_length=320),
    x_api_key: Optional[str] = Header(None),
):
    """查询邮箱的 Pro 状态: active / canceled / none (运营接口, 需 X-API-Key)。

    [已收敛] 此前匿名可查任意邮箱, 等于付费用户邮箱枚举通道; 前端并不使用它。
    """
    if not _api_key_ok(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"email": email.lower(), "status": db.get_pro_status(email.lower())}


@router.get("/health")
async def health_check():
    """Health check endpoint for load balancers."""
    return {"status": "ok", "service": "agentfit-cloud"}
