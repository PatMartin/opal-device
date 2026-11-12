"""Device inventory: registration CRUD.

device_id path parameters accept a UUID or a host-qualified name
(`{host_name}/{name}`); qualified names contain `/` and arrive
percent-encoded, so item routes use the `path` converter (decoded value
matches the stored qualified_name). Sub-resource routes with qualified
names are handled by the raw-path dispatcher in this module.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .dependencies import StoreDep
from .errors import ApiError
from .precondition import check_if_match, require_if_match
from .schemas import DeviceCreate, DevicePatch
from .store import Store

router = APIRouter()


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
