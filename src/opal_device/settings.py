"""Kernel settings surface: portable key/value map with revision ETag.

The portable settings map merges implementation defaults with stored
overrides. Secret material is never returned. `pending_restart_fields`
is a reserved read-only key naming settings that need a restart.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from .dependencies import StoreDep
from .errors import ApiError
from .precondition import check_if_match, require_if_match
from .store import Store

router = APIRouter()

RESERVED_KEYS = {"pending_restart_fields"}

DEFAULT_SETTINGS: dict[str, Any] = {
    "federation.auto_discovery_enabled": False,
    "federation.default_visibility": "allow_trusted",
    "events.publish_enabled": False,
    "pending_restart_fields": [],
}


class SettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any


# -- repository -----------------------------------------------------------


def _overrides(store: Store) -> dict[str, Any]:
    rows = store.fetchall("SELECT key, value FROM settings")
    return {r["key"]: json.loads(r["value"]) for r in rows}


def merged_settings(store: Store) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(_overrides(store))
    return merged


def _get_override(store: Store, key: str) -> tuple[bool, Any, str | None]:
    row = store.fetchone(
        "SELECT value, updated_at FROM settings WHERE key = ?", (key,)
    )
    if row is None:
        return False, None, None
    return True, json.loads(row["value"]), row["updated_at"]


def _set(store: Store, key: str, value: Any) -> None:
    store.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
    )


def _delete(store: Store, key: str) -> bool:
    cur = store.execute("DELETE FROM settings WHERE key = ?", (key,))
    return cur.rowcount > 0


def _setting_read(store: Store, key: str) -> dict[str, Any]:
    found, value, updated_at = _get_override(store, key)
    if not found and key not in DEFAULT_SETTINGS:
        raise ApiError(404, f"Unknown setting key: {key}")
    read: dict[str, Any] = {
        "key": key,
        "value": value if found else DEFAULT_SETTINGS[key],
        "default": DEFAULT_SETTINGS.get(key),
        "is_default": not found,
    }
    if updated_at:
        read["updated_at"] = updated_at
    return read


def _check_reserved(body: dict[str, Any]) -> None:
    bad = RESERVED_KEYS & body.keys()
    if bad:
        raise ApiError(
            422,
            f"Reserved setting key(s) are read-only: {sorted(bad)}",
            code="reserved_setting",
        )


# -- routes ---------------------------------------------------------------


@router.get("/api/v1/setting/")
def list_settings(store: StoreDep) -> JSONResponse:
    return JSONResponse(
        content=merged_settings(store), headers={"ETag": store.etag()}
    )


@router.patch("/api/v1/setting/")
def patch_settings(
    request: Request,
    store: StoreDep,
    body: dict[str, Any] = Body(...),
) -> JSONResponse:
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        _check_reserved(body)
        for key, value in body.items():
            _set(store, key, value)
        store.bump_revision()
        return JSONResponse(
            content=merged_settings(store), headers={"ETag": store.etag()}
        )


@router.get("/api/v1/setting/{key}")
def get_setting(key: str, store: StoreDep) -> dict:
    return _setting_read(store, key)


@router.put("/api/v1/setting/{key}")
def put_setting(
    key: str, request: Request, store: StoreDep, body: SettingUpdate
) -> dict:
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        _check_reserved({key: None})
        _set(store, key, body.value)
        store.bump_revision()
        return _setting_read(store, key)


@router.delete("/api/v1/setting/{key}")
def delete_setting(key: str, request: Request, store: StoreDep) -> Response:
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        _check_reserved({key: None})
        had_override = _delete(store, key)
        if not had_override and key not in DEFAULT_SETTINGS:
            raise ApiError(404, f"Unknown setting key: {key}")
        store.bump_revision()
    return Response(status_code=204)
