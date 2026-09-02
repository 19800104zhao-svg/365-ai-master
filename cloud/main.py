"""365 AI Master — cloud API application entrypoint."""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cloud.api import db, router
from cloud.config import settings

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=settings.log_file
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="365 AI Master API",
    description="Privacy-first AI usage health check for Claude Code & Codex",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Device-Token"],
)

# Initialize database schema (single engine instance shared with cloud.api —
# a second instance here used to mean two connection pools and a test-only
# monkeypatch footgun).
try:
    db.init_db()
    logger.info(f"Database initialized: {settings.database_url}")
except Exception as e:
    # Table may already exist (e.g. concurrent worker startup) — not fatal
    logger.warning(f"Database init skipped: {e}")

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"

# Static assets (og:image etc.)
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard.

    The HTML carries a `__SITE_URL__` placeholder for absolute URLs that
    must match the deployed origin (og:url / og:image). Substituting it
    here means the domain lives in one place (SITE_URL env) instead of
    being hard-coded into the page.
    """
    index = DASHBOARD_DIR / "index.html"
    if not index.exists():
        return JSONResponse(status_code=404, content={"detail": "Dashboard not found"})
    html = index.read_text(encoding="utf-8").replace(
        "__SITE_URL__", settings.site_url.rstrip("/")
    )
    return HTMLResponse(html)


# Include API routes
app.include_router(router)


# Global error handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


@app.on_event("startup")
async def startup():
    logger.info("365 AI Master API starting up")
    logger.info(f"Database: {settings.database_url}")
    logger.info(f"Site URL: {settings.site_url}")
    logger.info(f"Rate limit: {settings.rate_limit_requests_per_minute}/min per IP")


@app.on_event("shutdown")
async def shutdown():
    logger.info("365 AI Master API shutting down")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
