"""Discovery plugin registry.

Plugins enumerate local devices through Python package entry points
(group `opal_device.plugins`); a builtin `host` plugin reports host
compute devices. `enabled` is persisted configuration (If-Match
mutations); `available` is a live probe reported per Plugin read.
"""

from __future__ import annotations

import os
import platform
from importlib.metadata import entry_points
from typing import Any, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .correlation import ids_from_request
from .dependencies import StoreDep
from .errors import ApiError
from .precondition import check_if_match, require_if_match
from .schemas import DiscoveryRequestModel
from .store import Store

router = APIRouter()

ENTRY_POINT_GROUP = "opal_device.plugins"


class DiscoveryPlugin(Protocol):
    id: str
    name: str
    version: str
    supported_types: list[str]
    capabilities: list[str]

    def available(self) -> tuple[bool, str | None]: ...
    def test(self) -> tuple[bool, dict[str, Any]]: ...
    def discover(
        self, include_network: bool = False, dry_run: bool = False
    ) -> list[dict]: ...


class HostPlugin:
    """Builtin backend reporting this host's compute devices."""

    id = "host"
    name = "Host compute"
    version = "1.0.0"
    supported_types = ["cpu"]
    capabilities = ["compute"]

    def available(self) -> tuple[bool, str | None]:
        return True, None

    def test(self) -> tuple[bool, dict[str, Any]]:
        return True, {"cpu_count": os.cpu_count()}

    def discover(
        self, include_network: bool = False, dry_run: bool = False
    ) -> list[dict]:
        host = platform.node() or "localhost"
        caps: dict[str, Any] = {"compute": {"cpu_count": os.cpu_count()}}
        return [
            {
                "name": f"{host}-cpu",
                "host_id": host,
                "host_name": host,
                "type": "cpu",
                "backend": "host",
                "access": "local",
                "capabilities": caps,
            }
        ]


def plugin_registry() -> dict[str, DiscoveryPlugin]:
    from .audio_plugin import AudioPlugin

    registry: dict[str, DiscoveryPlugin] = {
        "host": HostPlugin(),
        "audio": AudioPlugin(),
    }
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        eps = []
    for ep in eps:
        try:
            registry[ep.name] = ep.load()()
        except Exception:
            continue
    return registry


# -- state + views ------------------------------------------------------------


def is_enabled(store: Store, plugin_id: str) -> bool:
    row = store.fetchone(
        "SELECT enabled FROM plugin_state WHERE id = ?", (plugin_id,)
    )
    return bool(row["enabled"]) if row else True


def _set_enabled(store: Store, plugin_id: str, enabled: bool) -> None:
    store.execute(
        "INSERT INTO plugin_state (id, enabled) VALUES (?, ?) "
        "ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled",
        (plugin_id, 1 if enabled else 0),
    )


def _view(store: Store, plugin: DiscoveryPlugin) -> dict:
    available, reason = plugin.available()
    doc = {
        "id": plugin.id,
        "name": plugin.name,
        "enabled": is_enabled(store, plugin.id),
        "installed": True,
        "available": available,
        "supported_types": list(plugin.supported_types),
    }
    if plugin.version:
        doc["version"] = plugin.version
    if plugin.capabilities:
        doc["capabilities"] = list(plugin.capabilities)
    if reason:
        doc["reason"] = reason
    return doc


def _get_plugin(plugin_id: str) -> DiscoveryPlugin:
    plugin = plugin_registry().get(plugin_id)
    if plugin is None:
        raise ApiError(404, f"Plugin not found: {plugin_id}")
    return plugin


# -- routes --------------------------------------------------------------------


@router.get("/api/v1/plugin/")
def list_plugins(store: StoreDep):
    items = [_view(store, p) for p in plugin_registry().values()]
    return JSONResponse(content={"items": items})


@router.get("/api/v1/plugin/{plugin_id}")
def get_plugin(plugin_id: str, store: StoreDep):
    return JSONResponse(content=_view(store, _get_plugin(plugin_id)))


def _toggle(
    plugin_id: str, enabled: bool, request: Request, store: Store
):
    plugin = _get_plugin(plugin_id)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        if is_enabled(store, plugin_id) == enabled:
            state = "enabled" if enabled else "disabled"
            raise ApiError(
                409, f"Plugin is already {state}", code="conflict"
            )
        _set_enabled(store, plugin_id, enabled)
        store.bump_revision()
        return JSONResponse(
            content=_view(store, plugin), headers={"ETag": store.etag()}
        )


@router.post("/api/v1/plugin/{plugin_id}/enable")
def enable_plugin(plugin_id: str, request: Request, store: StoreDep):
    return _toggle(plugin_id, True, request, store)


@router.post("/api/v1/plugin/{plugin_id}/disable")
def disable_plugin(plugin_id: str, request: Request, store: StoreDep):
    return _toggle(plugin_id, False, request, store)


@router.post("/api/v1/plugin/{plugin_id}/test")
def test_plugin(plugin_id: str, store: StoreDep):
    plugin = _get_plugin(plugin_id)
    ok, details = plugin.test()
    return JSONResponse(
        content={"plugin_id": plugin_id, "ok": ok, "details": details}
    )


@router.post("/api/v1/plugin/{plugin_id}/discover")
def discover_plugin(
    plugin_id: str,
    request: Request,
    store: StoreDep,
    body: DiscoveryRequestModel,
):
    _get_plugin(plugin_id)
    from .discovery import run_scan

    ids = ids_from_request(request)
    with store.write_txn():
        result = run_scan(
            store,
            plugin_ids=[plugin_id],
            include_network=body.include_network,
            dry_run=body.dry_run,
            request_id=ids.request_id,
            transaction_id=ids.transaction_id,
        )
    return JSONResponse(content=result)
