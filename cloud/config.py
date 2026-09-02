import os
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Cloud API configuration from environment variables or defaults."""

    # Database
    # Railway/Heroku may provide postgres:// which SQLAlchemy 2.x rejects —
    # normalize to postgresql://
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///agentfit_cloud.db"
    ).replace("postgres://", "postgresql://", 1)

    # API
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    api_workers: int = int(os.getenv("API_WORKERS", "4"))

    # Security
    api_key: str = os.getenv("API_KEY", "dev-key-change-in-production")
    require_api_key: bool = os.getenv("REQUIRE_API_KEY", "false").lower() == "true"

    # CORS — read as comma-separated string (avoids pydantic-settings JSON
    # parsing errors when CORS_ORIGINS is set as "a,b,c" in .env)
    cors_origins_raw: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:8000,http://localhost:9000",
    )

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    # Feature flags
    enable_submissions: bool = os.getenv("ENABLE_SUBMISSIONS", "true").lower() == "true"
    enable_percentile_queries: bool = os.getenv("ENABLE_PERCENTILE_QUERIES", "true").lower() == "true"
    enable_stats: bool = os.getenv("ENABLE_STATS", "true").lower() == "true"
    enable_analytics: bool = os.getenv("ENABLE_ANALYTICS", "true").lower() == "true"

    # Rate limiting
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    rate_limit_requests_per_minute: int = int(os.getenv("RATE_LIMIT_RPM", "60"))

    # Data retention
    record_retention_days: int = int(os.getenv("RECORD_RETENTION_DAYS", "90"))

    # Stripe billing (Pro $1/月) — 三个变量都配置后 /billing/checkout 才启用
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe_price_id: str = os.getenv("STRIPE_PRICE_ID", "")
    site_url: str = os.getenv("SITE_URL", "https://360-ai-coach-production.up.railway.app")

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_price_id)

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "agentfit_cloud.log")

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # tolerate unrelated variables in .env


settings = Settings()

# Fail fast instead of silently falling back to ephemeral SQLite in production:
# on Railway the container filesystem is wiped on every deploy, so a missing
# DATABASE_URL would mean "service is green, all user data gone" with no alarm.
if os.getenv("RAILWAY_ENVIRONMENT") and not os.getenv("DATABASE_URL"):
    raise RuntimeError(
        "DATABASE_URL is not set in a Railway environment. Refusing to start with "
        "ephemeral SQLite — attach the Postgres service or set DATABASE_URL."
    )
