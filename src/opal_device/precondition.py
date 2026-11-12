"""Strong-ETag / If-Match preconditions for mutable configuration.

Every contract mutation requires If-Match on the global config
revision: `require_if_match(request)` runs early (428 missing /
422 malformed — weak tags, lists and `*` rejected) and
`check_if_match(request, store)` runs inside `store.write_txn()`
(412 stale), so the check and the write are atomic. All config writes
bump the revision; runtime activity must not.
"""

from __future__ import annotations

import re

from fastapi import Request

from .errors import ApiError
from .store import Store

_ETAG_RE = re.compile(r'^"[^"]+"$')


def require_if_match(request: Request) -> str:
    """Validate the If-Match header shape; returns the raw tag."""
    header = request.headers.get("if-match")
    if header is None:
        raise ApiError(
            428,
            "If-Match header is required for configuration mutations",
            code="precondition_required",
        )
    header = header.strip()
    if header == "*" or not _ETAG_RE.match(header):
        raise ApiError(
            422,
            "Malformed If-Match: one exact strong quoted tag required "
            "(weak tags, lists and '*' are rejected)",
            code="invalid_if_match",
        )
    request.state.if_match_tag = header
    return header


def check_if_match(request: Request, store: Store) -> None:
    """Compare the validated tag against the current revision."""
    tag = getattr(request.state, "if_match_tag", None)
    if tag is None or tag != store.etag():
        raise ApiError(
            412,
            "If-Match precondition failed: the supplied ETag is stale",
            code="precondition_failed",
        )
