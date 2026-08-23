"""agentfit.apply — CLI 侧幂等写入 CLAUDE.md 的独立实现。

为让 CLI 打包成独立包(不拖整个 cloud 后端),把 optimize --apply 唯一
依赖的 apply_to_claude_md 从 cloud.optimizer 内联到 agentfit 侧。
行为必须与 cloud 版一致(标记区块幂等替换)。
"""
from agentfit.apply import apply_to_claude_md, MARKER_START, MARKER_END


def _block(text="RULES"):
    return f"{MARKER_START}\n{text}\n{MARKER_END}"


def test_append_when_no_marker():
    """已有内容无标记块: 追加到末尾, 保留原内容。"""
    result = apply_to_claude_md("# my rules\n", _block())
    assert _block() in result
    assert result.startswith("# my rules")


def test_idempotent_replace_when_marker_exists():
    """已有标记块: 替换而非重复追加(幂等)。"""
    first = apply_to_claude_md("# my rules\n", _block("V1"))
    second = apply_to_claude_md(first, _block("V2"))
    assert "V2" in second
    assert "V1" not in second
    assert second.count(MARKER_START) == 1  # 只保留一个区块


def test_empty_existing_file():
    """空文件: 直接写入区块。"""
    result = apply_to_claude_md("", _block())
    assert _block() in result


def test_marker_matches_cloud_side():
    """CLI 与 cloud 的标记常量必须逐字一致, 否则跨端替换会失效。"""
    from cloud.optimizer import MARKER_START as C_START, MARKER_END as C_END
    assert MARKER_START == C_START
    assert MARKER_END == C_END
