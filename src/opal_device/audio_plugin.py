"""Builtin audio discovery plugin.

Enumerates host microphones and speakers through PortAudio
(`sounddevice`), emitting one registry device per direction-capable
endpoint with channel counts, sample rates, latencies and the host
API backend in capabilities/metadata. The dependency is optional at
import time; `available()` reports why the plugin cannot run.
"""

from __future__ import annotations

import platform
import re
from typing import Any


def _query_devices() -> list[dict]:
    import sounddevice as sd

    return [dict(d) for d in sd.query_devices()]


def _query_hostapis() -> list[dict]:
    import sounddevice as sd

    return [dict(a) for a in sd.query_hostapis()]


def _slug(text: str) -> str:
    # qualified_name is host_name/name — '/' and whitespace must go
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-").lower()
    return slug or "audio-device"


class AudioPlugin:
    id = "audio"
    name = "Host audio (PortAudio)"
    version = "1.0.0"
    supported_types = ["microphone", "speaker"]
    capabilities = ["audio"]

    def available(self) -> tuple[bool, str | None]:
        try:
            self._load()
        except Exception as exc:
            return False, f"sounddevice unavailable: {exc}"
        return True, None

    def _load(self):
        devices = _query_devices()
        hostapis = _query_hostapis()
        return devices, hostapis

    def test(self) -> tuple[bool, dict[str, Any]]:
        try:
            devices, hostapis = self._load()
        except Exception as exc:
            return False, {"error": str(exc)}
        return True, {
            "devices": len(devices),
            "microphones": sum(1 for d in devices
                               if d.get("max_input_channels", 0) > 0),
            "speakers": sum(1 for d in devices
                            if d.get("max_output_channels", 0) > 0),
            "host_apis": [a.get("name") for a in hostapis],
        }

    def discover(
        self, include_network: bool = False, dry_run: bool = False
    ) -> list[dict]:
        devices, hostapis = self._load()
        host = platform.node() or "localhost"
        defaults: dict[int, set] = {}
        for api in hostapis:
            api_devices = api.get("devices") or []
            for kind in ("input", "output"):
                rel = api.get(f"default_{kind}_device", -1)
                if 0 <= rel < len(api_devices):
                    defaults.setdefault(
                        api_devices[rel], set()).add(kind)

        found: list[dict] = []
        for idx, d in enumerate(devices):
            api_name = ""
            if 0 <= d.get("hostapi", -1) < len(hostapis):
                api_name = hostapis[d["hostapi"]].get("name", "")
            base = {
                "host_id": host,
                "host_name": host,
                "backend": "portaudio",
                "access": "local",
                "resource_uri": f"portaudio://{idx}",
                "metadata": {
                    "portaudio_index": idx,
                    "host_api": api_name,
                },
            }
            if defaults.get(idx):
                base["metadata"]["default"] = sorted(defaults[idx])
            if d.get("max_input_channels", 0) > 0:
                found.append({
                    **base,
                    "name": _slug(f"mic-{idx}-{d.get('name', '')}"),
                    "type": "microphone",
                    "capabilities": {"audio": {
                        "channels": d["max_input_channels"],
                        "sample_rate": d.get("default_samplerate"),
                        "latency_low": d.get("default_low_input_latency"),
                        "latency_high": d.get("default_high_input_latency"),
                        "host_api": api_name,
                    }},
                })
            if d.get("max_output_channels", 0) > 0:
                found.append({
                    **base,
                    "name": _slug(f"spk-{idx}-{d.get('name', '')}"),
                    "type": "speaker",
                    "capabilities": {"audio": {
                        "channels": d["max_output_channels"],
                        "sample_rate": d.get("default_samplerate"),
                        "latency_low": d.get("default_low_output_latency"),
                        "latency_high": d.get("default_high_output_latency"),
                        "host_api": api_name,
                    }},
                })
        return found
