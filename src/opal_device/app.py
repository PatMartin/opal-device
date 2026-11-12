"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import os
import time

from . import (
    __version__,
    backup,
    catalog,
    constants,
    defaults,
    devices,
    discovery,
    events,
    federation,
    groups,
    peers,
    plugins,
    profiles,
    settings,
    sharing,
    system,
)
from .auth import BearerAuthMiddleware
from .correlation import CorrelationMiddleware, ids_from_request
from .errors import ApiError, error_body
from .store import Store, default_db_path


def _envelope_response(request: Request, err: ApiError) -> JSONResponse:
    ids = ids_from_request(request)
    return JSONResponse(
        status_code=err.status,
        content=error_body(
            err,
            request_id=ids.request_id,
            transaction_id=ids.transaction_id,
            session_id=ids.session_id,
            causation_id=ids.causation_id,
        ),
        headers=err.headers,
    )


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(
        title="OPAL Devices API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = Store(default_db_path(data_dir))
    app.state.started_at = time.monotonic()
    app.state.auth_token = os.environ.get(constants.ENV_TOKEN) or None
    app.include_router(system.router)
    app.include_router(settings.router)
    app.include_router(devices.router)
    app.include_router(groups.router)
    app.include_router(defaults.router)
    app.include_router(plugins.router)
    app.include_router(catalog.router)
    app.include_router(discovery.router)
    app.include_router(events.router)
    app.include_router(peers.router)
    app.include_router(sharing.router)
    app.include_router(federation.router)
    app.include_router(profiles.router)
    app.include_router(backup.router)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return _envelope_response(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        err = ApiError(
            exc.status_code,
            exc.detail if isinstance(exc.detail, str) else "HTTP error",
            headers=getattr(exc, "headers", None),
        )
        return _envelope_response(request, err)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        err = ApiError(
            422,
            "Request validation failed",
            code="validation_error",
            details=jsonable_encoder(exc.errors()),
        )
        return _envelope_response(request, err)

    # Middleware order: Correlation outermost so every response (incl.
    # auth 401s) carries X-Opal-* headers; BearerAuth runs inside it.
    app.add_middleware(BearerAuthMiddleware)
    app.add_middleware(CorrelationMiddleware)
    return app
