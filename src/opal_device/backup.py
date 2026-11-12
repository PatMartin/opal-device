"""Configuration backup: export/validate/import + managed archives.

Export bundles settings overrides, profiles, defaults, groups, peers
(credential_ref only — never secrets), sharing policies and local
devices. Managed archives are JSON snapshots written under
`<data>/backups/`. Restore/import runs inside one write transaction.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from .dependencies import StoreDep
from .errors import ApiError
from .events import emit
from .precondition import check_if_match, require_if_match
from .settings import _set, merged_settings
from .store import Store

router = APIRouter()

FORMAT_VERSION = "1.0.0"
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.json$")

SECTIONS = (
    "settings", "profiles", "defaults", "groups", "peers",
    "sharing_policies", "devices",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backups_dir(store: Store) -> Path:
    d = Path(store.path).parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_filename(filename: str) -> str:
    if not _FILENAME_RE.match(filename):
        raise ApiError(422, f"Invalid backup filename: {filename}")
    return filename


# -- export / validate / import -------------------------------------------------


def export_document(store: Store) -> dict[str, Any]:
    from .devices import all_devices
    from .defaults import all_defaults
    from .groups import all_groups
    from .peers import all_peers
    from .profiles import _all as all_profiles
    from .sharing import all_policies

    return {
        "format_version": FORMAT_VERSION,
        "exported_at": _now(),
        "service": "opal-device",
        "settings": merged_settings(store),
        "profiles": all_profiles(store),
        "defaults": all_defaults(store),
        "groups": all_groups(store),
        "peers": all_peers(store),
        "sharing_policies": all_policies(store),
        "devices": [
            d for d in all_devices(store) if not d.get("origin_peer_id")
        ],
    }


def validate_document(doc: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(doc, dict):
        return {
            "valid": False,
            "errors": ["bundle must be a JSON object"],
            "warnings": [],
        }
    if "format_version" not in doc:
        errors.append("missing format_version")
    details: dict[str, Any] = {}
    for section in SECTIONS:
        value = doc.get(section)
        if value is None:
            continue
        if section == "settings":
            if not isinstance(value, dict):
                errors.append("settings must be an object")
            else:
                details[section] = len(value)
        elif not isinstance(value, list):
            errors.append(f"{section} must be a list")
        else:
            details[section] = len(value)
    devices = doc.get("devices")
    if isinstance(devices, list):
        for i, dev in enumerate(devices):
            if not isinstance(dev, dict):
                errors.append(f"devices[{i}] must be an object")
                continue
            for field in ("name", "host_id", "host_name", "type", "backend"):
                if not dev.get(field):
                    errors.append(f"devices[{i}] missing {field}")
    known = set(SECTIONS) | {
        "format_version", "exported_at", "service"
    }
    for key in doc:
        if key not in known:
            warnings.append(f"unknown section ignored: {key}")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "details": {"sections": details},
    }


def import_document(store: Store, doc: dict) -> dict[str, int]:
    """Apply an export bundle inside the caller's write transaction."""
    from .devices import _insert, _update_or_409, find_device
    from .defaults import upsert_default
    from .groups import _save_group, find_group
    from .peers import _insert as peer_insert, find_peer
    from .profiles import _save as profile_save, _find as find_profile
    from .sharing import all_policies

    counts: dict[str, int] = {}
    for key, value in (doc.get("settings") or {}).items():
        _set(store, key, value)
        counts["settings"] = counts.get("settings", 0) + 1
    for prof in doc.get("profiles") or []:
        if find_profile(store, prof["id"]) is None:
            profile_save(store, prof)
            counts["profiles"] = counts.get("profiles", 0) + 1
    for grp in doc.get("groups") or []:
        if find_group(store, grp["id"]) is None:
            _save_group(store, grp)
            counts["groups"] = counts.get("groups", 0) + 1
    for dflt in doc.get("defaults") or []:
        upsert_default(store, dflt)
        counts["defaults"] = counts.get("defaults", 0) + 1
    for peer in doc.get("peers") or []:
        if find_peer(store, peer["id"]) is None:
            try:
                peer_insert(store, peer)
            except sqlite3.IntegrityError:
                continue
            counts["peers"] = counts.get("peers", 0) + 1
    for pol in doc.get("sharing_policies") or []:
        existing = {p["id"] for p in all_policies(store)}
        if pol["id"] not in existing:
            store.execute(
                "INSERT INTO sharing_policies (id, doc) VALUES (?, ?)",
                (pol["id"], json.dumps(pol)),
            )
            counts["sharing_policies"] = (
                counts.get("sharing_policies", 0) + 1
            )
    for dev in doc.get("devices") or []:
        existing = find_device(store, dev["id"]) or find_device(
            store, dev.get("qualified_name", "")
        )
        dev.pop("origin_peer_id", None)
        if existing is None:
            try:
                _insert(store, dev)
            except sqlite3.IntegrityError:
                continue
        else:
            _update_or_409(store, dev)
        counts["devices"] = counts.get("devices", 0) + 1
    return counts


