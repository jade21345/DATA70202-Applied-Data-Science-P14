"""FastAPI application entry point.

Usage (from project root):
    uvicorn backend.main:app --reload --port 8000

Then:
    http://localhost:8000           - Jade's frontend (app/)
    http://localhost:8000/docs      - interactive Swagger UI
    http://localhost:8000/api/health - health check

The same server doubles as a static file host for app/ so the frontend
team can develop against a single origin without a separate live-server.
In production these can be split.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import diagnostics, maps, results, scenarios
from .schemas import HealthResponse
from .services import get_output_service

# Logging configuration: send to stdout, INFO by default.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backend")

app = FastAPI(
    title="Portugal Mixed-Member Electoral Model API",
    version="1.0.0",
    description=(
        "Backend bridge between the Python algorithm pipeline and the "
        "static frontend. Reads pre-computed scenario outputs and "
        "returns frontend-ready JSON / GeoJSON with party metadata "
        "joined in."
    ),
)

# CORS: open in development. Tighten in production by listing the
# specific frontend origin(s) instead of "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---- Health -----------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def health():
    out = get_output_service()
    return HealthResponse(status="ok", n_scenarios=len(out.list_scenarios()))


# ---- API routers ------------------------------------------------------------

app.include_router(scenarios.router)
app.include_router(maps.router)
app.include_router(results.router)
app.include_router(diagnostics.router)


# ---- Static frontend (Jade's app/ folder) -----------------------------------
# Serve app/ at the root URL so http://localhost:8000/ shows the
# frontend's index.html. The /api/* routes are registered above and
# take priority over static files.

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"

if APP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(APP_DIR), html=True), name="frontend")
    logger.info("Mounted static frontend from %s", APP_DIR)
else:
    @app.get("/", response_class=JSONResponse, tags=["health"])
    def root_no_frontend():
        return {
            "message": (
                "Backend is running, but no app/ directory was found. "
                "API endpoints are at /api/*."
            ),
        }
    logger.warning("app/ directory not found at %s; static frontend not mounted.", APP_DIR)
