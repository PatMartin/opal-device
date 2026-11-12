"""Static registry vocabulary catalog."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .dependencies import StoreDep
from .plugins import plugin_registry
from .schemas import DEVICE_TYPES

router = APIRouter()

EVENT_TYPES: tuple[str, ...] = (
    "device.registered",
    "device.updated",
    "device.removed",
    "device.status_changed",
    "group.created",
    "group.updated",
    "group.removed",
    "peer.registered",
    "peer.updated",
    "peer.removed",
    "peer.synced",
    "sharing_policy.created",
    "sharing_policy.removed",
    "plugin.enabled",
    "plugin.disabled",
    "default.set",
    "default.removed",
    "settings.updated",
    "discovery.completed",
)

CAPABILITY_NAMESPACES: tuple[str, ...] = ("audio", "video", "compute", "network")


@router.get("/api/v1/catalog")
def get_catalog(store: StoreDep):
    return JSONResponse(
        content={
            "device_types": list(DEVICE_TYPES),
            "backends": sorted(plugin_registry().keys()),
            "event_types": list(EVENT_TYPES),
            "capability_namespaces": list(CAPABILITY_NAMESPACES),
        }
    )
