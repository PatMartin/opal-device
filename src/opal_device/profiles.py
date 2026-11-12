"""Profiles: named settings-map presets.

A profile's service-defined `settings` object holds a settings-map
preset; exactly one profile may be active at a time. Activation (via
PATCH is_active=true, or POST .../activate) applies the preset to the
settings surface atomically within the same write transaction.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from .dependencies import StoreDep
from .errors import ApiError
from .precondition import check_if_match, require_if_match
from .settings import _set
from .store import Store

router = APIRouter()

RESERVED_SETTINGS = {"pending_restart_fields"}


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str | None = None
    is_active: bool = False
    settings: dict[str, Any] | None = None


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    settings: dict[str, Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find(store: Store, key: str) -> dict | None:
    row = store.fetchone("SELECT doc FROM profiles WHERE id = ?", (key,))
    if row is None:
        row = store.fetchone(
            "SELECT doc FROM profiles WHERE name = ?", (key,)
        )
    return json.loads(row["doc"]) if row else None


def _all(store: Store) -> list[dict]:
    rows = store.fetchall("SELECT doc FROM profiles ORDER BY name")
    return [json.loads(r["doc"]) for r in rows]


def _save(store: Store, doc: dict) -> None:
    store.execute(
        "INSERT INTO profiles (id, name, doc) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
        "doc = excluded.doc",
        (doc["id"], doc["name"], json.dumps(doc)),
    )


def _activate(store: Store, target: dict) -> None:
    for doc in _all(store):
        active = doc["id"] == target["id"]
        if doc.get("is_active") != active:
            doc["is_active"] = active
            _save(store, doc)
    for key, value in (target.get("settings") or {}).items():
        if key not in RESERVED_SETTINGS:
            _set(store, key, value)


def _check_settings_keys(body_settings: dict | None) -> None:
    if body_settings and "pending_restart_fields" in body_settings:
        raise ApiError(
            422,
            "pending_restart_fields is reserved and read-only",
            code="reserved_setting",
        )


# -- routes ---------------------------------------------------------------------


@router.get("/api/v1/profile/")
def list_profiles(store: StoreDep):
    return JSONResponse(
        content={"items": _all(store)}, headers={"ETag": store.etag()}
    )


@router.post("/api/v1/profile/", status_code=201)
def add_profile(request: Request, store: StoreDep, body: ProfileCreate):
    require_if_match(request)
    _check_settings_keys(body.settings)
    with store.write_txn():
        check_if_match(request, store)
        doc = body.model_dump(exclude_none=True)
        doc["id"] = str(uuid.uuid4())
        doc["is_active"] = bool(body.is_active)
        doc["created_at"] = doc["updated_at"] = _now()
        try:
            _save(store, doc)
        except sqlite3.IntegrityError:
            raise ApiError(
                409, f"Profile name already exists: {body.name}",
                code="conflict",
            )
        if doc["is_active"]:
            _activate(store, doc)
        store.bump_revision()
    return JSONResponse(
        status_code=201,
        content=doc,
        headers={"Location": f"/api/v1/profile/{doc['id']}"},
    )


@router.get("/api/v1/profile/{profile_id}")
def get_profile(profile_id: str, store: StoreDep):
    doc = _find(store, profile_id)
    if doc is None:
        raise ApiError(404, f"Profile not found: {profile_id}")
    return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.patch("/api/v1/profile/{profile_id}")
def patch_profile(
    profile_id: str, request: Request, store: StoreDep, body: ProfilePatch
):
    doc = _find(store, profile_id)
    if doc is None:
        raise ApiError(404, f"Profile not found: {profile_id}")
    require_if_match(request)
    _check_settings_keys(body.settings)
    with store.write_txn():
        check_if_match(request, store)
        updates = body.model_dump(exclude_none=True)
        activate = updates.pop("is_active", None)
        doc.update(updates)
        if activate is False:
            doc["is_active"] = False
        doc["updated_at"] = _now()
        try:
            _save(store, doc)
        except sqlite3.IntegrityError:
            raise ApiError(
                409, f"Profile name already exists: {doc['name']}",
                code="conflict",
            )
        if activate is True:
            _activate(store, doc)
            doc["is_active"] = True
        store.bump_revision()
        return JSONResponse(content=doc, headers={"ETag": store.etag()})


@router.delete("/api/v1/profile/{profile_id}", status_code=204)
def delete_profile(profile_id: str, request: Request, store: StoreDep):
    doc = _find(store, profile_id)
    if doc is None:
        raise ApiError(404, f"Profile not found: {profile_id}")
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if doc.get("is_active"):
            raise ApiError(
                409, "Cannot delete the active profile", code="conflict"
            )
        store.execute("DELETE FROM profiles WHERE id = ?", (doc["id"],))
        store.bump_revision()
    return Response(status_code=204)


@router.post("/api/v1/profile/{profile_id}/activate")
def activate_profile(profile_id: str, request: Request, store: StoreDep):
    doc = _find(store, profile_id)
    if doc is None:
        raise ApiError(404, f"Profile not found: {profile_id}")
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        _activate(store, doc)
        doc["is_active"] = True
        _save(store, doc)
        store.bump_revision()
        return JSONResponse(
            content=doc, headers={"ETag": store.etag()}
        )
