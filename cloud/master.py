"""365 AI Master — 大师推荐引擎 v1。

人设: 全球最精通 AI 前沿技术、使用技巧、安全提醒的超级大师。
v1 数据源是人工甄别过的「已验证内容池」,按日期轮换出「今日推荐」。
每条内容必须满足: 被广泛验证 (verified) + 注明来源 + 涉及隐私时显式标注。
v2 接自动采集 pipeline (GitHub/YouTube/Reddit/X/微信) 后,内容池变为动态。
"""
from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class MasterTip(BaseModel):
    """一条大师推荐。"""
    kind: str  # tip | skill | agent | product | security
    title: str
    detail: str
    why_trust: str          # 为什么可信 (甄别依据)
    source: str             # 来源: 官方文档 / GitHub / 社区共识
    privacy_note: Optional[str] = None  # 涉及隐私/授权时的提醒
    install: Optional[str] = None       # 可复制的安装/启用命令
    level: str = "beginner"  # beginner | advanced


class RankingItem(BaseModel):
    """排行榜条目 — 只收真实存在的知名项目,不编数据。"""
    name: str               # GitHub repo 或产品名
    tagline: str            # 一句话:它帮你干什么
    why: str                # 严选理由
    url: str
    install: Optional[str] = None


class DailyMasterFeed(BaseModel):
    date: str
    persona_line: str
    tips: List[MasterTip]
    skill_ranking: List[RankingItem] = []
    agent_ranking: List[RankingItem] = []
    disclaimer: str


PERSONA_LINE = (
    "我每天替你看完官方发布、GitHub、YouTube、Reddit 和 X 上的新东西,"
    "只把验证过、安全的交到你手上。"
)

DISCLAIMER = (
    "目前的推荐来自人工甄别的已验证内容池,每天轮换;"
    "自动采集与实时甄别正在开发中。营销内容一律不收。"
)

