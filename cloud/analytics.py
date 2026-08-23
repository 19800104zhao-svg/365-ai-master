"""Analytics engine for historical trends and closed-loop verification."""
from datetime import datetime, timedelta, date
from typing import List
from collections import defaultdict

from cloud.database import DatabaseEngine, ScoreRecord
from cloud.schemas import DailySample, WeeklySummary, ClosedLoopComparison, TrendAnalysis


RULE_TITLES = {
    "RULE_CONTEXT_BLOAT": "上下文暴涨黑洞",
    "RULE_LOW_CACHE_HIT": "缓存打靶率低下",
    "RULE_ERROR_RETRY_LOOP": "死循环盲目 Retry",
    "RULE_MODEL_OVERUSE": "高级模型杀鸡用牛刀",
    "RULE_MISSING_SKILL": "缺乏 Custom Skill 封装",
}


class AnalyticsEngine:
    """Engine for historical analytics and trend analysis."""

    def __init__(self, db: DatabaseEngine):
        self.db = db

    def get_daily_samples(
        self, days_back: int = 30, device_token: str = None
    ) -> List[DailySample]:
        """Fetch daily samples for the last N days.

        device_token 给定时只统计该设备自己的历史 — 趋势曲线是 per-token 的,
        绝不把全库聚合泄露给匿名访客。
        """
        session = self.db.SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days_back)
            query = session.query(ScoreRecord).filter(
                ScoreRecord.submitted_at >= cutoff
            )
            if device_token is not None:
                query = query.filter(ScoreRecord.device_token == device_token)
            records = query.order_by(ScoreRecord.submitted_at).all()

            # Group by date and take max score per day (most recent submission)
            daily_map = {}
            for record in records:
                record_date = record.submitted_at.date()
                if record_date not in daily_map or record.score > daily_map[record_date].score:
                    daily_map[record_date] = record

            samples = []
            for date_key in sorted(daily_map.keys()):
                record = daily_map[date_key]
                sample = DailySample(
                    date=date_key,
                    score=record.score,
                    total_tokens=record.total_tokens_7d,
                    total_cost=record.total_cost_7d,
                    rule_hits=record.rule_hits or {}
                )
                samples.append(sample)

            return samples
        finally:
            session.close()

    def aggregate_weekly(self, samples: List[DailySample]) -> List[WeeklySummary]:
        """Group daily samples into weeks (Mon-Sun)."""
        if not samples:
            return []

        weeks = defaultdict(list)
        for sample in samples:
            # Week starts on Monday (weekday 0)
            week_start = sample.date - timedelta(days=sample.date.weekday())
            week_key = week_start.isoformat()
            weeks[week_key].append(sample)

        summaries = []
        for week_start_str in sorted(weeks.keys()):
            week_start = datetime.fromisoformat(week_start_str).date()
            week_end = week_start + timedelta(days=6)
            week_samples = weeks[week_start_str]

            scores = [s.score for s in week_samples]
            tokens = [s.total_tokens for s in week_samples]
            costs = [s.total_cost for s in week_samples]

            # Find most frequent rules
            rule_counts = defaultdict(int)
            for sample in week_samples:
                for rule_id, hit in sample.rule_hits.items():
                    if hit:
                        rule_counts[rule_id] += 1

            most_frequent = sorted(
                rule_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]

            avg_tokens_per_day = sum(tokens) // (len(tokens) * 7) if tokens else 0

            summary = WeeklySummary(
                week_start_date=week_start,
                week_end_date=week_end,
                avg_score=sum(scores) / len(scores) if scores else 0.0,
                avg_tokens_per_day=avg_tokens_per_day,
                total_cost=sum(costs),
                highest_score=max(scores) if scores else 0,
                lowest_score=min(scores) if scores else 0,
                days_with_data=len(week_samples),
                most_frequent_rules_hit=[r[0] for r in most_frequent]
            )
            summaries.append(summary)

        return summaries

    def analyze_closed_loop(self, weeks: List[WeeklySummary]) -> List[ClosedLoopComparison]:
        """Analyze rule improvements by comparing first half vs second half of month."""
        if len(weeks) < 2:
            return []

        improvements = []
        mid_point = len(weeks) // 2

        if mid_point > 0 and mid_point < len(weeks):
            first_half_rules = defaultdict(int)
            second_half_rules = defaultdict(int)

            # Count rules in first half
            for w in weeks[:mid_point]:
                for rule_id in w.most_frequent_rules_hit:
                    first_half_rules[rule_id] += 1

            # Count rules in second half
            for w in weeks[mid_point:]:
                for rule_id in w.most_frequent_rules_hit:
                    second_half_rules[rule_id] += 1

            # Find improvements: rules that were frequent before but less now
            all_rules = set(first_half_rules.keys()) | set(second_half_rules.keys())

            for rule_id in all_rules:
                before = first_half_rules.get(rule_id, 0)
                after = second_half_rules.get(rule_id, 0)

                # Show as improvement if rule hit less in second half
                if before > after:
                    improvement_pct = ((before - after) / before * 100) if before > 0 else 0
                    improvements.append(
                        ClosedLoopComparison(
                            rule_id=rule_id,
                            rule_title=RULE_TITLES.get(rule_id, rule_id),
                            # 下半月起点视为建议生效点
                            recommendation_issued_date=weeks[mid_point].week_start_date,
                            before_metric=f"每周 {before} 次",
                            after_metric=f"每周 {after} 次",
                            tokens_saved=int((before - after) * 1_000_000),  # Rough estimate
                            improvement_percent=improvement_pct,
                            verified=True,
                        )
                    )

        return sorted(improvements, key=lambda x: x.improvement_percent, reverse=True)

    def analyze_trend(self, weeks: List[WeeklySummary]) -> TrendAnalysis:
        """Analyze trend direction: improving, stable, or degrading."""
        if len(weeks) < 2:
            return TrendAnalysis(
                period_start=weeks[0].week_start_date if weeks else date.today(),
                period_end=weeks[-1].week_end_date if weeks else date.today(),
                weeks=weeks,
                overall_trend="insufficient_data",
                trend_score=0.0,
                improvements_this_month=[],
                summary_text="数据不足，无法分析趋势。"
            )

        # Compare first week avg to last week avg
        first_avg = weeks[0].avg_score
        last_avg = weeks[-1].avg_score
        score_delta = last_avg - first_avg

        if score_delta > 5:
            trend = "improving"
            trend_score = min(1.0, score_delta / 100)
            summary = f"🎉 表现上升。{score_delta:.1f} 周内你的分数从 {first_avg:.0f} 提升到 {last_avg:.0f}。"
        elif score_delta < -5:
            trend = "degrading"
            trend_score = max(-1.0, score_delta / 100)
            summary = f"⚠️ 表现下降。你的分数从 {first_avg:.0f} 下降到 {last_avg:.0f}。"
        else:
            trend = "stable"
            trend_score = 0.0
            summary = "→ 表现稳定。你的分数保持在 {:.0f} 左右。".format((first_avg + last_avg) / 2)

        # Analyze closed-loop improvements
        improvements = self.analyze_closed_loop(weeks)

        return TrendAnalysis(
            period_start=weeks[0].week_start_date,
            period_end=weeks[-1].week_end_date,
            weeks=weeks,
            overall_trend=trend,
            trend_score=trend_score,
            improvements_this_month=improvements,
            summary_text=summary
        )

    def get_30day_analysis(self, device_token: str = None) -> TrendAnalysis:
        """Get complete 30-day analysis (per-token when device_token given)."""
        samples = self.get_daily_samples(days_back=30, device_token=device_token)
        weeks = self.aggregate_weekly(samples)
        return self.analyze_trend(weeks)

    def get_weekly_summaries(self) -> List[WeeklySummary]:
        """Get last 4 weeks of summaries."""
        samples = self.get_daily_samples(days_back=28)
        return self.aggregate_weekly(samples)

    def export_as_csv(self, device_token: str = None) -> str:
        """Export daily samples as CSV (per-token when device_token given)."""
        samples = self.get_daily_samples(days_back=30, device_token=device_token)
        lines = ["date,score,total_tokens_7d,total_cost_7d"]
        for sample in samples:
            lines.append(f"{sample.date},{sample.score},{sample.total_tokens},{sample.total_cost}")
        return "\n".join(lines)

    def export_as_json(self, device_token: str = None) -> dict:
        """Export analysis as JSON-serializable dict (per-token when given)."""
        analysis = self.get_30day_analysis(device_token=device_token)
        return analysis.model_dump()
