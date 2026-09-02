from datetime import datetime
from typing import Optional, Dict
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, JSON, Index
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
from cloud.models import AggregatedScore

Base = declarative_base()


class ScoreRecord(Base):
    """ORM model for anonymized AI Health Score records."""
    __tablename__ = "scores"

    id = Column(Integer, primary_key=True)
    score = Column(Integer, nullable=False, index=True)
    tier = Column(String(1), nullable=False)
    total_tokens_7d = Column(Integer, nullable=False)
    total_cost_7d = Column(Float, nullable=False)
    rule_hits = Column(JSON, nullable=True)
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    # Rich coach profile (all nullable — backward compatible)
    usage_by_model = Column(JSON, nullable=True)
    hourly_histogram = Column(JSON, nullable=True)
    task_types = Column(JSON, nullable=True)
    goal = Column(String(500), nullable=True)
    cache_hit_rate = Column(Float, nullable=True)
    billing_mode = Column(String(20), nullable=True)
    monthly_subscription_usd = Column(Float, nullable=True)
    # 匿名设备令牌 — 提交归属维度 (nullable: 旧记录无 token)
    device_token = Column(String(64), nullable=True, index=True)

    # Index for common queries
    __table_args__ = (
        Index('idx_score_submitted', 'score', 'submitted_at'),
    )