# ---------------------------------------------------------------------------
# 已验证内容池 — 只收被广泛证实、来源明确的条目。
#
# 纪律 (2026-08-10 定): **这里是正式内容的唯一来源**。
# 所有面向用户的推荐都必须写在这个列表里,走代码审查与版本控制。
# 数据库动态池 (master_tips 表) 只留给未来的自动采集 pipeline;
# 任何手工/测试写入都属于污染,一律用 /api/v1/master/retire 下架。
# ---------------------------------------------------------------------------
CONTENT_POOL: List[MasterTip] = [
    # ===== 使用技巧 (tips) =====
    MasterTip(
        kind="tip",
        title="给项目写一个 CLAUDE.md(AI 的项目说明书),它从此记住你的规矩",
        detail="CLAUDE.md 是放在项目文件夹里的规则文件(Claude Code 认这个名字,Codex 认 AGENTS.md)。"
        "写清楚项目结构、常用命令、你的规矩,AI 每次对话都会自动先读,不用再重复交代。",
        why_trust="Anthropic 官方推荐做法,Claude Code 文档明确支持",
        source="官方文档 docs.anthropic.com",
        level="beginner",
    ),
    MasterTip(
        kind="tip",
        title="长会话记得开新会话,省钱又提质",
        detail="会话越长,每次请求都在为历史内容付费,而且模型注意力被稀释。"
        "一个任务结束就开新会话,大任务拆成多个会话。",
        why_trust="上下文成本随会话长度线性增长是计费机制的基本事实",
        source="官方计费文档 + 社区共识",
        level="beginner",
    ),
    MasterTip(
        kind="tip",
        title="先让 AI 出计划,你点头后再让它动手",
        detail="复杂任务先说「先给我一个计划,先不要改代码」。"
        "看过计划再让它动手,返工大幅减少。Claude Code 里有专门的『计划模式』(Plan Mode)。",
        why_trust="官方内置功能,社区实测返工率显著下降",
        source="官方文档 + 社区实践",
        level="beginner",
    ),
    MasterTip(
        kind="tip",
        title="用好提示词缓存,重复内容只按 1 折计费",
        detail="固定的要求、参考资料放在每次提问的开头,变化的问题放末尾——"
        "AI 认出重复部分后,这部分只收约 1 折的钱。",
        why_trust="提示词缓存(Prompt Caching)是官方计费机制,折扣写在价目表里",
        source="官方定价文档",
        level="advanced",
    ),
    MasterTip(
        kind="tip",
        title="用 /compact 或开新会话前,先让 AI 总结当前进度",
        detail="长会话结束前说「把已完成的结论和待办总结成一段」,存进项目笔记再开新会话——"
        "上下文清爽了,进度一点不丢。",
        why_trust="官方推荐的上下文管理实践,社区广泛验证",
        source="官方文档 + 社区共识",
        level="beginner",
    ),
    MasterTip(
        kind="tip",
        title="连续失败两次就停,改问法而不是重发",
        detail="AI 连续两次没做对,原样重发第三次几乎必然还是错的。"
        "停下来: 缩小任务范围、补充关键信息、或者换一种问法。",
        why_trust="重试循环是被广泛观察到的浪费模式",
        source="社区共识",
        level="beginner",
    ),
    # ===== Skill / 工具 (skills) =====
    MasterTip(
        kind="skill",
        title="官方 Skills 库: 一条命令给 AI 装上新能力",
        detail="Anthropic 官方开源的 skills 仓库,涵盖文档处理、数据分析等常用能力,"
        "复制到你电脑的 ~/.claude/skills 文件夹即可使用。",
        why_trust="Anthropic 官方仓库,持续维护",
        source="GitHub anthropics/skills",
        install="git clone https://github.com/anthropics/skills ~/.claude/skills-official",
        level="beginner",
    ),
    MasterTip(
        kind="skill",
        title="给 AI 接上外部工具的标准接口(MCP),装之前先看权限",
        detail="MCP (Model Context Protocol) 是连接 AI 与外部工具的开放标准,"
        "官方与社区有大量现成 server,能连数据库、浏览器、本地文件。",
        why_trust="Anthropic 主导的开放标准,生态成熟",
        source="GitHub modelcontextprotocol/servers",
        privacy_note="第三方 MCP server 可能获得文件系统或网络权限——"
        "只装来源可查的 server,不明来源不要给敏感目录权限。",
        level="advanced",
    ),
    # ===== Agent 用法 (agents) =====
    MasterTip(
        kind="agent",
        title="批量任务交给后台 agent,你只看结果",
        detail="翻译一批文档、清洗一批数据这类活,让 agent 在后台跑完再叫你,"
        "不需要盯着屏幕等。Claude Code 支持后台任务与定时任务。",
        why_trust="官方内置能力",
        source="官方文档",
        level="advanced",
    ),
    MasterTip(
        kind="agent",
        title="重要结论让第二个 agent 唱反调",
        detail="让一个 agent 给方案,再开一个 agent 专门挑毛病——"
        "「请反驳这个方案,找出它会失败的场景」。对抗审查能拦下大部分想当然。",
        why_trust="对抗验证是被广泛采用的质量实践",
        source="社区最佳实践",
        level="advanced",
    ),
    # ===== 安全与隐私 (security) =====
    MasterTip(
        kind="security",
        title="任何要读你聊天记录/邮箱的 AI 产品,先看三件事",
        detail="① 数据存在哪、存多久 ② 是否用你的数据训练模型 ③ 能不能一键删除。"
        "条款里找不到明确答案的,默认按「会被使用」对待。",
        why_trust="数据最小化原则,各国隐私法规的共同底线",
        source="GDPR/CCPA 通行原则",
        privacy_note="担心个人数据的产品,授权前先在设置里找「数据与隐私」页;"
        "没有这一页本身就是信号。",
        level="beginner",
    ),
    MasterTip(
        kind="security",
        title="API Key 别写进代码,放环境变量",
        detail="密钥写进代码,迟早跟着代码一起泄漏。放进环境变量或密钥管理器;"
        "万一泄漏,立刻去服务商后台作废换新。",
        why_trust="密钥泄漏是 GitHub 上最常见的安全事故之一",
        source="安全工程共识",
        level="beginner",
    ),
    MasterTip(
        kind="security",
        title="让 AI 跑命令可以,删除和发送要过你的手",
        detail="给 AI 执行权限时,把「删除文件、对外发送、支付」设为必须人工确认。"
        "自动化的边界就是不可逆操作。",
        why_trust="最小权限原则,所有 agent 安全指南的共同条款",
        source="安全工程共识",
        level="beginner",
    ),
]


