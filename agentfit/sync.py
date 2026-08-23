"""agentfit sync — 把本地真实用量聚合后上报云端,体检从演示数据变成你的数据。

隐私边界: 只上报聚合数字 (分数/token/成本/模型分布/小时分布/规则命中),
不上报任何提示词内容、文件路径 (项目路径本地已哈希) 或会话文本。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from agentfit.analyzer.scoring import HealthAnalyzer
from agentfit.models.events import UsageEvent
from agentfit.pricing import classify_model, estimate_event_cost  # noqa: F401 (re-export)

DEFAULT_API_URL = "https://360-ai-coach-production.up.railway.app"
CONFIG_PATH = Path.home() / ".agentfit" / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def get_or_create_device_token(cfg: dict) -> str:
    """返回本机匿名设备令牌, 无则生成并写进 cfg (由调用方 save_config 持久化)。

    这是隐私边界的一部分: token 是随机 uuid, 不含任何个人信息, 只用于把
    上报的聚合数字归属到「本人」而非全局, 让首页不再泄露/劫持他人数据。
    """
    token = cfg.get("device_token")
    if not token:
        import uuid

        token = uuid.uuid4().hex
        cfg["device_token"] = token
    return token


def build_profile(
    events: list[UsageEvent],
    goal: str | None = None,
    billing_mode: str | None = None,
    monthly_subscription_usd: float | None = None,
) -> dict[str, Any]:
    """把本地事件聚合成云端 /coach/analyze 的上报画像。

    billing_mode: "subscription" (固定月费) 或 "api" (按量计费)。
    订阅模式下 total_cost_7d 是「API 折算价值」而非真实支出,
    云端会据此换算成额度口径而不是虚报省钱金额。
    """
    report = HealthAnalyzer().analyze(events)

    usage_by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"tokens": 0, "cost": 0.0, "requests": 0}
    )
    hourly = [0] * 24
    total_input = 0
    total_cache_read = 0
    total_cost = 0.0

    for e in events:
        # 跳过内部合成消息与零用量事件 (如 <synthetic>)
        if e.input_tokens + e.output_tokens == 0:
            continue
        m = usage_by_model[e.model]
        m["tokens"] += e.input_tokens + e.output_tokens
        m["cost"] += estimate_event_cost(e)
        m["requests"] += 1
        try:
            hourly[e.timestamp.astimezone().hour] += 1
        except Exception:
            hourly[e.timestamp.hour] += 1
        total_input += e.input_tokens
        total_cache_read += e.cache_read_tokens
        total_cost += estimate_event_cost(e)

    for m in usage_by_model.values():
        m["cost"] = round(m["cost"], 4)

    cache_denominator = total_input + total_cache_read
    cache_hit_rate = (
        round(total_cache_read / cache_denominator, 4) if cache_denominator > 0 else None
    )

    profile: dict[str, Any] = {
        "score": report.score,
        "tier": report.tier,
        "total_tokens_7d": report.total_tokens_7d,
        "total_cost_7d": round(total_cost, 2),
        "rule_hits": {i.rule_id: True for i in report.insights},
        "usage_by_model": dict(usage_by_model),
        "hourly_histogram": hourly if sum(hourly) > 0 else None,
    }
    if goal:
        profile["goal"] = goal[:500]
    if cache_hit_rate is not None:
        profile["cache_hit_rate"] = min(1.0, cache_hit_rate)
    if billing_mode in ("subscription", "api"):
        profile["billing_mode"] = billing_mode
    if monthly_subscription_usd is not None and monthly_subscription_usd > 0:
        profile["monthly_subscription_usd"] = monthly_subscription_usd
    return profile


def post_profile(api_url: str, profile: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    """POST 到 /coach/analyze,返回教练报告。"""
    url = api_url.rstrip("/") + "/api/v1/coach/analyze"
    req = urllib.request.Request(
        url,
        data=json.dumps(profile).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


LAUNCHD_LABEL = "com.agentfit.sync"


def install_daily_schedule(hour: int = 21, minute: int = 0) -> Path:
    """安装 macOS launchd 定时任务: 每天定时跑 agentfit sync。返回 plist 路径。"""
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LAUNCHD_LABEL}.plist"
    log_path = Path.home() / ".agentfit" / "sync.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>agentfit.cli</string>
        <string>sync</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key><string>{log_path}</string>
    <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist)
    return plist_path