def _import_result(counts: dict, message: str) -> dict:
    return {
        "success": True,
        "status": "imported",
        "message": message,
        "counts": counts,
    }


# -- routes ---------------------------------------------------------------------


@router.get("/api/v1/backup/export")
def backup_export(store: StoreDep):
    return JSONResponse(content=export_document(store))


@router.post("/api/v1/backup/validate")
def backup_validate(store: StoreDep, body: Any = Body(...)):
    return JSONResponse(content=validate_document(body))


@router.get("/api/v1/backup/validate")
def backup_validate_stored(store: StoreDep):
    return JSONResponse(content=validate_document(export_document(store)))


@router.post("/api/v1/backup/import")
def backup_import(
    request: Request, store: StoreDep, body: Any = Body(default=None)
):
    require_if_match(request)
    if body is None:
        seed = Path(store.path).parent / "config" / "export.json"
        if not seed.exists():
            raise ApiError(
                422, "No import bundle provided and no config seed found",
                code="import_source_missing",
            )
        body = json.loads(seed.read_text(encoding="utf-8"))
    report = validate_document(body)
    if not report["valid"]:
        raise ApiError(
            422, "Import bundle failed validation",
            code="invalid_bundle", details=report,
        )
    with store.write_txn():
        check_if_match(request, store)
        counts = import_document(store, body)
        emit(store, "settings.updated", {"source": "import"})
        store.bump_revision()
    return JSONResponse(
        content=_import_result(counts, "Configuration imported")
    )


@router.get("/api/v1/backup/list")
def backup_list(store: StoreDep):
    items = []
    for f in sorted(_backups_dir(store).glob("*.json")):
        st = f.stat()
        items.append(
            {
                "filename": f.name,
                "size_bytes": st.st_size,
                "created_at": datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return JSONResponse(content=items)


@router.post("/api/v1/backup/create", status_code=201)
def backup_create(store: StoreDep):
    filename = (
        "opal-device-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + ".json"
    )
    doc = export_document(store)
    path = _backups_dir(store) / filename
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return JSONResponse(
        status_code=201,
        content={"filename": filename, "message": "Backup created"},
    )


@router.get("/api/v1/backup/download/{filename}")
def backup_download(filename: str, store: StoreDep):
    _check_filename(filename)
    path = _backups_dir(store) / filename
    if not path.exists():
        raise ApiError(404, f"Backup not found: {filename}")
    return Response(
        content=path.read_bytes(), media_type="application/octet-stream"
    )


@router.post("/api/v1/backup/restore/{filename}")
def backup_restore(filename: str, request: Request, store: StoreDep):
    _check_filename(filename)
    require_if_match(request)
    path = _backups_dir(store) / filename
    if not path.exists():
        raise ApiError(404, f"Backup not found: {filename}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    report = validate_document(doc)
    if not report["valid"]:
        raise ApiError(
            422, "Backup archive failed validation",
            code="invalid_bundle", details=report,
        )
    with store.write_txn():
        check_if_match(request, store)
        counts = import_document(store, doc)
        store.bump_revision()
    result = _import_result(counts, f"Restored from {filename}")
    result["backup_filename"] = filename
    return JSONResponse(content=result)


@router.delete("/api/v1/backup/{filename}", status_code=204)
def backup_delete(filename: str, store: StoreDep):
    _check_filename(filename)
    path = _backups_dir(store) / filename
    if not path.exists():
        raise ApiError(404, f"Backup not found: {filename}")
    path.unlink()
    return Response(status_code=204)
