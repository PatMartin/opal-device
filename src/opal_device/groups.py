"""Device groups: static membership and dynamic selector collections."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from .dependencies import StoreDep
from .devices import all_devices, find_device, find_group
from .errors import ApiError
from .filters import match_device
from .precondition import check_if_match, require_if_match
from .schemas import Availability, DeviceType
from .store import Store

router = APIRouter()


class DeviceFilterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: DeviceType | None = None
    status: Availability | None = None
    host: str | None = None
    backend: str | None = None
    peer_id: str | None = None
    tags_all: list[str] | None = None
    labels: dict[str, str] | None = None
    capabilities: list[str] | None = None


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    mode: str
    selector: DeviceFilterModel | None = None
    description: str | None = None


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    selector: DeviceFilterModel | None = None


# -- helpers ---------------------------------------------------------------


def _selector_dict(model: DeviceFilterModel | None) -> dict | None:
    if model is None:
        return None
    return model.model_dump(exclude_none=True)


def _member_ids(store: Store, grp: dict) -> list[str]:
    if grp.get("mode") == "dynamic":
        selector = grp.get("selector") or {}
        return [
            d["id"] for d in all_devices(store) if match_device(d, selector)
        ]
    return list(grp.get("member_ids") or [])


def _group_view(store: Store, grp: dict) -> dict:
    view = {
        "id": grp["id"],
        "name": grp["name"],
        "mode": grp["mode"],
        "member_ids": _member_ids(store, grp),
    }
    if grp.get("selector") is not None:
        view["selector"] = grp["selector"]
    if grp.get("description") is not None:
        view["description"] = grp["description"]
    return view


def _insert_group(store: Store, doc: dict) -> None:
    store.execute(
        "INSERT INTO groups (id, name, mode, doc) VALUES (?, ?, ?, ?)",
        (doc["id"], doc["name"], doc["mode"], json.dumps(doc)),
    )


def _update_group(store: Store, doc: dict) -> None:
    store.execute(
        "UPDATE groups SET name = ?, mode = ?, doc = ? WHERE id = ?",
        (doc["name"], doc["mode"], json.dumps(doc), doc["id"]),
    )


def _get_or_404(store: Store, group_id: str) -> dict:
    grp = find_group(store, group_id)
    if grp is None:
        raise ApiError(404, f"Group not found: {group_id}")
    return grp


def _member_devices(store: Store, grp: dict) -> list[dict]:
    ids = set(_member_ids(store, grp))
    return [d for d in all_devices(store) if d["id"] in ids]


# -- collection -------------------------------------------------------------


@router.get("/api/v1/group/")
def list_groups(store: StoreDep):
    rows = store.fetchall("SELECT doc FROM groups ORDER BY name")
    items = [_group_view(store, json.loads(r["doc"])) for r in rows]
    return JSONResponse(content={"items": items})


@router.post("/api/v1/group/", status_code=201)
def add_group(request: Request, store: StoreDep, body: GroupCreate):
    if body.mode not in ("static", "dynamic"):
        raise ApiError(
            422, "mode must be 'static' or 'dynamic'", code="validation_error"
        )
    selector = _selector_dict(body.selector)
    if body.mode == "dynamic" and not selector:
        raise ApiError(
            422,
            "selector is required for dynamic groups",
            code="validation_error",
        )
    if body.mode == "static" and selector is not None:
        raise ApiError(
            422,
            "selector is forbidden for static groups",
            code="validation_error",
        )
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if find_group(store, body.name) is not None:
            raise ApiError(
                409, f"Group name already exists: {body.name}", code="conflict"
            )
        doc = {
            "id": str(uuid.uuid4()),
            "name": body.name,
            "mode": body.mode,
            "member_ids": [],
        }
        if selector is not None:
            doc["selector"] = selector
        if body.description is not None:
            doc["description"] = body.description
        _insert_group(store, doc)
        store.bump_revision()
        return JSONResponse(
            status_code=201,
            content=_group_view(store, doc),
            headers={"Location": f"/api/v1/group/{doc['id']}"},
        )


# -- item -------------------------------------------------------------------


@router.get("/api/v1/group/{group_id}")
def get_group(group_id: str, store: StoreDep):
    grp = _get_or_404(store, group_id)
    return JSONResponse(
        content=_group_view(store, grp), headers={"ETag": store.etag()}
    )


@router.patch("/api/v1/group/{group_id}")
def update_group(
    group_id: str, request: Request, store: StoreDep, body: GroupPatch
):
    grp = _get_or_404(store, group_id)
    selector = _selector_dict(body.selector)
    if selector is not None and grp["mode"] == "static":
        raise ApiError(
            422,
            "selector is forbidden for static groups",
            code="validation_error",
        )
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if body.description is not None:
            grp["description"] = body.description
        if selector is not None:
            grp["selector"] = selector
        _update_group(store, grp)
        store.bump_revision()
        return JSONResponse(
            content=_group_view(store, grp), headers={"ETag": store.etag()}
        )


@router.delete("/api/v1/group/{group_id}")
def remove_group(group_id: str, request: Request, store: StoreDep):
    grp = _get_or_404(store, group_id)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        store.execute("DELETE FROM groups WHERE id = ?", (grp["id"],))
        store.bump_revision()
    return Response(status_code=204)


# -- members -----------------------------------------------------------------


@router.get("/api/v1/group/{group_id}/member")
def list_members(group_id: str, store: StoreDep, refresh: bool = False):
    grp = _get_or_404(store, group_id)
    items = _member_devices(store, grp)
    return JSONResponse(
        content={"items": items, "next_cursor": None, "total": len(items)}
    )


@router.put("/api/v1/group/{group_id}/member/{device_id:path}")
def add_member(group_id: str, device_id: str, request: Request, store: StoreDep):
    grp = _get_or_404(store, group_id)
    if grp["mode"] == "dynamic":
        raise ApiError(
            422,
            "members cannot be added to a dynamic group",
            code="validation_error",
        )
    dev = find_device(store, device_id)
    if dev is None:
        raise ApiError(404, f"Device not found: {device_id}")
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        members = grp.setdefault("member_ids", [])
        if dev["id"] in members:
            raise ApiError(
                409, "Device is already a group member", code="conflict"
            )
        members.append(dev["id"])
        _update_group(store, grp)
        store.bump_revision()
        return JSONResponse(
            content=_group_view(store, grp), headers={"ETag": store.etag()}
        )


@router.delete("/api/v1/group/{group_id}/member/{device_id:path}")
def remove_member(
    group_id: str, device_id: str, request: Request, store: StoreDep
):
    grp = _get_or_404(store, group_id)
    if grp["mode"] == "dynamic":
        raise ApiError(
            422,
            "members cannot be removed from a dynamic group",
            code="validation_error",
        )
    dev = find_device(store, device_id)
    if dev is None:
        raise ApiError(404, f"Device not found: {device_id}")
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        members = grp.setdefault("member_ids", [])
        if dev["id"] not in members:
            raise ApiError(404, "Device is not a group member")
        members.remove(dev["id"])
        _update_group(store, grp)
        store.bump_revision()
    return Response(status_code=204)
