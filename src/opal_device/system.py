"""Kernel system surface: service identity and health probes.

Note: /health/live and /health/ready return OpalHealthProbe bodies even
on 503 (per contract); the aggregate /health/ report uses the OpalError
envelope for 503 like everywhere else.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import __version__, constants
from .dependencies import StoreDep
from .store import Store

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_ok(store: Store) -> bool:
    try:
        store.fetchone("SELECT 1")
        return True
    except Exception:
        return False


@router.get("/api/v1/info/")
def info() -> dict:
    return {
        "name": constants.SERVICE_NAME,
        "version": __version__,
        "api_version": constants.API_VERSION,
        "description": "OPAL Devices metadata registry",
    }


@router.get("/api/v1/info/ping")
def info_ping() -> dict:
    return {"status": "ok"}


@router.get("/api/v1/health/live")
def health_live() -> dict:
    return {"status": "ok", "ready": True, "version": __version__}


@router.get("/api/v1/health/ready")
def health_ready(store: StoreDep) -> JSONResponse:
    if _db_ok(store):
        return JSONResponse(
            {"status": "ok", "ready": True, "version": __version__}
        )
    return JSONResponse(
        status_code=503,
        content={"status": "unavailable", "ready": False, "version": __version__},
    )


@router.get("/api/v1/health/")
def health(request: Request, store: StoreDep) -> dict:
    db_ok = _db_ok(store)
    started = getattr(request.app.state, "started_at", time.monotonic())
    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "ready": db_ok,
        "database_ready": db_ok,
        "uptime_seconds": round(time.monotonic() - started, 3),
        "checked_at": _now(),
        "components": {
            "database": {
                "state": "ok" if db_ok else "unavailable",
            }
        },
    }
