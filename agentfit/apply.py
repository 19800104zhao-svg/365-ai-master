"""把体检生成的优化区块幂等写入 CLAUDE.md / AGENTS.md。

这是 CLI 端 optimize --apply 的本地写入逻辑。独立于 cloud 后端,
使 CLI 可打包成不含整个服务端的独立发行包。

标记常量必须与 cloud/optimizer.py 逐字一致(测试 test_apply 强制校验),
否则 API 返回的区块在本地无法被识别替换。
"""

MARKER_START = "<!-- 365-ai-master:optimization:start -->"
MARKER_END = "<!-- 365-ai-master:optimization:end -->"


def apply_to_claude_md(existing: str, optimization_block: str) -> str:
    """把优化区块幂等写入 CLAUDE.md 内容: 已有标记则替换,否则追加。"""
    if MARKER_START in existing and MARKER_END in existing:
        head, rest = existing.split(MARKER_START, 1)
        _, tail = rest.split(MARKER_END, 1)
        return head + optimization_block + tail
    sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
    return existing + sep + optimization_block + "\n"