# ---------------------------------------------------------------------------
# 排行榜严选池 — 全部是 GitHub 上真实存在、广泛使用的项目。
# 不标 star 数 (会过时会出错),排名 = 严选顺序 + 每日轮换。
# ---------------------------------------------------------------------------
SKILL_POOL: List[RankingItem] = [
    RankingItem(
        name="anthropics/skills",
        tagline="官方 Skill 库,文档/表格/PPT 处理开箱即用",
        why="Anthropic 官方出品,质量与维护有保证",
        url="https://github.com/anthropics/skills",
        install="git clone https://github.com/anthropics/skills ~/.claude/skills-official",
    ),
    RankingItem(
        name="modelcontextprotocol/servers",
        tagline="MCP 官方 server 合集,让 AI 连上数据库/浏览器/文件系统",
        why="MCP 标准的官方参考实现,生态基石",
        url="https://github.com/modelcontextprotocol/servers",
    ),
    RankingItem(
        name="stanfordnlp/dspy",
        tagline="把提示词写成可自动优化的程序,不再手工反复调",
        why="斯坦福出品,提示词工程的工程化标杆",
        url="https://github.com/stanfordnlp/dspy",
        install="pip install dspy",
    ),
    RankingItem(
        name="mem0ai/mem0",
        tagline="给你的 AI 加持久记忆,越用越懂你",
        why="Agent 记忆层的主流方案",
        url="https://github.com/mem0ai/mem0",
        install="pip install mem0ai",
    ),
    RankingItem(
        name="langchain-ai/langchain",
        tagline="AI 应用开发框架,组件最全",
        why="生态最大、集成最多的老牌框架",
        url="https://github.com/langchain-ai/langchain",
        install="pip install langchain",
    ),
    RankingItem(
        name="langgenius/dify",
        tagline="不写代码也能上线 AI 应用,拖拽可视化",
        why="低代码 LLM 平台里社区最活跃的",
        url="https://github.com/langgenius/dify",
    ),
    RankingItem(
        name="Mintplex-Labs/anything-llm",
        tagline="私有文档问答一站式,本地部署保护隐私",
        why="开箱即用的私有知识库方案",
        url="https://github.com/Mintplex-Labs/anything-llm",
    ),
    RankingItem(
        name="lobehub/lobe-chat",
        tagline="颜值最高的开源 AI 对话界面,插件丰富",
        why="自托管 Chat UI 的人气之选",
        url="https://github.com/lobehub/lobe-chat",
    ),
    RankingItem(
        name="danny-avila/LibreChat",
        tagline="一个界面接入所有模型,团队共享",
        why="多模型聚合界面的成熟方案",
        url="https://github.com/danny-avila/LibreChat",
    ),
    RankingItem(
        name="n8n-io/n8n",
        tagline="拖拽式工作流自动化,AI 节点齐全",
        why="把 AI 接进业务流程的最快路径",
        url="https://github.com/n8n-io/n8n",
    ),
]

