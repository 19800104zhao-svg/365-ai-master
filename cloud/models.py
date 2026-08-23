from datetime import datetime
from typing import List, Literal, Dict, Optional
from pydantic import BaseModel, Field


class AggregatedScore(BaseModel):
    """Anonymized submission payload from AgentFit client.

    Rich fields (usage_by_model / hourly_histogram / task_types / goal /
    cache_hit_rate) are optional and backward compatible — old clients
    submitting only the core fields keep working.
    """
    score: int = Field(ge=0, le=100)
    tier: Literal["S", "A", "B", "C"]
    total_tokens_7d: int = Field(ge=0)
    total_cost_7d: float = Field(ge=0.0)
    rule_hits: Dict[str, bool] = Field(default_factory=dict)
    submitted_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    # --- rich profile for coach (all optional) ---
    usage_by_model: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        description='模型级用量: {"claude-opus-5": {"tokens": 1200000, "cost": 45.0, "requests": 80}}',
    )
    hourly_histogram: Optional[List[int]] = Field(
        default=None, description="24 项数组: 每小时请求次数分布"
    )
    task_types: Dict[str, int] = Field(
        default_factory=dict,
        description='任务类型分布: {"coding": 120, "writing": 30}',
    )
    goal: Optional[str] = Field(default=None, max_length=500, description="用户自述目标")
    cache_hit_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    billing_mode: Optional[Literal["subscription", "api"]] = Field(
        default=None,
        description="订阅制 (成本=固定月费,total_cost_7d 为 API 折算价值) 或按量计费",
    )
    monthly_subscription_usd: Optional[float] = Field(default=None, ge=0.0)
    # 匿名设备令牌 (客户端生成的 uuid) — 把提交归属到本人而非全局,
    # 从根上消除 "谁最后写谁赢" 的首页劫持与隐私泄露。
    device_token: Optional[str] = Field(default=None, max_length=64)

    class Config:
        json_schema_extra = {
            "example": {
                "score": 78,
                "tier": "A",
                "total_tokens_7d": 6736252,
                "total_cost_7d": 32.14,
                "rule_hits": {
                    "RULE_CONTEXT_BLOAT": True,
                    "RULE_LOW_CACHE_HIT": False,
                    "RULE_ERROR_RETRY_LOOP": False,
                    "RULE_MODEL_OVERUSE": False,
                    "RULE_MISSING_SKILL": True,
                }
            }
        }


class PercentileQuery(BaseModel):
    """Query input: user's own score."""
    score: int = Field(ge=0, le=100)


class PercentileResult(BaseModel):
    """Response: percentile ranking."""
    score: int
    percentile: int
    total_samples: int
    ranking_tier: Literal["Bottom 25%", "25-50%", "50-75%", "Top 25%"]
    description: str = ""

    def __init__(self, **data):
        super().__init__(**data)
        # Auto-generate description based on tier
        if self.ranking_tier == "Bottom 25%":
            self.description = f"你在全球 Agent 开发者中排名第 {self.percentile} 百分位。保持学习，优化空间很大。"
        elif self.ranking_tier == "25-50%":
            self.description = f"你在全球 Agent 开发者中排名第 {self.percentile} 百分位。表现良好，还有进步空间。"
        elif self.ranking_tier == "50-75%":
            self.description = f"你在全球 Agent 开发者中排名第 {self.percentile} 百分位。击败了 {self.percentile}% 的同行。"
        else:
            self.description = f"🏆 你在全球 Agent 开发者中排名第 {self.percentile} 百分位，击败了 {self.percentile}% 的同行。优秀！"


class APIResponse(BaseModel):
    """Standard API response wrapper."""
    success: bool
    message: str = ""
    data: Optional[Dict] = None


class StatisticsResponse(BaseModel):
    """Public statistics about the dataset."""
    total_submissions: int
    avg_score: float
    avg_tokens_7d: int
    score_distribution: Optional[Dict[str, int]] = None  # {"0-20": 5, "20-40": 12, ...}


# ---------------------------------------------------------------------------
# Coach report models
# ---------------------------------------------------------------------------

class ModelRoutingAdvice(BaseModel):
    """一条模型路由建议: 什么任务用什么模型。"""
    task: str
    recommended_model: str
    reason: str
    est_saving_pct: Optional[int] = None


class TimeInsight(BaseModel):
    """一条时间习惯洞察。"""
    pattern: str
    advice: str


class ActionItem(BaseModel):
    """一条优先级行动项。"""
    priority: int = Field(ge=1, le=3)
    title: str
    detail: str
    expected_impact: str


class DimensionScore(BaseModel):
    """体检分项得分 (0-100)。"""
    key: str
    label: str
    score: int = Field(ge=0, le=100)


class CheckupIssue(BaseModel):
    """一条体检发现项 (360 式: 问题 + 修复方案 + 分步执行)。"""
    severity: Literal["critical", "warning", "info"]
    dimension: str
    title: str
    detail: str
    fix: str
    impact: str
    steps: List[str] = Field(default_factory=list)  # 分步执行方案 (优化方案页)


class CoachReport(BaseModel):
    """完整教练报告: 排名 + 模型路由 + 时间习惯 + 目标路径 + 行动清单。"""
    generated_at: datetime
    score: int
    tier: str
    title_text: str = ""      # 段位称号 (AI 宗师/大师/熟练者/探索者)
    encourage_text: str = ""  # 互动鼓励语
    verdict_text: str = ""
    dimension_scores: List[DimensionScore] = Field(default_factory=list)
    issues: List[CheckupIssue] = Field(default_factory=list)
    global_percentile: int
    beat_ratio_text: str
    total_samples: int
    model_routing: List[ModelRoutingAdvice]
    routing_diagnosis: str
    # 若按 API 价目计费的等值节省 (订阅用户实际不省这笔钱,见 saving_text)
    est_monthly_saving_usd: float
    # 口径正确的钱表述 — 前端只显示这些,不自己拼美元
    value_text: str = ""    # 你的订阅值不值 (整句, CLI 用)
    saving_text: str = ""   # 优化后你得到什么 (整句, CLI 用)
    # 结构化版本 (仪表板用: 数值 + 标签 + 说明, 避免长句胶囊)
    value_headline: str = ""
    value_label: str = ""
    value_caption: str = ""
    saving_headline: str = ""
    saving_label: str = ""
    saving_caption: str = ""
    rank_headline: str = ""   # 「第 3 名」或「前 8%」
    rank_caption: str = ""    # 「12 位用户中」或「全球排名」
    billing_mode: Optional[str] = None
    time_insights: List[TimeInsight]
    peak_hours: List[int]
    hourly_histogram: Optional[List[int]] = None
    goal_inference: str
    path_advice: str
    action_items: List[ActionItem]
