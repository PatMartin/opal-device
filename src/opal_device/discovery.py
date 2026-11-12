"""Local discovery scans and inventory upsert.

Scans run enabled plugins (or an explicit plugin_ids subset — explicit
selection overrides the enabled flag) and upsert results into the local
inventory keyed on qualified_name. Dry runs compute the same result
without persisting. Per-plugin failures collect as OpalErrorBody
entries; the scan itself still succeeds. Local scan only — network
probing is opt-in via include_network.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .correlation import ids_from_request
from .dependencies import StoreDep
from .devices import find_device
from .errors import ApiError
from .events import emit
from .plugins import is_enabled, plugin_registry
from .schemas import DiscoveryRequestModel
from .store import Store

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _err(code: str, message: str, request_id: str, txn_id: str) -> dict:
    return {
        "code": code,
        "message": message,
        "request_id": request_id,
        "transaction_id": txn_id,
        "retryable": False,
    }


def _doc_from_found(existing: dict | None, found: dict) -> dict:
    host_name = found["host_name"]
    doc = dict(existing) if existing else {}
    doc.setdefault("id", str(uuid.uuid4()))
    doc["name"] = found["name"]
    doc["qualified_name"] = f"{host_name}/{found['name']}"
    doc["host_id"] = found["host_id"]
    doc["host_name"] = host_name
    doc["type"] = found["type"]
    doc["backend"] = found["backend"]
    doc["access"] = found.get("access", "local")
    doc["status"] = "available"
    doc["last_seen"] = _now()
    doc["capabilities"] = found.get("capabilities", {})
    doc.setdefault("labels", found.get("labels") or {})
    doc.setdefault("tags", found.get("tags") or [])
    for field in ("resource_uri", "configuration", "metadata"):
        if existing is None and found.get(field) is not None:
            doc[field] = found[field]
    return doc


def _persist(store: Store, doc: dict, existing: dict | None) -> bool:
    """Insert/update a discovered doc; True when it was newly created."""
    if existing is None:
        store.execute(
            "INSERT INTO devices (id, qualified_name, name, host_id,"
            " host_name, type, backend, status, origin_peer_id, doc)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                doc["id"], doc["qualified_name"], doc["name"], doc["host_id"],
                doc["host_name"], doc["type"], doc["backend"], doc["status"],
                doc.get("origin_peer_id"), json.dumps(doc),
            ),
        )
        return True
    store.execute(
        "UPDATE devices SET qualified_name=?, name=?, status=?, doc=? WHERE id=?",
        (doc["qualified_name"], doc["name"], doc["status"],
         json.dumps(doc), doc["id"]),
    )
    return False


def _config_fields_changed(existing: dict, doc: dict) -> bool:
    for f in ("capabilities", "configuration", "type", "backend", "access"):
        if existing.get(f) != doc.get(f):
            return True
    return False


def run_scan(
    store: Store,
    *,
    plugin_ids: list[str] | None = None,
    include_network: bool = False,
    dry_run: bool = False,
    request_id: str = "",
    transaction_id: str = "",
) -> dict[str, Any]:
    registry = plugin_registry()
    if plugin_ids is None:
        selected = [p for p in registry.values() if is_enabled(store, p.id)]
        unknown = []
    else:
        selected = [registry[pid] for pid in plugin_ids if pid in registry]
        unknown = [pid for pid in plugin_ids if pid not in registry]

    errors = [
        _err("plugin_not_found", f"Plugin not found: {pid}",
             request_id, transaction_id)
        for pid in unknown
    ]
    discovered: list[dict] = []
    created = updated = 0
    changed = False

    for plugin in selected:
        available, reason = plugin.available()
        if not available:
            errors.append(
                _err(
                    "plugin_unavailable",
                    f"Plugin {plugin.id} unavailable: {reason}",
                    request_id, transaction_id,
                )
            )
            continue
        try:
            found_devices = plugin.discover(
                include_network=include_network, dry_run=dry_run
            )
        except Exception as exc:
            errors.append(
                _err(
                    "plugin_error",
                    f"Plugin {plugin.id} failed: {exc}",
                    request_id, transaction_id,
                )
            )
            continue
        for found in found_devices:
            try:
                qn = f"{found['host_name']}/{found['name']}"
            except KeyError:
                errors.append(
                    _err(
                        "invalid_device",
                        f"Plugin {plugin.id} returned malformed device",
                        request_id, transaction_id,
                    )
                )
                continue
            existing = find_device(store, qn)
            doc = _doc_from_found(existing, found)
            discovered.append(doc)
            if existing is None:
                created += 1
                changed = True
                if not dry_run:
                    _persist(store, doc, None)
                    emit(
                        store, "device.registered", doc,
                        device_id=doc["id"],
                    )
            else:
                if _config_fields_changed(existing, doc):
                    updated += 1
                    changed = True
                if not dry_run:
                    _persist(store, doc, existing)
                    emit(
                        store, "device.updated", doc,
                        device_id=doc["id"],
                    )

    if changed and not dry_run:
        emit(
            store, "discovery.completed",
            {"created": created, "updated": updated},
        )
        store.bump_revision()
    return {
        "discovered": discovered,
        "created": created,
        "updated": updated,
        "errors": errors,
    }


def refresh_inventory(store: Store) -> None:
    """Enabled-plugin scan used by ?refresh=true on inventory reads."""
    with store.write_txn():
        run_scan(store)


# -- routes --------------------------------------------------------------------


@router.post("/api/v1/discovery/scan")
def scan(request: Request, store: StoreDep, body: DiscoveryRequestModel):
    ids = ids_from_request(request)
    with store.write_txn():
        result = run_scan(
            store,
            plugin_ids=body.plugin_ids,
            include_network=body.include_network,
            dry_run=body.dry_run,
            request_id=ids.request_id,
            transaction_id=ids.transaction_id,
        )
    return JSONResponse(content=result)
