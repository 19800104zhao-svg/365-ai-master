from agentfit.models.events import UsageEvent
from agentfit.models.insights import HealthReport, InsightItem
from agentfit.analyzer.rules import (
    check_context_bloat, check_low_cache_hit,
    check_error_retries, check_model_overuse, check_missing_skills
)

class HealthAnalyzer:
    def analyze(self, events: list[UsageEvent]) -> HealthReport:
        insights: list[InsightItem] = []
        
        for rule_fn in [check_context_bloat, check_low_cache_hit, check_error_retries, check_model_overuse, check_missing_skills]:
            res = rule_fn(events)
            if res:
                insights.append(res)

        total_deduction = sum(i.deduction_points for i in insights)
        score = max(0, 100 - total_deduction)

        # 本地不编造百分位 — 真实全球排名只能来自云端 (agentfit sync)
        if score >= 90:
            tier = "S"
        elif score >= 75:
            tier = "A"
        elif score >= 60:
            tier = "B"
        else:
            tier = "C"
        percentile = f"{tier} 档 (真实全球排名请运行 agentfit sync 查看)"

        total_tokens = sum(e.input_tokens + e.output_tokens for e in events)
        # 按模型价目折算 (collector 里的定值不准) — API 折算价值,非订阅实际支出
        from agentfit.pricing import estimate_event_cost
        total_cost = sum(estimate_event_cost(e) for e in events)

        return HealthReport(
            score=score,
            tier=tier,
            percentile_text=percentile,
            total_tokens_7d=total_tokens,
            total_cost_7d=total_cost,
            insights=insights
        )
