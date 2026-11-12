"""Federation peer registry: CRUD + status.

Peers are never trusted implicitly — `trusted` comes only from an
explicit create/patch; `credential_ref` names a secret-store entry
(OPAL_DEVICE_CRED_<REF> env) and is stored but never echoed in
responses. Sync/discover land with the federation steps; the routes are
registered here first so `/peer/sync` and `/peer/discover` aren't
shadowed by `/peer/{peer_id}`.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .dependencies import StoreDep
from .errors import ApiError
from .events import emit
from .precondition import check_if_match, require_if_match
from .schemas import PeerCreate, PeerPatch
from .store import Store

router = APIRouter()


# -- repository ---------------------------------------------------------------


def find_peer(store: Store, key: str) -> dict | None:
    row = store.fetchone("SELECT doc FROM peers WHERE id = ?", (key,))
    if row is None:
        row = store.fetchone("SELECT doc FROM peers WHERE name = ?", (key,))
    return json.loads(row["doc"]) if row else None


def all_peers(store: Store) -> list[dict]:
    rows = store.fetchall("SELECT doc FROM peers ORDER BY id")
    return [json.loads(r["doc"]) for r in rows]


def _insert(store: Store, doc: dict) -> None:
    store.execute(
        "INSERT INTO peers (id, name, base_url, doc) VALUES (?, ?, ?, ?)",
        (doc["id"], doc["name"], doc["base_url"], json.dumps(doc)),
    )


def _update(store: Store, doc: dict) -> None:
    store.execute(
        "UPDATE peers SET name = ?, base_url = ?, doc = ? WHERE id = ?",
        (doc["name"], doc["base_url"], json.dumps(doc), doc["id"]),
    )


def _view(doc: dict) -> dict:
    """Contract Peer shape; credential_ref never leaves the process."""
    out = {
        "id": doc["id"],
        "name": doc["name"],
        "base_url": doc["base_url"],
        "trusted": bool(doc.get("trusted")),
        "status": doc.get("status", "unknown"),
        "auto_discovered": bool(doc.get("auto_discovered")),
    }
    for f in ("last_synced", "tls_verified"):
        if doc.get(f) is not None:
            out[f] = doc[f]
    return out


def _get_peer_or_404(store: Store, peer_id: str) -> dict:
    doc = find_peer(store, peer_id)
    if doc is None:
        raise ApiError(404, f"Peer not found: {peer_id}")
    return doc


# -- routes ---------------------------------------------------------------------


@router.post("/api/v1/peer/sync", status_code=202)
def peers_sync(request: Request, store: StoreDep):
    from .federation import sync_all_peers

    return sync_all_peers(request, store)


@router.post("/api/v1/peer/discover")
def peers_discover(store: StoreDep):
    from .settings import merged_settings

    enabled = merged_settings(store).get("federation.auto_discovery_enabled")
    if enabled is not True:
        raise ApiError(
            503,
            "Peer auto-discovery is disabled"
            " (federation.auto_discovery_enabled=false)",
            code="federation_discovery_disabled",
            retryable=False,
        )
    from .federation import discover_peers

    return discover_peers(store)


@router.get("/api/v1/peer/")
def list_peers(store: StoreDep):
    return JSONResponse(
        content={"items": [_view(p) for p in all_peers(store)]},
        headers={"ETag": store.etag()},
    )


@router.post("/api/v1/peer/", status_code=201)
def add_peer(request: Request, store: StoreDep, body: PeerCreate):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "name": body.name,
            "base_url": body.base_url,
            "trusted": body.trusted,
            "status": "unknown",
            "auto_discovered": False,
        }
        if body.credential_ref is not None:
            doc["credential_ref"] = body.credential_ref
        try:
            _insert(store, doc)
        except sqlite3.IntegrityError:
            raise ApiError(
                409,
                f"Peer base_url already registered: {body.base_url}",
                code="conflict",
            )
        emit(store, "peer.registered", _view(doc), peer_id=doc["id"])
        store.bump_revision()
    return JSONResponse(
        status_code=201,
        content=_view(doc),
        headers={"Location": f"/api/v1/peer/{doc['id']}"},
    )


@router.get("/api/v1/peer/{peer_id}")
def get_peer(peer_id: str, store: StoreDep):
    doc = _get_peer_or_404(store, peer_id)
    return JSONResponse(content=_view(doc), headers={"ETag": store.etag()})


@router.patch("/api/v1/peer/{peer_id}")
def patch_peer(
    peer_id: str, request: Request, store: StoreDep, body: PeerPatch
):
    doc = _get_peer_or_404(store, peer_id)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        for field in ("name", "base_url", "credential_ref", "trusted"):
            value = getattr(body, field)
            if value is not None:
                doc[field] = value
        try:
            _update(store, doc)
        except sqlite3.IntegrityError:
            raise ApiError(
                409,
                f"Peer base_url already registered: {doc['base_url']}",
                code="conflict",
            )
        emit(store, "peer.updated", _view(doc), peer_id=doc["id"])
        store.bump_revision()
        return JSONResponse(
            content=_view(doc), headers={"ETag": store.etag()}
        )


@router.delete("/api/v1/peer/{peer_id}", status_code=204)
def delete_peer(peer_id: str, request: Request, store: StoreDep):
    doc = _get_peer_or_404(store, peer_id)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        store.execute("DELETE FROM peers WHERE id = ?", (doc["id"],))
        # remote-cache rows for this peer go too
        store.execute(
            "DELETE FROM devices WHERE origin_peer_id = ?", (doc["id"],)
        )
        emit(
            store, "peer.removed",
            {"name": doc["name"], "base_url": doc["base_url"]},
            peer_id=doc["id"],
        )
        store.bump_revision()
    return Response(status_code=204)


@router.get("/api/v1/peer/{peer_id}/status")
def peer_status(peer_id: str, store: StoreDep):
    doc = _get_peer_or_404(store, peer_id)
    return JSONResponse(content=_view(doc))


@router.post("/api/v1/peer/{peer_id}/sync")
def peer_sync(peer_id: str, request: Request, store: StoreDep):
    doc = _get_peer_or_404(store, peer_id)
    from .federation import sync_peer

    return sync_peer(request, store, doc)
