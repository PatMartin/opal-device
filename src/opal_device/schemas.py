"""Contract-shaped pydantic models (device kernel schemas)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

DeviceType = Literal[
    "camera", "microphone", "speaker", "cpu", "gpu", "npu", "accelerator", "other"
]
Availability = Literal["available", "unavailable", "unknown", "stale"]
Access = Literal["local", "network", "virtual"]

DEVICE_TYPES: tuple[str, ...] = (
    "camera", "microphone", "speaker", "cpu", "gpu", "npu", "accelerator", "other"
)


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    host_id: str
    host_name: str
    type: DeviceType
    backend: str
    access: Access
    resource_uri: str | None = None
    configuration: dict[str, Any] | None = None
    labels: dict[str, str] | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    resource_uri: str | None = None
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class LabelSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class TagSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str


class DiscoveryRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_ids: list[str] | None = None
    include_network: bool = False
    dry_run: bool = False
