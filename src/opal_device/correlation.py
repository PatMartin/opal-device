"""X-Opal-* correlation header handling (SUITE STANDARD v1.2).

Four optional inbound headers; each must be an opaque 1..128 char
printable-ASCII token with no whitespace (invalid -> 422). The
transaction id is adopted when supplied, else generated. The request id
identifies this inbound exchange and is regenerated when absent.
Session id is echoed when known; causation id when supplied.
Correlation ids are transport context, never authorization.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .errors import ApiError, error_body

SESSION_HEADER = "x-opal-session-id"
TRANSACTION_HEADER = "x-opal-transaction-id"
REQUEST_HEADER = "x-opal-request-id"
CAUSATION_HEADER = "x-opal-causation-id"

_HEADER_NAMES = {
    "session_id": SESSION_HEADER,
    "transaction_id": TRANSACTION_HEADER,
    "request_id": REQUEST_HEADER,
    "causation_id": CAUSATION_HEADER,
}

_TOKEN_RE = re.compile(r"^[!-~]{1,128}$")


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class CorrelationIds:
    session_id: str | None
    transaction_id: str
    request_id: str
    causation_id: str | None

    def response_headers(self) -> dict[str, str]:
        headers = {
            TRANSACTION_HEADER: self.transaction_id,
            REQUEST_HEADER: self.request_id,
        }
        if self.session_id is not None:
            headers[SESSION_HEADER] = self.session_id
        if self.causation_id is not None:
            headers[CAUSATION_HEADER] = self.causation_id
        return headers


def ids_from_request(request: Request) -> CorrelationIds:
    ids = getattr(request.state, "opal_ids", None)
    if ids is None:
        ids = CorrelationIds(
            session_id=None,
            transaction_id=_new_id(),
            request_id=_new_id(),
            causation_id=None,
        )
    return ids


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        supplied: dict[str, str | None] = {}
        for field, header in _HEADER_NAMES.items():
            value = request.headers.get(header)
            if value is not None and not _TOKEN_RE.match(value):
                ids = CorrelationIds(
                    session_id=None,
                    transaction_id=_new_id(),
                    request_id=_new_id(),
                    causation_id=None,
                )
                err = ApiError(
                    422,
                    f"Invalid {header} header: must be 1..128 printable ASCII characters",
                    code="invalid_correlation_header",
                    details={"header": header},
                )
                return JSONResponse(
                    status_code=422,
                    content=error_body(
                        err,
                        request_id=ids.request_id,
                        transaction_id=ids.transaction_id,
                    ),
                    headers=ids.response_headers(),
                )
            supplied[field] = value

        ids = CorrelationIds(
            session_id=supplied["session_id"],
            transaction_id=supplied["transaction_id"] or _new_id(),
            request_id=supplied["request_id"] or _new_id(),
            causation_id=supplied["causation_id"],
        )
        request.state.opal_ids = ids

        try:
            response = await call_next(request)
        except ApiError as err:
            response = _api_error_response(err, ids)
        except Exception:
            response = _api_error_response(
                ApiError(500, "Internal server error", retryable=True), ids
            )
        for name, value in ids.response_headers().items():
            response.headers[name] = value
        return response


def _api_error_response(err: ApiError, ids: CorrelationIds) -> JSONResponse:
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
