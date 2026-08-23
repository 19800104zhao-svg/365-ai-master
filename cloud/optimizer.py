"""一键优化 — 把体检结果编译成可直接生效的 CLAUDE.md 配置块。

360 的「一键修复」在 AI 使用场景的对应物:
体检发现的问题 → 生成对应的行为规则 → 用户一键写进 CLAUDE.md →
AI 从下一个会话起就按新规则工作 → 下周体检验证改善 (闭环)。

只为命中的问题生成规则,不堆砌通用废话。
"""
from datetime import date
from typing import Dict, List, Optional

MARKER_START = "<!-- 365-ai-master:optimization:start -->"
MARKER_END = "<!-- 365-ai-master:optimization:end -->"


def build_optimization_md(
    rule_hits: Dict[str, bool],
    mix_flags: Dict[str, bool],
    time_flags: Dict[str, bool],
    cache_hit_rate: Optional[float],
    dominant_task: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    """生成个性化优化配置 (markdown)。只包含体检命中的项。"""
    d = (today or date.today()).isoformat()
    sections: List[str] = []

    # 模型路由 — model_overuse / fast_gap / RULE_MODEL_OVERUSE 命中才写
    if (
        mix_flags.get("model_overuse")
        or mix_flags.get("fast_gap")
        or rule_hits.get("RULE_MODEL_OVERUSE")
    ):
        sections.append(
            "## 模型路由规则\n"
            "- 机械任务 (格式转换/重命名/批量提取/分类/简单查找): 用 Haiku 级轻量模型\n"
            "- 日常编码/文档撰写/代码审查: 用 Sonnet 级均衡模型\n"
            "- 只有架构设计/疑难调试/高风险决策才用 Opus/Fable 级旗舰模型\n"
            "- 大批量流水线任务 (翻译/摘要/清洗): 轻量模型 + 后台批处理,不占交互时间"
        )

    # 上下文纪律 — RULE_CONTEXT_BLOAT 命中才写
    if rule_hits.get("RULE_CONTEXT_BLOAT"):
        sections.append(
            "## 上下文纪律\n"
            "- 一个任务一个会话;阶段性目标完成后开新会话,不在长会话里续命\n"
            "- 读大文件只读需要的部分,不整文件灌进上下文\n"
            "- 会话结论及时落盘到项目文档,下个会话引用文档而不是重述历史"
        )

    # 缓存优化 — 低缓存命中才写
    if cache_hit_rate is not None and cache_hit_rate < 0.5:
        sections.append(
            "## 提示词缓存优化\n"
            "- 稳定内容 (系统规则/项目背景/参考资料) 固定放提示词开头,不要改动顺序\n"
            "- 变化的问题和数据放提示词末尾\n"
            "- 高频重复的背景说明沉淀成固定模板文件"
        )

    # 重试纪律 — RULE_ERROR_RETRY_LOOP 命中才写
    if rule_hits.get("RULE_ERROR_RETRY_LOOP"):
        sections.append(
            "## 失败重试纪律\n"
            "- 同一任务连续失败 2 次必须停下: 改写提示词、缩小范围或换思路\n"
            "- 禁止原样重发第三次"
        )

    # 沉淀复用 — RULE_MISSING_SKILL 命中才写
    if rule_hits.get("RULE_MISSING_SKILL"):
        sections.append(
            "## 沉淀复用\n"
            "- 同类任务第三次出现时,把它写成 skill/模板/脚本再执行\n"
            "- 高频命令和约束收进本文件或项目 CLAUDE.md,不逐次口述"
        )

    # 时间习惯 — 碎片化/深夜命中才写
    time_rules = []
    if time_flags.get("fragmented"):
        time_rules.append(
            "- 零散问题攒到固定的 2-3 个「AI 工作时段」批量处理,同类任务合并会话"
        )
    if time_flags.get("late_night"):
        time_rules.append(
            "- 批量任务 (数据处理/翻译/回归测试) 配置成定时后台任务夜间自动跑,"
            "白天只做需要判断的决策"
        )
    if time_rules:
        sections.append("## 时间习惯\n" + "\n".join(time_rules))

    if not sections:
        sections.append(
            "## 保持当前用法\n"
            "- 本次体检未发现需要写成规则的问题;把注意力放在高频任务的沉淀复用上"
        )

    body = "\n\n".join(sections)
    return (
        f"{MARKER_START}\n"
        f"# AI 使用优化规则 (365 AI Master 体检生成 · {d})\n\n"
        f"{body}\n\n"
        f"说明: 把这段放在你 AI 助手的规则文件里 (Claude Code 用 CLAUDE.md,"
        f"Codex 用 AGENTS.md),AI 会自动遵守。下次体检后重新生成即可更新。\n"
        f"{MARKER_END}"
    )


def apply_to_claude_md(existing: str, optimization_block: str) -> str:
    """把优化区块幂等写入 CLAUDE.md 内容: 已有标记则替换,否则追加。"""
    if MARKER_START in existing and MARKER_END in existing:
        head, rest = existing.split(MARKER_START, 1)
        _, tail = rest.split(MARKER_END, 1)
        return head + optimization_block + tail
    sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
    return existing + sep + optimization_block + "\n"