AGENT_POOL: List[RankingItem] = [
    RankingItem(
        name="browser-use/browser-use",
        tagline="让 AI 替你操作浏览器: 订票/填表/抓数据",
        why="浏览器 agent 里最活跃的开源方案",
        url="https://github.com/browser-use/browser-use",
        install="pip install browser-use",
    ),
    RankingItem(
        name="aider-AI/aider",
        tagline="终端里的 AI 结对程序员,直接改你的 git 仓库",
        why="命令行编码 agent 的口碑之作",
        url="https://github.com/aider-AI/aider",
        install="pip install aider-install && aider-install",
    ),
    RankingItem(
        name="cline/cline",
        tagline="VS Code 里的自主编码 agent,能跑命令能改文件",
        why="IDE 内 agent 的人气首选",
        url="https://github.com/cline/cline",
    ),
    RankingItem(
        name="assafelovic/gpt-researcher",
        tagline="给个题目,自动完成全网调研并出报告",
        why="深度研究 agent 的代表作",
        url="https://github.com/assafelovic/gpt-researcher",
        install="pip install gpt-researcher",
    ),
    RankingItem(
        name="microsoft/autogen",
        tagline="微软出品的多 agent 协作框架",
        why="多 agent 编排的学术与工业标杆",
        url="https://github.com/microsoft/autogen",
        install="pip install autogen-agentchat",
    ),
    RankingItem(
        name="crewAIInc/crewAI",
        tagline="按角色组建 AI 团队: 研究员+写手+审校流水线",
        why="角色化多 agent 的易用之选",
        url="https://github.com/crewAIInc/crewAI",
        install="pip install crewai",
    ),
    RankingItem(
        name="geekan/MetaGPT",
        tagline="一句需求,多 agent 模拟软件公司交付项目",
        why="多 agent 软件工程的先驱项目",
        url="https://github.com/geekan/MetaGPT",
    ),
    RankingItem(
        name="OpenInterpreter/open-interpreter",
        tagline="本地代码解释器,让 AI 直接操作你的电脑",
        why="本地自动化 agent 的经典",
        url="https://github.com/OpenInterpreter/open-interpreter",
        install="pip install open-interpreter",
    ),
    RankingItem(
        name="khoj-ai/khoj",
        tagline="你的第二大脑: 笔记/文档/日程的个人 AI 助理",
        why="个人助理 agent 里隐私友好的开源方案",
        url="https://github.com/khoj-ai/khoj",
    ),
    RankingItem(
        name="ItzCrazyKns/Perplexica",
        tagline="开源版 AI 搜索引擎,自托管的 Perplexity",
        why="AI 搜索 agent 的开源替代",
        url="https://github.com/ItzCrazyKns/Perplexica",
    ),
]


def rotate_ranking(pool: List[RankingItem], seed: int, top_n: int = 10) -> List[RankingItem]:
    """按日轮换起点,让榜单每天有新鲜感但内容全部真实。"""
    if not pool:
        return []
    start = seed % len(pool)
    rotated = pool[start:] + pool[:start]
    return rotated[:top_n]


def get_daily_feed(
    today: Optional[date] = None,
    extra_pool: Optional[List[dict]] = None,
) -> DailyMasterFeed:
    """按日期从内容池轮换出今日推荐: 技巧/能力/安全 各至少一条。

    extra_pool: 采集 pipeline 入库的动态条目 (dict 列表),
    与静态池合并后参与轮换;动态条目排前,新内容更容易被选中。
    """
    d = today or date.today()
    seed = d.toordinal()

    dynamic = []
    for item in extra_pool or []:
        try:
            dynamic.append(MasterTip(**item))
        except Exception:
            continue  # 坏数据不进池

    combined = dynamic + CONTENT_POOL

    def pick(kind_list: List[str], offset: int) -> List[MasterTip]:
        pool = [t for t in combined if t.kind in kind_list]
        if not pool:
            return []
        return [pool[(seed + offset) % len(pool)]]

    tips = (
        pick(["tip"], 0)
        + pick(["skill", "agent"], 1)
        + pick(["security"], 2)
    )
    return DailyMasterFeed(
        date=d.isoformat(),
        persona_line=PERSONA_LINE,
        tips=tips,
        skill_ranking=rotate_ranking(SKILL_POOL, seed),
        agent_ranking=rotate_ranking(AGENT_POOL, seed + 3),
        disclaimer=DISCLAIMER,
    )
