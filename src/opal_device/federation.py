"""Federation: inbound trusted-peer inventory + registry identity.

Inbound peer auth happens in BearerAuthMiddleware: a peer credential
(OPAL_DEVICE_CRED_<REF>) authenticates as a peer principal that may
read only /api/v1/federation/*, and only when the peer is trusted —
anything else is 403. The remote-cache sync engine lands in Step 18.
"""

from __future__ import annotations

import platform
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .dependencies import StoreDep
from .devices import _decode_cursor, _encode_cursor, all_devices
from .errors import ApiError
from .peers import find_peer
from .sharing import visible_devices
from .store import Store

router = APIRouter()

_REGISTRY_ID_KEY = "registry_id"
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def registry_id(store: Store) -> str:
    row = store.fetchone(
        "SELECT value FROM meta WHERE key = ?", (_REGISTRY_ID_KEY,)
    )
    if row:
        return row["value"]
    rid = str(uuid.uuid4())
    store.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)", (_REGISTRY_ID_KEY, rid)
    )
    return rid


def _local_devices(store: Store) -> list[dict]:
    return [d for d in all_devices(store) if not d.get("origin_peer_id")]


# -- routes ---------------------------------------------------------------------


@router.get("/api/v1/federation/registry")
def federation_registry(store: StoreDep):
    return JSONResponse(
        content={
            "status": "ok",
            "version": __version__,
            "registry_id": registry_id(store),
        }
    )


@router.get("/api/v1/federation/inventory")
def federation_inventory(
    request: Request,
    store: StoreDep,
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1),
):
    principal = getattr(request.state, "principal", {"kind": "management"})
    devices = _local_devices(store)
    if principal.get("kind") == "peer":
        peer = find_peer(store, principal["peer_id"])
        if peer is None or not peer.get("trusted"):
            raise ApiError(
                403, "Authenticated but not permitted", code="forbidden"
            )
        devices = visible_devices(store, peer, devices)

    devices.sort(key=lambda d: d["id"])
    if cursor is not None:
        after = _decode_cursor(cursor)
        devices = [d for d in devices if d["id"] > after]
    eff = min(limit if limit is not None else _DEFAULT_LIMIT, _MAX_LIMIT)
    page = devices[:eff]
    next_cursor = (
        _encode_cursor(page[-1]["id"]) if len(devices) > eff else None
    )
    return JSONResponse(
        content={
            "registry_id": registry_id(store),
            "host": platform.node() or "localhost",
            "devices": page,
            "snapshot_at": _now(),
            "next_cursor": next_cursor,
        }
    )


# -- sync engine (Step 18 fills in real transport) ------------------------------


def sync_peer(request: Request, store: Store, peer: dict) -> JSONResponse:
    raise ApiError(
        503,
        "Federation sync transport is not configured yet",
        code="peer_sync_unavailable",
        retryable=True,
    )


def sync_all_peers(request: Request, store: Store) -> JSONResponse:
    raise ApiError(
        503,
        "Federation sync transport is not configured yet",
        code="peer_sync_unavailable",
        retryable=True,
    )


def discover_peers(store: Store) -> JSONResponse:
    raise ApiError(
        503,
        "Federation sync transport is not configured yet",
        code="peer_sync_unavailable",
        retryable=True,
    )
