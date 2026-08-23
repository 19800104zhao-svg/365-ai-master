"""AgentFit Cloud API main application."""
import logging
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from cloud.api import router
from cloud.database import DatabaseEngine
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
    title="AgentFit Cloud API",
    description="Privacy-first percentile benchmark for AI Agent usage coaching",
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
    allow_headers=["Content-Type", "X-API-Key"],
)

# Initialize database
db = DatabaseEngine(db_url=settings.database_url)
try:
    db.init_db()
    logger.info(f"Database initialized: {settings.database_url}")
except Exception as e:
    # Table may already exist (e.g. concurrent worker startup) — not fatal
    logger.warning(f"Database init skipped: {e}")


# Serve dashboard
@app.get("/")
async def dashboard():
    """Serve the analytics dashboard."""
    dashboard_path = Path(__file__).parent.parent / "dashboard" / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path, media_type="text/html")
    return JSONResponse(
        status_code=404,
        content={"detail": "Dashboard not found"}
    )


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


# Startup event
@app.on_event("startup")
async def startup():
    logger.info("AgentFit Cloud API starting up")
    logger.info(f"Database: {settings.database_url}")
    logger.info(f"API Key Required: {settings.require_api_key}")
    logger.info(f"Submissions Enabled: {settings.enable_submissions}")
    logger.info(f"Percentile Queries Enabled: {settings.enable_percentile_queries}")


# Shutdown event
@app.on_event("shutdown")
async def shutdown():
    logger.info("AgentFit Cloud API shutting down")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers,
        log_level=settings.log_level.lower(),
    )
