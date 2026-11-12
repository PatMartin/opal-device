"""Bearer authentication (kernel BearerAuth scheme).

OPAL_DEVICE_TOKEN (env, opaque). Set -> every /api/v1 request needs
`Authorization: Bearer <token>` (401 + WWW-Authenticate: Bearer).
Unset -> loopback development mode; main.py refuses non-loopback binds
without it. The token is never logged, exported, echoed, or accepted in
query strings. Runs inside CorrelationMiddleware so 401s still carry
X-Opal-* headers.
"""

from __future__ import annotations

import secrets

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import os

from .correlation import ids_from_request
from .errors import ApiError, error_body

_LOOPBACK_HOSTS = {"localhost", "::1", "[::1]"}


def is_loopback_host(host: str) -> bool:
    h = host.strip().lower()
    return h in _LOOPBACK_HOSTS or h.startswith("127.")


def _envelope(request: Request, err: ApiError) -> JSONResponse:
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


def _unauthorized(request: Request) -> JSONResponse:
    return _envelope(
        request,
        ApiError(
            401,
            "Missing or invalid authentication credentials",
            code="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        ),
    )


def _forbidden(request: Request) -> JSONResponse:
    return _envelope(
        request,
        ApiError(403, "Authenticated but not permitted", code="forbidden"),
    )


def _peer_principal(request: Request, supplied: str) -> dict | None:
    """Match a presented token against registered peers' credential refs."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return None
    from .peers import all_peers

    for peer in all_peers(store):
        ref = peer.get("credential_ref")
        if not ref:
            continue
        expected = os.environ.get(f"OPAL_DEVICE_CRED_{ref.upper()}")
        if expected and secrets.compare_digest(supplied, expected):
            return {
                "kind": "peer",
                "peer_id": peer["id"],
                "trusted": bool(peer.get("trusted")),
            }
    return None


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        token = getattr(request.app.state, "auth_token", None)
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        supplied = (
            auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        )
        if supplied:
            if token and secrets.compare_digest(supplied, token):
                request.state.principal = {"kind": "management"}
                return await call_next(request)
            peer = _peer_principal(request, supplied)
            if peer is not None:
                request.state.principal = peer
                if (
                    peer["trusted"]
                    and request.url.path.startswith("/api/v1/federation/")
                ):
                    return await call_next(request)
                return _forbidden(request)
            return _unauthorized(request)
        if not token:
            # loopback development mode
            request.state.principal = {"kind": "management"}
            return await call_next(request)
        return _unauthorized(request)