class SubscriberRecord(Base):
    """订阅 AI 资讯/新品推荐的邮箱。"""
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True)
    email = Column(String(320), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MasterTipRecord(Base):
    """采集 pipeline 提交的大师推荐条目 (v2 动态内容池)。"""
    __tablename__ = "master_tips"

    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False, unique=True)
    detail = Column(String(1000), nullable=False)
    why_trust = Column(String(500), nullable=False)
    source = Column(String(300), nullable=False)
    privacy_note = Column(String(500), nullable=True)
    install = Column(String(500), nullable=True)
    level = Column(String(20), nullable=False, default="beginner")
    active = Column(Integer, nullable=False, default=1)  # 1=进池, 0=下架
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ProSubscriptionRecord(Base):
    """Stripe Pro 订阅状态 (由 webhook 维护)。"""
    __tablename__ = "pro_subscriptions"

    id = Column(Integer, primary_key=True)
    email = Column(String(320), nullable=False, index=True)
    stripe_customer_id = Column(String(120), nullable=True)
    stripe_subscription_id = Column(String(120), nullable=True, unique=True)
    status = Column(String(30), nullable=False, default="active")  # active | canceled
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DatabaseEngine:
    """Database manager for cloud aggregated scores."""

    def __init__(self, db_url: str = "sqlite:///agentfit_cloud.db"):
        self.db_url = db_url
        engine_kwargs = {
            "echo": False,
            "pool_pre_ping": True,  # Test connection before using
        }
        if "sqlite" in db_url:
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in db_url:
                # In-memory SQLite: share one connection across threads,
                # otherwise each pooled connection sees an empty database
                from sqlalchemy.pool import StaticPool
                engine_kwargs["poolclass"] = StaticPool
        self.engine = create_engine(db_url, **engine_kwargs)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def init_db(self):
        """Initialize database schema and apply lightweight column migration.

        create_all only creates missing tables — it never ALTERs existing
        ones. Deployments that created the scores table before the coach
        columns existed need ADD COLUMN, otherwise every INSERT/SELECT
        touching the new columns fails.
        """
        Base.metadata.create_all(self.engine)
        self._migrate_missing_columns()

    def _migrate_missing_columns(self):
        from sqlalchemy import inspect, text

        column_ddl = {
            "usage_by_model": "JSON",
            "hourly_histogram": "JSON",
            "task_types": "JSON",
            "goal": "VARCHAR(500)",
            "cache_hit_rate": "FLOAT",
            "billing_mode": "VARCHAR(20)",
            "monthly_subscription_usd": "FLOAT",
            "device_token": "VARCHAR(64)",
        }
        inspector = inspect(self.engine)
        if "scores" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("scores")}
        missing = [name for name in column_ddl if name not in existing]
        if not missing:
            return
        with self.engine.begin() as conn:
            for name in missing:
                conn.execute(
                    text(f"ALTER TABLE scores ADD COLUMN {name} {column_ddl[name]}")
                )

    def save_aggregated_score(self, score: AggregatedScore) -> bool:
        """Save an anonymized score record to the database."""
        session = self.SessionLocal()
        try:
            record = ScoreRecord(
                score=score.score,
                tier=score.tier,
                total_tokens_7d=score.total_tokens_7d,
                total_cost_7d=score.total_cost_7d,
                rule_hits=score.rule_hits or {},
                submitted_at=score.submitted_at or datetime.utcnow(),
                usage_by_model=getattr(score, "usage_by_model", None) or None,
                hourly_histogram=getattr(score, "hourly_histogram", None),
                task_types=getattr(score, "task_types", None) or None,
                goal=getattr(score, "goal", None),
                cache_hit_rate=getattr(score, "cache_hit_rate", None),
                billing_mode=getattr(score, "billing_mode", None),
                monthly_subscription_usd=getattr(score, "monthly_subscription_usd", None),
                device_token=getattr(score, "device_token", None),
            )
            session.add(record)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            print(f"Error saving score: {e}")
            return False
        finally:
            session.close()

    def _latest_per_device(self, session) -> list:
        """每台设备只取最近一次提交 — 排名/统计的唯一口径。

        为什么: 每日自动 sync 让一个人 30 天贡献 30 行, 若按行数算,
        "打败全球 X%" 的分母是提交数而不是人数, 数字站不住。
        无 device_token 的旧记录 (身份修复前 / 探针) 一律不计入。
        """
        rows = (
            session.query(
                ScoreRecord.device_token,
                ScoreRecord.score,
                ScoreRecord.tier,
                ScoreRecord.total_tokens_7d,
                ScoreRecord.total_cost_7d,
            )
            .filter(ScoreRecord.device_token.isnot(None))
            .order_by(
                ScoreRecord.device_token,
                ScoreRecord.submitted_at.desc(),
                ScoreRecord.id.desc(),
            )
            .all()
        )
        latest = []
        seen = set()
        for row in rows:
            if row[0] in seen:
                continue
            seen.add(row[0])
            latest.append(row)
        return latest

    def get_percentile_for_score(self, score: int) -> int:
        """Percentile rank (0-100) among devices (latest submission per device)."""
        session = self.SessionLocal()
        try:
            scores = [r[1] for r in self._latest_per_device(session)]
            total_count = len(scores)
            if total_count < 2:
                return 50  # Default to median if insufficient data

            lower_count = sum(1 for s in scores if s < score)
            equal_count = sum(1 for s in scores if s == score)

            if equal_count > 0:
                percentile = ((lower_count + equal_count / 2.0) / total_count) * 100
            else:
                percentile = (lower_count / total_count) * 100

            return max(1, min(100, int(percentile)))
        finally:
            session.close()

    def add_subscriber(self, email: str) -> bool:
        """保存订阅邮箱。重复订阅视为成功 (幂等)。"""
        session = self.SessionLocal()
        try:
            exists = (
                session.query(SubscriberRecord)
                .filter(SubscriberRecord.email == email)
                .first()
            )
            if exists:
                return True
            session.add(SubscriberRecord(email=email))
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            print(f"Error saving subscriber: {e}")
            return False
        finally:
            session.close()

    def add_master_tip(self, tip: Dict) -> bool:
        """入池一条大师推荐 (title 唯一,重复提交幂等成功)。"""
        session = self.SessionLocal()
        try:
            exists = (
                session.query(MasterTipRecord)
                .filter(MasterTipRecord.title == tip["title"])
                .first()
            )
            if exists:
                return True
            session.add(MasterTipRecord(
                kind=tip["kind"],
                title=tip["title"],
                detail=tip["detail"],
                why_trust=tip["why_trust"],
                source=tip["source"],
                privacy_note=tip.get("privacy_note"),
                install=tip.get("install"),
                level=tip.get("level", "beginner"),
            ))
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            print(f"Error adding master tip: {e}")
            return False
        finally:
            session.close()

    def retire_master_tip(self, title: str) -> bool:
        """下架一条动态库内容 (active=0)。找不到返回 False。"""
        session = self.SessionLocal()
        try:
            record = (
                session.query(MasterTipRecord)
                .filter(MasterTipRecord.title == title)
                .first()
            )
            if record is None:
                return False
            record.active = 0
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            print(f"Error retiring master tip: {e}")
            return False
        finally:
            session.close()

    def get_active_master_tips(self) -> list:
        """取动态内容池的全部在架条目 (dict 列表)。"""
        session = self.SessionLocal()
        try:
            records = (
                session.query(MasterTipRecord)
                .filter(MasterTipRecord.active == 1)
                .order_by(MasterTipRecord.created_at.desc())
                .all()
            )
            return [
                {
                    "kind": r.kind,
                    "title": r.title,
                    "detail": r.detail,
                    "why_trust": r.why_trust,
                    "source": r.source,
                    "privacy_note": r.privacy_note,
                    "install": r.install,
                    "level": r.level,
                }
                for r in records
            ]
        finally:
            session.close()

    def count_subscribers(self) -> int:
        session = self.SessionLocal()
        try:
            return session.query(func.count(SubscriberRecord.id)).scalar() or 0
        finally:
            session.close()

    def upsert_pro_subscription(
        self,
        email: str,
        stripe_customer_id: Optional[str],
        stripe_subscription_id: Optional[str],
        status: str,
    ) -> bool:
        """按 subscription_id 幂等更新 Pro 订阅状态 (webhook 可能重发)。"""
        session = self.SessionLocal()
        try:
            record = None
            if stripe_subscription_id:
                record = (
                    session.query(ProSubscriptionRecord)
                    .filter(ProSubscriptionRecord.stripe_subscription_id == stripe_subscription_id)
                    .first()
                )
            if record is None:
                record = ProSubscriptionRecord(
                    email=email,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                )
                session.add(record)
            record.status = status
            if email:
                record.email = email
            record.updated_at = datetime.utcnow()
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            print(f"Error upserting pro subscription: {e}")
            return False
        finally:
            session.close()

    def get_pro_status(self, email: str) -> str:
        """返回该邮箱的 Pro 状态: active / canceled / none。"""
        session = self.SessionLocal()
        try:
            record = (
                session.query(ProSubscriptionRecord)
                .filter(ProSubscriptionRecord.email == email)
                .order_by(ProSubscriptionRecord.updated_at.desc())
                .first()
            )
            return record.status if record else "none"
        finally:
            session.close()

    def get_latest_submission(self, device_token: Optional[str] = None) -> Optional[Dict]:
        """Return the most recent submission as a dict (None if empty).

        device_token 给定时只在该设备自己的提交里取最新 — 这是首页按 token
        隔离的基础。device_token=None 保留为运营/全局视图 (仅内部或需 api_key 的
        接口使用),公开接口一律传 token。
        """
        session = self.SessionLocal()
        try:
            query = session.query(ScoreRecord)
            if device_token is not None:
                query = query.filter(ScoreRecord.device_token == device_token)
            record = (
                query
                .order_by(ScoreRecord.submitted_at.desc(), ScoreRecord.id.desc())
                .first()
            )
            if record is None:
                return None
            return {
                "score": record.score,
                "tier": record.tier,
                "total_tokens_7d": record.total_tokens_7d,
                "total_cost_7d": record.total_cost_7d,
                "rule_hits": record.rule_hits or {},
                "submitted_at": record.submitted_at,
                "usage_by_model": record.usage_by_model or {},
                "hourly_histogram": record.hourly_histogram,
                "task_types": record.task_types or {},
                "goal": record.goal,
                "cache_hit_rate": record.cache_hit_rate,
                "billing_mode": record.billing_mode,
                "monthly_subscription_usd": record.monthly_subscription_usd,
            }
        finally:
            session.close()

    def get_rank_for_score(self, score: int) -> int:
        """返回该分数在全部设备中的名次 (1 = 最高分, 每设备只算最近一次)。"""
        session = self.SessionLocal()
        try:
            higher = sum(1 for r in self._latest_per_device(session) if r[1] > score)
            return higher + 1
        finally:
            session.close()

    def get_statistics(self) -> Dict:
        """Aggregate statistics over devices (latest submission per device)."""
        session = self.SessionLocal()
        try:
            latest = self._latest_per_device(session)
            total = len(latest)

            if total == 0:
                return {
                    "total_submissions": 0,
                    "avg_score": 0.0,
                    "avg_tokens_7d": 0
                }

            avg_score = sum(r[1] for r in latest) / total
            avg_tokens = sum(r[3] for r in latest) / total
            avg_cost = sum(r[4] for r in latest) / total

            return {
                "total_submissions": total,
                "avg_score": round(float(avg_score), 1),
                "avg_tokens_7d": int(avg_tokens),
                "avg_cost_7d": round(float(avg_cost), 2)
            }
        finally:
            session.close()

    def get_score_distribution(self) -> Dict[str, int]:
        """Score distribution in 20-point buckets (latest submission per device)."""
        session = self.SessionLocal()
        try:
            buckets = {
                "0-20": 0,
                "20-40": 0,
                "40-60": 0,
                "60-80": 0,
                "80-100": 0
            }

            records = [(r[1],) for r in self._latest_per_device(session)]
            for (score,) in records:
                if score < 20:
                    buckets["0-20"] += 1
                elif score < 40:
                    buckets["20-40"] += 1
                elif score < 60:
                    buckets["40-60"] += 1
                elif score < 80:
                    buckets["60-80"] += 1
                else:
                    buckets["80-100"] += 1

            return buckets
        finally:
            session.close()

    def get_tier_distribution(self) -> Dict[str, int]:
        """Get distribution by tier (S, A, B, C)."""
        session = self.SessionLocal()
        try:
            tiers = {"S": 0, "A": 0, "B": 0, "C": 0}

            for tier in ["S", "A", "B", "C"]:
                count = session.query(func.count(ScoreRecord.id)).filter(ScoreRecord.tier == tier).scalar() or 0
                tiers[tier] = count

            return tiers
        finally:
            session.close()

    def cleanup_old_records(self, days_old: int = 90):
        """Remove records older than N days (GDPR compliance)."""
        session = self.SessionLocal()
        try:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=days_old)
            deleted = session.query(ScoreRecord).filter(ScoreRecord.submitted_at < cutoff).delete()
            session.commit()
            return deleted
        except Exception as e:
            session.rollback()
            print(f"Error cleaning records: {e}")
            return 0
        finally:
            session.close()
