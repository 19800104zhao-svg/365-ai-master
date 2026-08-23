from agentfit.models.events import UsageEvent
from agentfit.models.insights import InsightItem

def check_context_bloat(events: list[UsageEvent]) -> InsightItem | None:
    if len(events) < 25:
        return None
    
    mid = len(events) // 2
    first_half_avg = sum(e.input_tokens for e in events[:mid]) / mid if mid > 0 else 0
    second_half_avg = sum(e.input_tokens for e in events[mid:]) / (len(events) - mid) if (len(events) - mid) > 0 else 0
    
    if first_half_avg > 0 and (second_half_avg / first_half_avg) >= 1.8:
        return InsightItem(
            rule_id="RULE_CONTEXT_BLOAT",
            title="上下文暴涨黑洞",
            severity="high",
            deduction_points=18,
            evidence=f"会话共有 {len(events)} 轮交互，后半段平均 Input Token 比前半段暴涨 {(second_half_avg/first_half_avg - 1)*100:.0f}%。",
            recommendation="完成阶段性子目标后及时开启新会话，并将已完成结论保存至项目 Context 文档中。",
            expected_savings_tokens=int(sum(e.input_tokens for e in events[mid:]) * 0.4)
        )
    return None

def check_low_cache_hit(events: list[UsageEvent]) -> InsightItem | None:
    total_input = sum(e.input_tokens for e in events)
    total_cache = sum(e.cache_read_tokens for e in events)
    
    if total_input > 10000 and (total_cache / total_input) < 0.20:
        return InsightItem(
            rule_id="RULE_LOW_CACHE_HIT",
            title="缓存打靶率低下",
            severity="medium",
            deduction_points=12,
            evidence=f"7天 Input Token 为 {total_input}，缓存命中率仅为 {(total_cache/total_input)*100:.1f}%（同类目标 ≥ 60%）。",
            recommendation="将静态规则集中放置在 CLAUDE.md / AGENTS.md，保持 Prompt 前缀结构稳定。",
            expected_savings_tokens=int(total_input * 0.3)
        )
    return None

def check_error_retries(events: list[UsageEvent]) -> InsightItem | None:
    error_events = [e for e in events if e.has_error]
    error_count = len(error_events)
    if error_count >= 3:
        # 按错误事件的实际 token 估算浪费,不用拍脑袋定值
        wasted = sum(e.input_tokens + e.output_tokens for e in error_events)
        return InsightItem(
            rule_id="RULE_ERROR_RETRY_LOOP",
            title="死循环盲目 Retry",
            severity="critical",
            deduction_points=22,
            evidence=f"检测到 {error_count} 次重复发生的错误与重试，未进行有效的 Prompt/上下文调整。",
            recommendation="连续失败 2 次后强制中断 Agent 并重新总结根因，不要原样连续 Retry。",
            expected_savings_tokens=wasted
        )
    return None

def check_model_overuse(events: list[UsageEvent]) -> InsightItem | None:
    high_tier_count = sum(1 for e in events if "opus" in e.model.lower() or "sonnet" in e.model.lower())
    if len(events) > 10 and (high_tier_count / len(events)) > 0.8:
        # 按实际高档用量估算可下放价值 (约四成可下放,轻量档价格约 1/10)
        from agentfit.pricing import classify_model, estimate_event_cost
        high_tier_cost = sum(
            estimate_event_cost(e) for e in events if classify_model(e.model) != "fast"
        )
        savings = round(high_tier_cost * 0.4 * 0.9, 2)
        return InsightItem(
            rule_id="RULE_MODEL_OVERUSE",
            title="高级模型杀鸡用牛刀",
            severity="low",
            deduction_points=10,
            evidence=f"在全量交互中，{high_tier_count/len(events)*100:.0f}% 的请求使用了最高档位的昂贵模型。",
            recommendation="对于文件定位、简单格式调整与搜索任务，优先使用 Flash/Light 快捷模型。",
            expected_savings_usd=savings
        )
    return None

def check_missing_skills(events: list[UsageEvent]) -> InsightItem | None:
    # Basic skill check stub
    if len(events) > 40:
        return InsightItem(
            rule_id="RULE_MISSING_SKILL",
            title="缺乏 Custom Skill 封装",
            severity="medium",
            deduction_points=10,
            evidence="检测到在多个 Session 中重复输入了相似的初始环境说明与构建命令。",
            recommendation="将高频使用的操作命令与约束规则封装为自定义 `.claude/skills/` 技能。",
            expected_savings_tokens=80000
        )
    return None
