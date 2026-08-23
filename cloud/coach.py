"""AgentFit Coach Engine.

把用户的 token 用量、模型分布、时间习惯、任务类型翻译成可执行的优化建议:
- 模型路由: 什么任务用什么模型
- 时间习惯: 什么时间怎么用
- 全球排名: 打败了多少 AI 用户
- 目标推断: 用户到底想干什么, 路径怎么走
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from cloud.models import (
    ActionItem,
    CheckupIssue,
    CoachReport,
    DimensionScore,
    ModelRoutingAdvice,
    TimeInsight,
)

# ---------------------------------------------------------------------------
# 模型分层知识库 (按名称子串归类, 大小写不敏感)
# ---------------------------------------------------------------------------
PREMIUM_KEYWORDS = ("opus", "fable", "mythos", "o1-pro", "o3-pro", "ultra", "gpt-5-pro")
# "-mini" 带连字符, 避免误伤 "gemini"
FAST_KEYWORDS = ("haiku", "-mini", "nano", "flash", "lite", "8b", "instant")
# 其余归为 balanced (sonnet / gpt-4o / gemini-pro 等)

# fast 档价格约为 premium 档的 1/10 — 用于节省估算
FAST_VS_PREMIUM_COST_RATIO = 0.1
# 假设 premium 用量中约 40% 是本可下放的常规任务 (保守估计)
ROUTABLE_SHARE_OF_PREMIUM = 0.4

TASK_LABELS = {
    "coding": "工程开发",
    "writing": "内容写作",
    "research": "研究分析",
    "data": "数据处理",
    "chat": "对话咨询",
    "design": "设计创意",
}

RULE_ADVICE = {
    "RULE_CONTEXT_BLOAT": ActionItem(
        priority=1,
        title="精简会话上下文",
        detail="聊天记录越滚越长,AI 每次都要重读全部历史,又慢又费钱。"
        "一个任务聊完就开新对话;常用的背景说明存成固定模板,别每次重新贴。",
        expected_impact="单次对话 token 消耗下降 30-50%",
    ),
    "RULE_LOW_CACHE_HIT": ActionItem(
        priority=2,
        title="提高缓存命中率",
        detail="提示词缓存能让重复内容按约 1 折计费。把 system prompt 和固定参考资料"
        "放在提示词开头并保持稳定,变化的内容放末尾。",
        expected_impact="输入成本最高降 90%",
    ),
    "RULE_ERROR_RETRY_LOOP": ActionItem(
        priority=1,
        title="终止无效重试",
        detail="同一个任务反复失败还在原样重发。连续失败 2 次就停下来: 换个问法、"
        "把任务拆小,或者补充关键信息,别硬试。",
        expected_impact="消灭无效消耗,通常占浪费的 10-20%",
    ),
    "RULE_MODEL_OVERUSE": ActionItem(
        priority=1,
        title="高配模型下放",
        detail="大量简单任务在用最贵的模型。按下面的对照表,把机械杂活交给轻量模型干。",
        expected_impact="成本或额度消耗下降 30-60%",
    ),
    "RULE_MISSING_SKILL": ActionItem(
        priority=3,
        title="沉淀高频任务为 skill",
        detail="同样的活每次都从头教 AI 一遍。把常干的活写成 skill 模板,一次写好反复用,结果还更稳定。",
        expected_impact="重复任务耗时下降一半以上",
    ),
}


def classify_model(name: str) -> str:
    """按模型名称归入 premium / balanced / fast 三档。"""
    lower = name.lower()
    if any(k in lower for k in PREMIUM_KEYWORDS):
        return "premium"
    if any(k in lower for k in FAST_KEYWORDS):
        return "fast"
    return "balanced"


def build_routing_table(dominant_task: Optional[str]) -> List[ModelRoutingAdvice]:
    """任务类型 → 推荐模型路由表。始终返回,有主线任务时置顶相关行。"""
    table = [
        ModelRoutingAdvice(
            task="机械任务(格式转换/重命名/提取/分类/简单问答)",
            recommended_model="Haiku 4.5 等轻量档",
            reason="这类任务对推理深度没要求,轻量模型速度快 3-5 倍、价格约为旗舰档 1/10",
            est_saving_pct=90,
        ),
        ModelRoutingAdvice(
            task="日常编码 / 文档撰写 / 代码审查",
            recommended_model="Sonnet 5 等均衡档",
            reason="性价比最优,质量足够覆盖 80% 日常工作",
            est_saving_pct=60,
        ),
        ModelRoutingAdvice(
            task="架构设计 / 深度调试 / 复杂决策 / 高风险输出",
            recommended_model="Opus 5 / Fable 5 等旗舰档",
            reason="这是旗舰模型真正的用武之地——错误成本高、需要长程推理的任务",
            est_saving_pct=None,
        ),
        ModelRoutingAdvice(
            task="大批量流水线(翻译/摘要/数据清洗)",
            recommended_model="轻量档 + 后台定时批处理",
            reason="量大且单条简单,用轻量模型异步跑,不占用你的工作时间",
            est_saving_pct=85,
        ),
    ]
    return table


def diagnose_model_mix(
    usage_by_model: Dict[str, dict],
    total_cost_7d: float,
) -> Tuple[str, float, Dict[str, bool]]:
    """诊断当前模型使用结构。

    返回 (诊断文本, 预估每月可节省美元, 信号 flags)。
    flags: model_overuse=旗舰档成本超配; fast_gap=几乎没用轻量模型。
    诊断闸门以**成本份额**为准 (premium 单价约为 fast 10 倍,
    成本集中远早于 token 集中出现, 用 token 份额做闸门会漏报最典型的浪费)。
    """
    flags = {"model_overuse": False, "fast_gap": False}
    if not usage_by_model:
        return (
            "还没同步到你各个模型的用量明细(在电脑上运行一次 agentfit sync 即可)。"
            "下面先给你通用的『什么活用什么模型』对照表;同步后会变成个性化诊断。",
            0.0,
            flags,
        )

    tier_tokens = {"premium": 0, "balanced": 0, "fast": 0}
    tier_cost = {"premium": 0.0, "balanced": 0.0, "fast": 0.0}
    for name, u in usage_by_model.items():
        tier = classify_model(name)
        tier_tokens[tier] += int(u.get("tokens", 0))
        tier_cost[tier] += float(u.get("cost", 0.0))

    total_tokens = sum(tier_tokens.values()) or 1
    total_cost = sum(tier_cost.values())
    fast_token_share = tier_tokens["fast"] / total_tokens
    # 明细成本缺失时退回 token 份额估算
    if total_cost > 0:
        premium_cost_share = tier_cost["premium"] / total_cost
    else:
        premium_cost_share = tier_tokens["premium"] / total_tokens

    monthly_cost = total_cost_7d / 7.0 * 30.0
    saving = 0.0
    parts = []

    if premium_cost_share > 0.6:
        flags["model_overuse"] = True
        saving = (
            tier_cost["premium"] / 7.0 * 30.0
            * ROUTABLE_SHARE_OF_PREMIUM
            * (1 - FAST_VS_PREMIUM_COST_RATIO)
        )
        parts.append(
            f"旗舰档模型吃掉了你 {premium_cost_share:.0%} 的用量价值。"
            "按经验其中约四成是可以下放的常规任务——把它们切到轻量档。"
        )
    elif premium_cost_share > 0.3:
        parts.append(
            f"旗舰档占成本 {premium_cost_share:.0%},结构基本健康。"
            "留意别把格式化、提取类杂活也丢给它。"
        )
    else:
        parts.append(f"旗舰档仅占成本 {premium_cost_share:.0%},模型分层意识很好。")

    if fast_token_share < 0.05:
        flags["fast_gap"] = True
        parts.append(
            "你几乎没有使用轻量模型——这是最大的优化空间。"
            "机械任务切过去,速度和成本同时改善。"
        )
        if saving == 0.0:
            saving = monthly_cost * 0.2

    return " ".join(parts), round(saving, 2), flags


def analyze_time_habits(
    hourly_histogram: Optional[List[int]],
) -> Tuple[List[TimeInsight], List[int], Dict[str, bool]]:
    """从 24 小时使用分布提取习惯洞察。返回 (insights, peak_hours, flags)。

    flags: no_data / late_night / fragmented / concentrated。
    """
    flags = {
        "no_data": False,
        "late_night": False,
        "fragmented": False,
        "concentrated": False,
    }
    if not hourly_histogram or len(hourly_histogram) != 24 or sum(hourly_histogram) == 0:
        flags["no_data"] = True
        return (
            [
                TimeInsight(
                    pattern="暂无时间分布数据",
                    advice="运行一次 agentfit sync 同步数据后,教练就能告诉你什么时间该干什么。",
                )
            ],
            [],
            flags,
        )

    total = sum(hourly_histogram)
    ranked = sorted(range(24), key=lambda h: hourly_histogram[h], reverse=True)
    peak_hours = sorted(ranked[:3])
    insights: List[TimeInsight] = []

    late_night = sum(hourly_histogram[0:6]) / total
    if late_night > 0.25:
        flags["late_night"] = True
        insights.append(
            TimeInsight(
                pattern=f"深夜(0-6点)用量占 {late_night:.0%}",
                advice="把批量任务(整理数据/翻译/跑测试)设成后台定时任务,深夜自动跑,"
                "你只在白天做需要判断力的决策。熬夜盯着 AI 干活是双输。",
            )
        )

    active_hours = sum(1 for c in hourly_histogram if c > 0)
    top3_share = sum(hourly_histogram[h] for h in ranked[:3]) / total
    if active_hours >= 14 and top3_share < 0.45:
        flags["fragmented"] = True
        insights.append(
            TimeInsight(
                pattern=f"全天碎片化使用({active_hours} 个小时段有活动)",
                advice="碎片化使用意味着反复重建上下文,这是隐形浪费。"
                "把零散问题攒到固定的 2-3 个'AI 工作时段'集中处理,"
                "一个会话内批量完成同类任务。",
            )
        )
    elif top3_share >= 0.6:
        flags["concentrated"] = True
        insights.append(
            TimeInsight(
                pattern=f"使用高度集中在 {'/'.join(f'{h}点' for h in peak_hours)}(占 {top3_share:.0%})",
                advice="集中使用是好习惯。进一步:在高峰时段开始前,"
                "先把当天要用的资料和模板准备好,让高峰时段纯产出。",
            )
        )

    if not insights:
        insights.append(
            TimeInsight(
                pattern=f"高峰时段: {'/'.join(f'{h}点' for h in peak_hours)}",
                advice="时间分布正常。可以尝试把最难的任务安排在你的高峰时段,"
                "机械任务丢给后台批处理。",
            )
        )

    return insights, peak_hours, flags


def infer_goal_and_path(
    task_types: Dict[str, int],
    goal: Optional[str],
    rule_hits: Dict[str, bool],
) -> Tuple[str, str]:
    """从任务类型分布 + 用户自述目标推断主线,给出路径建议。"""
    if goal:
        goal_prefix = f"你自述的目标是「{goal}」。"
    else:
        goal_prefix = ""

    if not task_types:
        inference = goal_prefix + "尚未上报任务类型分布,无法推断主线。"
        path = "上报任务分类数据后,教练会告诉你:你的时间实际花在哪、和你的目标是否一致。"
        return inference, path

    total = sum(task_types.values()) or 1
    dominant = max(task_types, key=task_types.get)
    dominant_share = task_types[dominant] / total
    dominant_label = TASK_LABELS.get(dominant, dominant)

    inference = (
        goal_prefix
        + f"数据显示你 {dominant_share:.0%} 的 AI 用量花在「{dominant_label}」上"
        + ("——这就是你的实际主线。" if dominant_share > 0.5 else ",但分布较分散。")
    )

    path_map = {
        "coding": "工程主线的复利路径:①高频操作沉淀成 skill/脚本 ②测试和审查交给流水线自动跑 "
        "③旗舰模型只留给架构和疑难调试。目标是让 AI 从'帮你写代码'升级为'帮你运营工程体系'。",
        "writing": "内容主线的复利路径:①建立选题库和风格模板,初稿用均衡档批量生成 "
        "②人工只做判断和精修 ③发布数据回流指导下一轮选题。关键指标是'发出数量',不是'素材完备度'。",
        "research": "研究主线的复利路径:①广度扫描用轻量模型并行跑 ②深读和综合才上旗舰模型 "
        "③每次研究产出结构化笔记沉淀进知识库,避免重复研究。",
        "data": "数据主线的复利路径:①全部转成定时批处理,脱离手工触发 ②轻量模型足够 "
        "③把校验规则写死在流水线里,人只看异常报告。",
        "chat": "咨询对话占比高:①高频问题沉淀成 FAQ/知识库,一次回答反复复用 "
        "②真正需要深度分析的问题才开长会话。",
        "design": "创意主线的复利路径:①风格参考和素材库先行 ②批量生成再人工筛选 "
        "③把选中方案的提示词记下来,攒成你的配方库。",
    }
    path = path_map.get(
        dominant,
        "把最高频的任务类型流程化:固定入口、固定模板、固定验收标准,让重复劳动复利化。",
    )

    if dominant_share <= 0.5:
        path = (
            "你的用量分散在多条线上。建议:为每类任务固定一个'工作流入口'(模板/skill),"
            "避免每次从零开始。主线聚焦后效率至少翻倍。 " + path
        )

    return inference, path


FAST_GAP_ADVICE = ActionItem(
    priority=2,
    title="让轻量模型干杂活",
    detail="你几乎没用过轻量模型。格式转换、提取、分类这类杂活交给 Haiku 级小模型,"
    "速度快 3-5 倍,价格只有大模型的十分之一。",
    expected_impact="杂活成本下降 80-90%",
)


def build_action_items(
    rule_hits: Dict[str, bool],
    mix_flags: Optional[Dict[str, bool]],
    cache_hit_rate: Optional[float],
) -> List[ActionItem]:
    """汇总触发的规则与模型结构信号 → 按优先级取前 3 条行动项。"""
    items: List[ActionItem] = []
    mix_flags = mix_flags or {}

    for rule, hit in (rule_hits or {}).items():
        if hit and rule in RULE_ADVICE:
            items.append(RULE_ADVICE[rule])

    if cache_hit_rate is not None and cache_hit_rate < 0.5 and not any(
        i.title == RULE_ADVICE["RULE_LOW_CACHE_HIT"].title for i in items
    ):
        items.append(RULE_ADVICE["RULE_LOW_CACHE_HIT"])

    # 只有旗舰档确实超配才建议"下放"; 缺轻量档是另一件事,给对应的建议
    overuse_title = RULE_ADVICE["RULE_MODEL_OVERUSE"].title
    if mix_flags.get("model_overuse") and not any(i.title == overuse_title for i in items):
        items.append(RULE_ADVICE["RULE_MODEL_OVERUSE"])
    if mix_flags.get("fast_gap") and not any(
        i.title in (FAST_GAP_ADVICE.title, overuse_title) for i in items
    ):
        items.append(FAST_GAP_ADVICE)

    if not items:
        items.append(
            ActionItem(
                priority=3,
                title="保持当前节奏",
                detail="未检测到明显浪费。下一步把注意力放在'沉淀':高频任务模板化,让好习惯复利。",
                expected_impact="效率长期复利",
            )
        )

    items.sort(key=lambda i: i.priority)
    return items[:3]


def _clamp(v: int, lo: int = 20, hi: int = 100) -> int:
    return max(lo, min(hi, v))


def compute_checkup(
    rule_hits: Dict[str, bool],
    mix_flags: Dict[str, bool],
    time_flags: Dict[str, bool],
    cache_hit_rate: Optional[float],
    saving: float,
) -> Tuple[List[DimensionScore], List[CheckupIssue]]:
    """360 式体检: 四维分项得分 + 发现的问题清单 (每项带修复方案)。"""
    issues: List[CheckupIssue] = []

    # --- 维度 1: 模型路由 ---
    routing = 95
    if mix_flags.get("model_overuse"):
        routing -= 40
        issues.append(CheckupIssue(
            severity="critical",
            dimension="模型路由",
            title="旗舰模型承担了过多常规任务",
            detail=f"旗舰档吃掉了大部分成本,其中约四成是轻量模型就能干好的活。",
            fix="给日常任务设默认模型: 机械任务用 Haiku 级,日常编码用 Sonnet 级,只在架构/疑难问题时手动切换旗舰档。",
            impact="成本或额度消耗下降 30-60%",
            steps=[
                "把日常默认模型设为 Sonnet 级 (Claude Code 里输入 /model 即可切换)",
                "机械任务 (格式转换/批量提取/分类) 明确指定 Haiku 级模型执行",
                "把模型使用规则写进 CLAUDE.md——点「一键修复」自动生成,复制粘贴即可",
                "一周后重新体检,确认旗舰档成本占比降到 60% 以下",
            ],
        ))
    if mix_flags.get("fast_gap"):
        routing -= 20
        issues.append(CheckupIssue(
            severity="warning",
            dimension="模型路由",
            title="几乎没有使用轻量模型",
            detail="格式转换、提取、分类这类机械任务在用中高档模型跑,既慢又贵。",
            fix="把机械任务切到 Haiku 级模型: 速度快 3-5 倍,价格约为主力模型 1/10。",
            impact="机械任务成本下降 80-90%",
            steps=[
                "列出你最常做的 3 类机械任务 (如格式转换/提取/摘要)",
                "这些任务发起时明确指定 Haiku 级模型",
                "大批量任务改成后台批处理,异步跑完再看结果",
            ],
        ))

    # --- 维度 2: 时间习惯 ---
    time_score = 90
    if time_flags.get("late_night"):
        time_score -= 25
        issues.append(CheckupIssue(
            severity="warning",
            dimension="时间习惯",
            title="深夜高强度使用",
            detail="0-6 点用量占比过高。人盯着 AI 跑批量任务是双输: 你在熬夜,AI 在等你确认。",
            fix="把批量任务(整理数据/翻译/跑测试)设成后台定时任务深夜自动跑,白天只做需要判断力的决策。",
            impact="夺回睡眠,同样的产出",
            steps=[
                "梳理深夜在做的任务,区分「需要我判断」和「机器能自己跑」",
                "「机器能自己跑」的部分设成定时任务 (Claude Code 支持定时任务,或用 cron)",
                "第二天早上只审结果,不熬夜盯过程",
            ],
        ))
    if time_flags.get("fragmented"):
        time_score -= 20
        issues.append(CheckupIssue(
            severity="warning",
            dimension="时间习惯",
            title="全天碎片化使用",
            detail="零散使用意味着反复重建上下文,时间和费用都花在重复交代背景上。",
            fix="固定 2-3 个'AI 工作时段',把零散问题攒起来,一个会话内批量处理同类任务。",
            impact="上下文重建成本大幅下降,专注度提升",
            steps=[
                "在日程里固定 2-3 个「AI 工作时段」(建议上午一段、下午一段)",
                "零散问题先记到待办清单,不立刻开新会话",
                "工作时段内按任务类型分组,同类问题在同一个会话里批量处理",
            ],
        ))
    if time_flags.get("concentrated"):
        time_score = min(95, time_score + 5)

    # --- 维度 3: 上下文卫生 ---
    context = 95
    if rule_hits.get("RULE_CONTEXT_BLOAT"):
        context -= 35
        issues.append(CheckupIssue(
            severity="critical",
            dimension="上下文卫生",
            title="会话上下文膨胀",
            detail="聊天记录越滚越长,AI 每次回答都要重读全部历史——你在为没用的旧内容反复付费。",
            fix="一个任务聊完就开新对话;大文件只让 AI 读需要的部分;常用背景写成固定模板,别每次重贴。",
            impact="单次对话 token 消耗下降 30-50%",
            steps=[
                "养成习惯: 每完成一个任务就开新会话 (Claude Code 里输入 /clear)",
                "读大文件时指定需要的部分,避免整个文件进上下文",
                "把反复交代的项目背景写进 CLAUDE.md,一次沉淀永久生效",
            ],
        ))
    if cache_hit_rate is not None and cache_hit_rate < 0.5:
        context -= 20
        issues.append(CheckupIssue(
            severity="warning",
            dimension="上下文卫生",
            title=f"缓存命中率仅 {cache_hit_rate:.0%}",
            detail="提示词开头不稳定,缓存没有生效,输入费用一直按全价在跑。",
            fix="把 system prompt 和固定参考资料放在提示词开头并保持稳定,变化的内容放末尾。",
            impact="输入成本最高降 90%",
            steps=[
                "检查高频提示词,把固定部分 (角色设定/规则/参考资料) 挪到最前面",
                "固定部分逐字保持稳定——改一个字缓存就失效",
                "变化的问题和数据统一放在提示词末尾",
                "一周后重新体检,目标缓存命中率回到 60% 以上",
            ],
        ))
    if rule_hits.get("RULE_ERROR_RETRY_LOOP"):
        context -= 15
        issues.append(CheckupIssue(
            severity="warning",
            dimension="上下文卫生",
            title="无效重试循环",
            detail="同一个任务失败后被原样重发了好几次——第三次几乎必然还是失败,钱白花。",
            fix="连续失败 2 次就停下来: 换个问法、把任务拆小,或者补充关键信息再试。",
            impact="消灭 10-20% 的无效消耗",
            steps=[
                "立规矩: 同一任务连续失败 2 次,强制停止原样重发",
                "失败后三选一: 缩小任务范围 / 补充关键上下文 / 换一种表述",
                "反复失败的任务记入避坑清单,下次绕开同样的坑",
            ],
        ))

    # --- 维度 4: 沉淀复用 ---
    leverage = 85
    if rule_hits.get("RULE_MISSING_SKILL"):
        leverage -= 30
        issues.append(CheckupIssue(
            severity="info",
            dimension="沉淀复用",
            title="高频任务未沉淀为 skill",
            detail="同样的活每次都从头教 AI 一遍,时间和钱都花在重复解释上。",
            fix="把常干的活写成 skill 模板: 一次写好,反复复用,结果还更稳定。",
            impact="重复任务耗时下降一半以上",
            steps=[
                "找出本周重复做过 3 次以上的任务",
                "把它的完整提示词和步骤整理成一个 skill 或固定模板",
                "下次直接调用模板,只补充本次的差异信息",
            ],
        ))
    if time_flags.get("fragmented"):
        leverage -= 10

    dims = [
        DimensionScore(key="routing", label="模型路由", score=_clamp(routing)),
        DimensionScore(key="timing", label="时间习惯", score=_clamp(time_score)),
        DimensionScore(key="context", label="上下文卫生", score=_clamp(context)),
        DimensionScore(key="leverage", label="沉淀复用", score=_clamp(leverage)),
    ]
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: severity_rank[i.severity])
    return dims, issues


def build_money_texts(
    saving: float,
    total_cost_7d: float,
    billing_mode: Optional[str],
    monthly_subscription_usd: Optional[float],
) -> Tuple[str, str]:
    """唯一的钱表述出口,返回 (value_text, saving_text) 两句短话。

    value_text: 你的订阅值不值 (只有订阅模式有)。
    saving_text: 优化后你得到什么。
    原则: 说人话,一句话只说一件事;订阅用户绝不说"省美元"。
    """
    monthly_value = total_cost_7d / 7.0 * 30.0
    value_text = ""
    saving_text = ""

    if billing_mode == "subscription":
        if monthly_subscription_usd and monthly_subscription_usd > 0 and monthly_value > 0:
            ratio = monthly_value / monthly_subscription_usd
            value_text = (
                f"这个月的用量,按量付费要花约 ${monthly_value:.0f}——"
                f"你的 ${monthly_subscription_usd:.0f} 订阅约 {ratio:.0f} 倍回本,放心继续用"
            )
        if saving > 0 and monthly_value > 0:
            quota_pct = min(60, round(saving / monthly_value * 100))
            saving_text = (
                f"照方案优化后,同一份订阅能多干约 {quota_pct}% 的活,"
                "高峰期更少被限流"
            )
    elif billing_mode == "api":
        if saving > 0:
            saving_text = f"照方案优化后,每月账单约省 ${saving:.0f}"
    else:
        if saving > 0:
            saving_text = (
                f"照方案优化后,按量付费的用户每月约省 ${saving:.0f};"
                "订阅用户省下的是额度"
            )
    return value_text, saving_text


def build_money_cards(
    saving: float,
    total_cost_7d: float,
    billing_mode: Optional[str],
    monthly_subscription_usd: Optional[float],
) -> Dict[str, str]:
    """结构化的钱指标 (仪表板用): 每张卡 = 大数值 + 标签 + 一行说明。

    与 build_money_texts 同源同口径,只是拆成可对齐排版的三段,
    避免界面上出现长句胶囊。
    """
    monthly_value = total_cost_7d / 7.0 * 30.0
    out = {
        "value_headline": "", "value_label": "", "value_caption": "",
        "saving_headline": "", "saving_label": "", "saving_caption": "",
    }

    if billing_mode == "subscription":
        if monthly_subscription_usd and monthly_subscription_usd > 0 and monthly_value > 0:
            ratio = monthly_value / monthly_subscription_usd
            out["value_headline"] = f"{ratio:.0f} 倍"
            out["value_label"] = "订阅回本倍数"
            out["value_caption"] = (
                f"同样用量按量付费约 ${monthly_value:.0f}/月,"
                f"你只付 ${monthly_subscription_usd:.0f}"
            )
        if saving > 0 and monthly_value > 0:
            quota_pct = min(60, round(saving / monthly_value * 100))
            out["saving_headline"] = f"+{quota_pct}%"
            out["saving_label"] = "可释放额度"
            out["saving_caption"] = "照方案优化后同一份订阅能多干的活"
    elif billing_mode == "api":
        if saving > 0:
            out["saving_headline"] = f"${saving:.0f}"
            out["saving_label"] = "每月可省"
            out["saving_caption"] = "照方案优化后的账单降幅"
    else:
        if saving > 0:
            out["saving_headline"] = f"${saving:.0f}"
            out["saving_label"] = "每月优化空间"
            out["saving_caption"] = "按量付费的省钱额;订阅用户省下的是额度"
    return out


TIER_TITLES = {
    "S": ("AI 宗师", "顶尖用法,你已经是大师本尊"),
    "A": ("AI 大师", "用得相当地道,离宗师一步之遥"),
    "B": ("AI 熟练者", "基础扎实,照方案再上一层楼"),
    "C": ("AI 探索者", "别灰心,每个大师都从这里开始"),
}


def build_title_and_encourage(
    tier: str, percentile: int, issue_count: int
) -> Tuple[str, str]:
    """段位称号 + 互动鼓励语 (像 360 体检完的那句贴心话)。"""
    title, base = TIER_TITLES.get(tier, TIER_TITLES["C"])

    if tier in ("S", "A"):
        encourage = base + ("。保持住,别让机械任务拖累你的段位" if issue_count else "!")
    elif tier == "B":
        encourage = base + f"——修完下面 {issue_count} 个问题,下次体检就能冲 A 段"
    else:
        encourage = (
            base + "。要加油哦!方案已经帮你备好了,"
            "照着『一键修复』给的方案做,下周分数就能涨上来"
        )
    return title, encourage


def build_verdict(score: int, issue_count: int) -> str:
    if score >= 85:
        grade = "优秀"
    elif score >= 70:
        grade = "良好"
    elif score >= 50:
        grade = "一般"
    else:
        grade = "急需优化"
    if issue_count == 0:
        return f"{grade} · 未发现明显问题"
    return f"{grade} · 发现 {issue_count} 个可优化项"


class CoachEngine:
    """生成完整教练报告。"""

    def __init__(self, db):
        self.db = db

    def generate_report(
        self,
        score: int,
        tier: str,
        total_tokens_7d: int,
        total_cost_7d: float,
        rule_hits: Optional[Dict[str, bool]] = None,
        usage_by_model: Optional[Dict[str, dict]] = None,
        hourly_histogram: Optional[List[int]] = None,
        task_types: Optional[Dict[str, int]] = None,
        goal: Optional[str] = None,
        cache_hit_rate: Optional[float] = None,
        billing_mode: Optional[str] = None,
        monthly_subscription_usd: Optional[float] = None,
    ) -> CoachReport:
        rule_hits = rule_hits or {}
        usage_by_model = usage_by_model or {}
        task_types = task_types or {}

        percentile = self.db.get_percentile_for_score(score)
        stats = self.db.get_statistics()
        total_samples = stats.get("total_submissions", 0)

        # 小样本说名次 (人人看得懂),样本够了才说百分比 + 前百分之几
        if total_samples >= 20:
            top_pct = max(1, 100 - percentile)
            beat_text = f"你已打败全球 {percentile}% 的 AI 用户,位列前 {top_pct}%"
            rank_headline = f"前 {top_pct}%"
            rank_caption = f"打败全球 {percentile}% 的用户"
        elif total_samples >= 2:
            rank = self.db.get_rank_for_score(score)
            beat_text = f"目前 {total_samples} 位用户提交了数据,你排第 {rank} 名"
            rank_headline = f"第 {rank} 名"
            rank_caption = f"共 {total_samples} 位用户已提交数据"
        else:
            beat_text = "你是第一位提交数据的用户,基准由你定义"
            rank_headline = "第 1 名"
            rank_caption = "你是首位提交数据的用户"

        diagnosis, saving, mix_flags = diagnose_model_mix(usage_by_model, total_cost_7d)
        time_insights, peak_hours, time_flags = analyze_time_habits(hourly_histogram)
        goal_inference, path_advice = infer_goal_and_path(task_types, goal, rule_hits)
        dominant_task = max(task_types, key=task_types.get) if task_types else None
        dimension_scores, issues = compute_checkup(
            rule_hits, mix_flags, time_flags, cache_hit_rate, saving
        )
        title_text, encourage_text = build_title_and_encourage(
            tier, percentile, len(issues)
        )

        return CoachReport(
            generated_at=datetime.utcnow(),
            score=score,
            tier=tier,
            title_text=title_text,
            encourage_text=encourage_text,
            verdict_text=build_verdict(score, len(issues)),
            dimension_scores=dimension_scores,
            issues=issues,
            global_percentile=percentile,
            beat_ratio_text=beat_text,
            total_samples=total_samples,
            model_routing=build_routing_table(dominant_task),
            routing_diagnosis=diagnosis,
            est_monthly_saving_usd=saving,
            value_text=(money := build_money_texts(
                saving, total_cost_7d, billing_mode, monthly_subscription_usd
            ))[0],
            saving_text=money[1],
            **build_money_cards(
                saving, total_cost_7d, billing_mode, monthly_subscription_usd
            ),
            rank_headline=rank_headline,
            rank_caption=rank_caption,
            billing_mode=billing_mode,
            time_insights=time_insights,
            peak_hours=peak_hours,
            hourly_histogram=hourly_histogram,
            goal_inference=goal_inference,
            path_advice=path_advice,
            action_items=build_action_items(rule_hits, mix_flags, cache_hit_rate),
        )
