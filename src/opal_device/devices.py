"""Device inventory: registration CRUD, list, and sub-resources.

device_id path parameters accept a UUID or a host-qualified name
(`{host_name}/{name}`). Qualified names contain `/` and arrive
percent-encoded; ASGI decodes `%2F` to a literal slash before routing,
so all `/api/v1/device/*` traffic is dispatched here on the raw path:
first the whole remainder resolves as a device id/qualified name (the
item ops), and only when that fails is a trailing sub-resource suffix
(capabilities/status/test/label/tag) split off and re-resolved. A device
whose qualified name ends in a reserved suffix still resolves correctly
as an item.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Body, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from fastapi.responses import JSONResponse, Response

from .dependencies import StoreDep
from .errors import ApiError
from .events import emit
from .filters import match_device, parse_label_param
from .precondition import check_if_match, require_if_match
from .schemas import (
    Access,
    Availability,
    DeviceCreate,
    DevicePatch,
    DeviceType,
    LabelSet,
    TagSet,
)
from .store import Store

router = APIRouter()

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

_SINGLE_SUFFIXES = {"capabilities", "status", "test", "label", "tag"}
_TWO_PART_SUFFIXES = {"label", "tag"}
_DEVICE_PREFIX = "/api/v1/device/"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def all_devices(store: Store) -> list[dict]:
    rows = store.fetchall("SELECT doc FROM devices ORDER BY id")
    return [json.loads(r["doc"]) for r in rows]


def find_group(store: Store, key: str) -> dict | None:
    row = store.fetchone("SELECT doc FROM groups WHERE id = ?", (key,))
    if row is None:
        row = store.fetchone("SELECT doc FROM groups WHERE name = ?", (key,))
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


# -- path dispatch ----------------------------------------------------------


def _candidates(request: Request) -> list[tuple[str, str | None]]:
    """(device_id, sub-resource) candidates, best interpretation first."""
    raw = request.scope["raw_path"].decode("utf-8", "replace")
    rest = raw[len(_DEVICE_PREFIX):] if raw.startswith(_DEVICE_PREFIX) else ""
    rest = rest.rstrip("/")
    cands: list[tuple[str, str | None]] = [(unquote(rest), None)]
    head, sep, tail = rest.rpartition("/")
    if sep and tail in _SINGLE_SUFFIXES:
        cands.append((unquote(head), tail))
    if sep:
        head2, sep2, tail2 = head.rpartition("/")
        if sep2 and tail2 in _TWO_PART_SUFFIXES:
            cands.append((unquote(head2), f"{tail2}/{tail}"))
    return cands


def _resolve(request: Request, store: Store) -> tuple[dict | None, str | None]:
    """First candidate whose device exists; (None, None) when none do."""
    for device_id, sub in _candidates(request):
        doc = find_device(store, device_id)
        if doc is not None:
            return doc, sub
    return None, None


def _not_found(request: Request) -> ApiError:
    last = _candidates(request)[-1][0]
    return ApiError(404, f"Device not found: {last}")


def _method_not_allowed(sub: str | None) -> ApiError:
    target = f"device sub-resource {sub!r}" if sub else "device item"
    return ApiError(405, f"Method not allowed on {target}")


# -- collection -------------------------------------------------------------


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
                f"Device qualified_name already exists: "
                f"{doc['qualified_name']}",
                code="conflict",
            )
        emit(store, "device.registered", doc, device_id=doc["id"])
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


def _refresh_inventory(store: Store) -> None:
    from .discovery import refresh_inventory

    refresh_inventory(store)


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


# -- item + sub-resource views ----------------------------------------------


def _status_body(doc: dict) -> dict:
    body = {
        "device_id": doc["id"],
        "status": doc["status"],
        "origin": "federated" if doc.get("origin_peer_id") else "local",
    }
    for field in ("status_reason", "last_seen", "last_checked"):
        if doc.get(field) is not None:
            body[field] = doc[field]
    return body


def _device_test(store: Store, doc: dict) -> dict:
    """Non-invasive metadata/status checks; runtime update, no bump."""
    checks = []
    required = (
        "id", "name", "qualified_name", "host_id", "host_name", "type",
        "backend", "access",
    )
    missing = [f for f in required if not doc.get(f)]
    checks.append(
        {
            "name": "metadata",
            "ok": not missing,
            "message": (
                "all required fields present"
                if not missing
                else f"missing: {', '.join(missing)}"
            ),
        }
    )
    uri = doc.get("resource_uri")
    if uri is not None:
        ok = bool(urlparse(uri).scheme)
        checks.append(
            {
                "name": "resource_uri",
                "ok": ok,
                "message": uri if ok else "not an absolute URI",
            }
        )
    checks.append(
        {
            "name": "backend",
            "ok": bool(doc.get("backend")),
            "message": doc.get("backend") or "no backend declared",
        }
    )
    status = "available" if all(c["ok"] for c in checks) else "unavailable"
    checked_at = _now()
    if doc.get("status") != status:
        emit(
            store, "device.status_changed",
            {"status": status}, device_id=doc["id"],
        )
    doc["status"] = status
    doc["last_checked"] = checked_at
    _update(store, doc)
    return {
        "device_id": doc["id"],
        "status": status,
        "checks": checks,
        "checked_at": checked_at,
    }


# -- dispatched routes --------------------------------------------------------


@router.get("/api/v1/device/{rest:path}")
def device_get(rest: str, request: Request, store: StoreDep):
    doc, sub = _resolve(request, store)
    if doc is None:
        raise _not_found(request)
    if sub is None:
        return JSONResponse(content=doc, headers={"ETag": store.etag()})
    if sub == "capabilities":
        return JSONResponse(content=doc.get("capabilities") or {})
    if sub == "status":
        return JSONResponse(content=_status_body(doc))
    if sub == "label":
        return JSONResponse(content=doc.get("labels") or {})
    if sub == "tag":
        return JSONResponse(content={"items": doc.get("tags") or []})
    raise _method_not_allowed(sub)


@router.post("/api/v1/device/{rest:path}")
def device_post(
    rest: str, request: Request, store: StoreDep, body: dict | None = None
):
    doc, sub = _resolve(request, store)
    if doc is None:
        raise _not_found(request)
    if sub == "test":
        return JSONResponse(content=_device_test(store, doc))
    if sub == "tag":
        payload = _validate_body(TagSet, body)
        return _tag_add(request, store, doc, payload.tag)
    raise _method_not_allowed(sub)


def _validate_body(model, body: Any):
    try:
        return model.model_validate(body or {})
    except ValidationError as err:
        raise ApiError(
            422,
            "Request validation failed",
            code="validation_error",
            details=jsonable_encoder(err.errors()),
        )


def _tag_add(request: Request, store: Store, doc: dict, tag: str):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if tag in (doc.get("tags") or []):
            raise ApiError(409, f"Tag already present: {tag}", code="conflict")
        doc.setdefault("tags", []).append(tag)
        _update(store, doc)
        emit(store, "device.updated", doc, device_id=doc["id"])
        store.bump_revision()
        return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.patch("/api/v1/device/{rest:path}")
def device_patch(rest: str, request: Request, store: StoreDep, body: DevicePatch):
    doc, sub = _resolve(request, store)
    if doc is None:
        raise _not_found(request)
    if sub is not None:
        raise _method_not_allowed(sub)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if body.name is not None:
            doc["name"] = body.name
            doc["qualified_name"] = _qualified_name(
                doc["host_name"], body.name
            )
        for field in ("resource_uri", "configuration", "metadata"):
            value = getattr(body, field)
            if value is not None:
                doc[field] = value
        _update_or_409(store, doc)
        emit(store, "device.updated", doc, device_id=doc["id"])
        store.bump_revision()
        return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.put("/api/v1/device/{rest:path}")
def device_put(
    rest: str,
    request: Request,
    store: StoreDep,
    body: dict | None = Body(default=None),
):
    doc, sub = _resolve(request, store)
    if doc is None:
        raise _not_found(request)
    if sub is not None and sub.startswith("label/"):
        key = unquote(sub.split("/", 1)[1])
        payload = _validate_body(LabelSet, body)
        return _label_set(request, store, doc, key, payload.value)
    raise _method_not_allowed(sub)


def _label_set(
    request: Request, store: Store, doc: dict, key: str, value: str
):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc.setdefault("labels", {})[key] = value
        _update(store, doc)
        emit(store, "device.updated", doc, device_id=doc["id"])
        store.bump_revision()
        return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.delete("/api/v1/device/{rest:path}")
def device_delete(rest: str, request: Request, store: StoreDep):
    doc, sub = _resolve(request, store)
    if doc is None:
        raise _not_found(request)
    if sub is not None and sub.startswith("label/"):
        key = unquote(sub.split("/", 1)[1])
        return _label_delete(request, store, doc, key)
    if sub is not None and sub.startswith("tag/"):
        tag = unquote(sub.split("/", 1)[1])
        return _tag_delete(request, store, doc, tag)
    if sub is not None:
        raise _method_not_allowed(sub)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        _delete(store, doc["id"])
        emit(
            store, "device.removed",
            {"qualified_name": doc["qualified_name"]},
            device_id=doc["id"],
        )
        store.bump_revision()
    return Response(status_code=204)


def _label_delete(request: Request, store: Store, doc: dict, key: str):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if key not in (doc.get("labels") or {}):
            raise ApiError(404, f"Label not found: {key}")
        del doc["labels"][key]
        _update(store, doc)
        emit(store, "device.updated", doc, device_id=doc["id"])
        store.bump_revision()
    return Response(status_code=204)


def _tag_delete(request: Request, store: Store, doc: dict, tag: str):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if tag not in (doc.get("tags") or []):
            raise ApiError(404, f"Tag not found: {tag}")
        doc["tags"].remove(tag)
        _update(store, doc)
        emit(store, "device.updated", doc, device_id=doc["id"])
        store.bump_revision()
    return Response(status_code=204)
