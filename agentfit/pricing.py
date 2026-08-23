"""模型价目与成本估算 — scoring 与 sync 共用的唯一口径。

重要: 这里算出的是「按 API 价目折算的用量价值」。
订阅用户 (Claude Pro/Max 等) 的实际支出是固定月费,
折算值只用于衡量用量规模与结构,不代表真实花费。
"""
from agentfit.models.events import UsageEvent

# USD per MTok: (input, output, cache_read) — 按官方价目的档位近似
TIER_PRICES = {
    "premium": (15.0, 75.0, 1.5),
    "balanced": (3.0, 15.0, 0.3),
    "fast": (1.0, 5.0, 0.1),
}

PREMIUM_KEYWORDS = ("opus", "fable", "mythos")
FAST_KEYWORDS = ("haiku", "-mini", "nano", "flash", "lite")


def classify_model(name: str) -> str:
    lower = name.lower()
    if any(k in lower for k in PREMIUM_KEYWORDS):
        return "premium"
    if any(k in lower for k in FAST_KEYWORDS):
        return "fast"
    return "balanced"


def estimate_event_cost(e: UsageEvent) -> float:
    """按模型档位价目估算单事件的 API 折算价值 (USD)。"""
    p_in, p_out, p_cache = TIER_PRICES[classify_model(e.model)]
    return (
        e.input_tokens / 1e6 * p_in
        + e.output_tokens / 1e6 * p_out
        + e.cache_read_tokens / 1e6 * p_cache
    )
