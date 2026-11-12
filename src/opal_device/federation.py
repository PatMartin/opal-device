"""Federation: inbound trusted-peer inventory + registry identity.

Inbound peer auth happens in BearerAuthMiddleware: a peer credential
(OPAL_DEVICE_CRED_<REF>) authenticates as a peer principal that may
read only /api/v1/federation/*, and only when the peer is trusted —
anything else is 403. The remote-cache sync engine lands in Step 18.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import httpx

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .dependencies import StoreDep
from .devices import (
    _decode_cursor,
    _encode_cursor,
    all_devices,
    find_device,
)
from .errors import ApiError
from .events import emit
from .peers import all_peers, find_peer
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


# -- sync engine ----------------------------------------------------------------


def _peer_token(peer: dict) -> str | None:
    ref = peer.get("credential_ref")
    return os.environ.get(f"OPAL_DEVICE_CRED_{ref.upper()}") if ref else None


def _client(request: Request, peer: dict) -> httpx.Client:
    factory = getattr(request.app.state, "http_client_factory", None)
    if factory is not None:
        client = factory(peer)
    else:
        client = httpx.Client(base_url=peer["base_url"], timeout=10.0)
    token = _peer_token(peer)
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return client


def _fetch_remote_inventory(client: httpx.Client) -> list[dict]:
    devices: list[dict] = []
    cursor: str | None = None
    while True:
        params = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        resp = client.get("/api/v1/federation/inventory", params=params)
        resp.raise_for_status()
        body = resp.json()
        devices.extend(body["devices"])
        cursor = body.get("next_cursor")
        if not cursor:
            return devices


def _upsert_remote(store: Store, peer_id: str, remote: dict) -> str:
    """Upsert a remote device doc into the cache; returns add|update|same."""
    existing = find_device(store, remote["id"])
    if existing is None:
        existing = find_device(store, remote.get("qualified_name", ""))
    doc = dict(remote)
    doc["origin_peer_id"] = peer_id
    if existing is None:
        store.execute(
            "INSERT INTO devices (id, qualified_name, name, host_id,"
            " host_name, type, backend, status, origin_peer_id, doc)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                doc["id"], doc["qualified_name"], doc["name"],
                doc.get("host_id", ""), doc.get("host_name", ""),
                doc["type"], doc["backend"], doc.get("status", "unknown"),
                peer_id, json.dumps(doc),
            ),
        )
        return "add"
    changed = existing != doc
    doc["id"] = existing["id"]
    store.execute(
        "UPDATE devices SET doc = ?, status = ?, origin_peer_id = ?"
        " WHERE id = ?",
        (json.dumps(doc), doc.get("status", "unknown"), peer_id,
         existing["id"]),
    )
    return "update" if changed else "same"


def _sync_peer_impl(
    request: Request, store: Store, peer: dict
) -> dict:
    with _client(request, peer) as client:
        remote = _fetch_remote_inventory(client)
    added = updated = 0
    seen: set[str] = set()
    with store.write_txn():
        for dev in remote:
            outcome = _upsert_remote(store, peer["id"], dev)
            seen.add(dev["id"])
            if outcome == "add":
                added += 1
            elif outcome == "update":
                updated += 1
        stale = 0
        for cached in store.fetchall(
            "SELECT id, doc FROM devices WHERE origin_peer_id = ?",
            (peer["id"],),
        ):
            doc = json.loads(cached["doc"])
            if doc["id"] not in seen and doc.get("status") != "stale":
                doc["status"] = "stale"
                store.execute(
                    "UPDATE devices SET doc = ?, status = 'stale'"
                    " WHERE id = ?",
                    (json.dumps(doc), doc["id"]),
                )
                stale += 1
        synced_at = _now()
        peer["last_synced"] = synced_at
        peer["status"] = "available"
        store.execute(
            "UPDATE peers SET doc = ? WHERE id = ?",
            (json.dumps(peer), peer["id"]),
        )
        emit(
            store, "peer.synced",
            {"peer_id": peer["id"], "added": added,
             "updated": updated, "stale": stale},
            peer_id=peer["id"],
        )
        store.bump_revision()
    return {
        "peer_id": peer["id"],
        "synced_at": synced_at,
        "added": added,
        "updated": updated,
        "stale": stale,
        "errors": [],
    }


def sync_peer(request: Request, store: Store, peer: dict) -> JSONResponse:
    if not peer.get("trusted"):
        raise ApiError(
            422, "Peer is not trusted; sync refused", code="peer_untrusted"
        )
    try:
        result = _sync_peer_impl(request, store, peer)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        with store.write_txn():
            peer["status"] = "unavailable"
            store.execute(
                "UPDATE peers SET doc = ? WHERE id = ?",
                (json.dumps(peer), peer["id"]),
            )
        raise ApiError(
            503,
            f"Peer unreachable: {peer['base_url']} ({exc})",
            code="peer_unreachable",
            retryable=True,
        )
    return JSONResponse(content=result)


def sync_all_peers(request: Request, store: Store) -> JSONResponse:
    peers = [p for p in all_peers(store) if p.get("trusted")]
    if not peers:
        raise ApiError(
            422, "No trusted peers registered", code="no_trusted_peers"
        )

    def worker() -> None:
        for peer in peers:
            try:
                _sync_peer_impl(request, store, peer)
            except (httpx.HTTPError, ValueError, KeyError):
                with store.write_txn():
                    peer["status"] = "unavailable"
                    store.execute(
                        "UPDATE peers SET doc = ? WHERE id = ?",
                        (json.dumps(peer), peer["id"]),
                    )

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "message": f"Synchronization admitted for {len(peers)} peer(s)",
        },
    )


def discover_peers(store: Store) -> JSONResponse:
    # mDNS discovery is not provisioned in this deployment; report an
    # empty candidate set rather than fabricating peers.
    return JSONResponse(content={"items": []})
