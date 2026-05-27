"""
Savantsdatabas Orchestrator API.

FastAPI app that exposes:
- Health/status endpoints (root, /health)
- Database stats (/db/stats)

Future endpoints (per docs/ARCHITECTURE.md Fase 5):
- /orchestrator/plan
- /scrape/job/*

Run:
    cd backend
    .venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from rich.logging import RichHandler

# ─────────────────────────────────────────────────────────────────────────────
# Env + logging bootstrap (must happen BEFORE importing deps that read env)
# ─────────────────────────────────────────────────────────────────────────────

# Load backend/.env regardless of CWD when uvicorn launches.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_ROOT / ".env")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
log = logging.getLogger("savantsdatabas.api")

from api.deps import get_supabase, verify_token  # noqa: E402  (after env load)
from api.orchestrator import router as orchestrator_router  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Savantsdatabas Orchestrator API",
    version="0.1.0",
    description=(
        "Backend orchestrator per Savantsdatabas: gestisce piani di scraping, "
        "job, statistiche DB e ingest pipeline (Fase 5 di ARCHITECTURE.md)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Orchestrator router — Fase 6 (LangGraph plan-phase endpoints)
app.include_router(orchestrator_router)


@app.on_event("startup")
def _startup_log() -> None:
    if not os.environ.get("ORCHESTRATOR_API_TOKEN"):
        log.warning(
            "ORCHESTRATOR_API_TOKEN not set — API is unauthenticated (dev mode)."
        )
    else:
        log.info("Bearer auth enabled (ORCHESTRATOR_API_TOKEN set).")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/")
def root() -> dict[str, str]:
    """Liveness probe — no DB call."""
    return {"status": "ok", "service": "savantsdatabas-orchestrator"}


@app.get("/health", dependencies=[Depends(verify_token)])
def health() -> dict[str, Any]:
    """Readiness probe — pings Supabase with a cheap count query."""
    supabase_status = "connected"
    db_company_count = 0
    try:
        sb = get_supabase()
        # Cheap exact count on companies as a connectivity check.
        res = sb.table("companies").select("id", count="exact").limit(1).execute()
        db_company_count = res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.exception("Supabase health check failed: %s", exc)
        supabase_status = "error"

    return {
        "status": "ok" if supabase_status == "connected" else "degraded",
        "supabase": supabase_status,
        "db_company_count": db_company_count,
    }


def _count(sb, table: str, **filters: Any) -> int:
    """Helper: head=True count with optional eq filters. Returns 0 on error."""
    try:
        q = sb.table(table).select("id", count="exact", head=True)
        for col, val in filters.items():
            q = q.eq(col, val)
        res = q.execute()
        return res.count or 0
    except Exception as exc:  # noqa: BLE001
        log.warning("count(%s, %s) failed: %s", table, filters, exc)
        return 0


@app.get("/db/stats", dependencies=[Depends(verify_token)])
def db_stats() -> dict[str, Any]:
    """Return per-table counts used by the dashboard."""
    sb = get_supabase()
    return {
        "companies": {
            "total": _count(sb, "companies"),
            "arkiverade": _count(sb, "companies", arkiverad=True),
        },
        "contacts": {
            "total": _count(sb, "contacts"),
            "verified": _count(sb, "contacts", verifierad=True),
        },
        "sources": {"total": _count(sb, "sources")},
        "plans": {"total": _count(sb, "plans")},
        "scrape_jobs": {"total": _count(sb, "scrape_jobs")},
        "knowledge_chunks": {"total": _count(sb, "knowledge_chunks")},
    }
