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

from .correlation import ids_from_request
from .errors import ApiError, error_body

_LOOPBACK_HOSTS = {"localhost", "::1", "[::1]"}


def is_loopback_host(host: str) -> bool:
    h = host.strip().lower()
    return h in _LOOPBACK_HOSTS or h.startswith("127.")


def _unauthorized(request: Request) -> JSONResponse:
    ids = ids_from_request(request)
    err = ApiError(
        401,
        "Missing or invalid authentication credentials",
        code="unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )
    return JSONResponse(
        status_code=401,
        content=error_body(
            err,
            request_id=ids.request_id,
            transaction_id=ids.transaction_id,
            session_id=ids.session_id,
            causation_id=ids.causation_id,
        ),
        headers=err.headers,
    )


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        token = getattr(request.app.state, "auth_token", None)
        if not token or not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth[:7].lower() == "bearer ":
            return _unauthorized(request)
        supplied = auth[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, token):
            return _unauthorized(request)
        request.state.principal = "management"
        return await call_next(request)
