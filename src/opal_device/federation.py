"""Federation: peer sync + inventory surfaces.

Inbound trusted-peer authz and the remote-cache sync engine land in
later steps; until then sync/discover report the dependency as
unavailable rather than pretending to succeed.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .errors import ApiError
from .store import Store

_UNAVAILABLE = ApiError(
    503,
    "Federation sync transport is not configured yet",
    code="peer_sync_unavailable",
    retryable=True,
)


def sync_peer(request: Request, store: Store, peer: dict) -> JSONResponse:
    raise _UNAVAILABLE


def sync_all_peers(request: Request, store: Store) -> JSONResponse:
    raise _UNAVAILABLE


def discover_peers(store: Store) -> JSONResponse:
    raise _UNAVAILABLE
