"""Device inventory: registration CRUD.

device_id path parameters accept a UUID or a host-qualified name
(`{host_name}/{name}`); qualified names contain `/` and arrive
percent-encoded, so item routes use the `path` converter (decoded value
matches the stored qualified_name). Sub-resource routes with qualified
names are handled by the raw-path dispatcher in this module.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from .dependencies import StoreDep
from .errors import ApiError
from .filters import match_device, parse_label_param
from .precondition import check_if_match, require_if_match
from .schemas import Access, Availability, DeviceCreate, DevicePatch, DeviceType
from .store import Store

router = APIRouter()

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


# -- repository -----------------------------------------------------------


def _qualified_name(host_name: str, name: str) -> str:
    return f"{host_name}/{name}"


def find_device(store: Store, device_id: str) -> dict | None:
    row = store.fetchone("SELECT doc FROM devices WHERE id = ?", (device_id,))
    if row is None:
        row = store.fetchone(
            "SELECT doc FROM devices WHERE qualified_name = ?", (device_id,)
        )
    return json.loads(row["doc"]) if row else None


def _insert(store: Store, doc: dict) -> None:
    store.execute(
        "INSERT INTO devices (id, qualified_name, name, host_id, host_name,"
        " type, backend, status, origin_peer_id, doc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc["id"],
            doc["qualified_name"],
            doc["name"],
            doc["host_id"],
            doc["host_name"],
            doc["type"],
            doc["backend"],
            doc["status"],
            doc.get("origin_peer_id"),
            json.dumps(doc),
        ),
    )


def _update(store: Store, doc: dict) -> None:
    store.execute(
        "UPDATE devices SET qualified_name = ?, name = ?, status = ?, doc = ?"
        " WHERE id = ?",
        (
            doc["qualified_name"],
            doc["name"],
            doc["status"],
            json.dumps(doc),
            doc["id"],
        ),
    )


def _delete(store: Store, device_id: str) -> None:
    store.execute("DELETE FROM devices WHERE id = ?", (device_id,))


def _update_or_409(store: Store, doc: dict) -> None:
    try:
        _update(store, doc)
    except sqlite3.IntegrityError:
        raise ApiError(
            409,
            f"Device qualified_name already exists: {doc['qualified_name']}",
            code="conflict",
        )


def all_devices(store: Store) -> list[dict]:
    rows = store.fetchall("SELECT doc FROM devices ORDER BY id")
    return [json.loads(r["doc"]) for r in rows]


def find_group(store: Store, key: str) -> dict | None:
    row = store.fetchone("SELECT doc FROM groups WHERE id = ?", (key,))
    if row is None:
        row = store.fetchone("SELECT doc FROM groups WHERE name = ?", (key,))
    return json.loads(row["doc"]) if row else None


def _refresh_inventory(store: Store) -> None:
    """Run enabled discovery plugins (populated by the plugin step)."""
    return None


def _encode_cursor(device_id: str) -> str:
    return base64.urlsafe_b64encode(f"dev:{device_id}".encode()).decode()


def _decode_cursor(cursor: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ApiError(422, "Invalid cursor", code="invalid_cursor")
    if not raw.startswith("dev:"):
        raise ApiError(422, "Invalid cursor", code="invalid_cursor")
    return raw[4:]


# -- handlers -------------------------------------------------------------


@router.post("/api/v1/device/", status_code=201)
def add_device(request: Request, store: StoreDep, body: DeviceCreate):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc = {
            "id": str(uuid.uuid4()),
            "name": body.name,
            "qualified_name": _qualified_name(body.host_name, body.name),
            "host_id": body.host_id,
            "host_name": body.host_name,
            "type": body.type,
            "backend": body.backend,
            "status": "unknown",
            "capabilities": {},
            "labels": body.labels or {},
            "tags": body.tags or [],
            "access": body.access,
        }
        if body.resource_uri is not None:
            doc["resource_uri"] = body.resource_uri
        if body.configuration is not None:
            doc["configuration"] = body.configuration
        if body.metadata is not None:
            doc["metadata"] = body.metadata
        try:
            _insert(store, doc)
        except sqlite3.IntegrityError:
            raise ApiError(
                409,
                f"Device qualified_name already exists: {doc['qualified_name']}",
                code="conflict",
            )
        store.bump_revision()
    return JSONResponse(
        status_code=201,
        content=doc,
        headers={"Location": f"/api/v1/device/{doc['id']}"},
    )


@router.get("/api/v1/device/")
def list_devices(
    store: StoreDep,
    request: Request,
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    type: DeviceType | None = None,
    status: Availability | None = None,
    host: str | None = None,
    backend: str | None = None,
    peer_id: str | None = None,
    tag: str | None = None,
    label: str | None = None,
    capability: str | None = None,
    min_memory_mb: int | None = Query(default=None, ge=0),
    group: str | None = None,
    include_stale: bool = True,
    refresh: bool = False,
):
    if refresh:
        _refresh_inventory(store)

    filt: dict[str, Any] = {}
    if type is not None:
        filt["type"] = type
    if status is not None:
        filt["status"] = status
    if host is not None:
        filt["host"] = host
    if backend is not None:
        filt["backend"] = backend
    if peer_id is not None:
        filt["peer_id"] = peer_id
    if tag is not None:
        filt["tags_all"] = [tag]
    if label is not None:
        k, v = parse_label_param(label)
        filt["labels"] = {k: v}
    cap_exprs: list[str] = []
    if capability is not None:
        cap_exprs.append(capability)
    if min_memory_mb is not None:
        cap_exprs.append(f"compute.memory_mb >= {min_memory_mb}")
    if cap_exprs:
        filt["capabilities"] = cap_exprs

    member_ids: set[str] | None = None
    if group is not None:
        grp = find_group(store, group)
        if grp is None:
            member_ids = set()
        elif grp.get("mode") == "dynamic":
            selector = grp.get("selector") or {}
            member_ids = {
                d["id"] for d in all_devices(store) if match_device(d, selector)
            }
        else:
            member_ids = set(grp.get("member_ids") or [])

    devices = all_devices(store)
    if not include_stale:
        devices = [d for d in devices if d.get("status") != "stale"]
    items = [
        d
        for d in devices
        if match_device(d, filt)
        and (member_ids is None or d["id"] in member_ids)
    ]
    total = len(items)

    if cursor is not None:
        after = _decode_cursor(cursor)
        items = [d for d in items if d["id"] > after]

    eff_limit = min(limit if limit is not None else _DEFAULT_LIMIT, _MAX_LIMIT)
    page = items[:eff_limit]
    next_cursor = (
        _encode_cursor(page[-1]["id"]) if len(items) > eff_limit else None
    )
    return JSONResponse(
        content={"items": page, "next_cursor": next_cursor, "total": total},
        headers={"ETag": store.etag()},
    )


@router.get("/api/v1/device/{device_id:path}")
def get_device(device_id: str, store: StoreDep):
    doc = find_device(store, device_id)
    if doc is None:
        raise ApiError(404, f"Device not found: {device_id}")
    return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.patch("/api/v1/device/{device_id:path}")
def update_device(
    device_id: str, request: Request, store: StoreDep, body: DevicePatch
):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc = find_device(store, device_id)
        if doc is None:
            raise ApiError(404, f"Device not found: {device_id}")
        if body.name is not None:
            doc["name"] = body.name
            doc["qualified_name"] = _qualified_name(doc["host_name"], body.name)
        for field in ("resource_uri", "configuration", "metadata"):
            value = getattr(body, field)
            if value is not None:
                doc[field] = value
        _update_or_409(store, doc)
        store.bump_revision()
        return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.delete("/api/v1/device/{device_id:path}")
def remove_device(device_id: str, request: Request, store: StoreDep):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc = find_device(store, device_id)
        if doc is None:
            raise ApiError(404, f"Device not found: {device_id}")
        _delete(store, doc["id"])
        store.bump_revision()
    return Response(status_code=204)
