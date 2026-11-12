"""OpalError envelope and API exception type.

All 4xx/5xx JSON bodies use the kernel OpalError envelope:
{"error": {code, message, request_id, transaction_id, ...}}.
"""

from __future__ import annotations

from typing import Any

_DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    412: "precondition_failed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    428: "precondition_required",
    429: "too_many_requests",
    500: "internal_error",
    503: "service_unavailable",
}


class ApiError(Exception):
    """Contract-shaped API error; handlers render it as OpalError."""

    def __init__(
        self,
        status: int,
        message: str,
        code: str | None = None,
        *,
        details: Any = None,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code or _DEFAULT_CODES.get(status, "error")
        self.details = details
        self.retryable = retryable
        self.headers = headers or {}


def error_body(
    err: ApiError,
    *,
    request_id: str,
    transaction_id: str,
    session_id: str | None = None,
    causation_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": err.code,
        "message": err.message,
        "request_id": request_id,
        "transaction_id": transaction_id,
        "retryable": err.retryable,
    }
    if session_id is not None:
        body["session_id"] = session_id
    if causation_id is not None:
        body["causation_id"] = causation_id
    if err.details is not None:
        body["details"] = err.details
    return {"error": body}
