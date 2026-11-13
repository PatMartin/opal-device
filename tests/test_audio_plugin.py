"""Audio discovery plugin: PortAudio enumeration -> device docs."""

from __future__ import annotations

import sys
import types

import pytest

from opal_device import audio_plugin
from opal_device.audio_plugin import AudioPlugin

DEVICES = [
    {"name": "Built-in Mic", "hostapi": 0, "max_input_channels": 2,
     "max_output_channels": 0, "default_samplerate": 48000.0,
     "default_low_input_latency": 0.01, "default_high_input_latency": 0.1},
    {"name": "Speakers (Realtek)", "hostapi": 0, "max_input_channels": 0,
     "max_output_channels": 2, "default_samplerate": 44100.0,
     "default_low_output_latency": 0.02, "default_high_output_latency": 0.2},
    {"name": "USB Headset", "hostapi": 1, "max_input_channels": 1,
     "max_output_channels": 1, "default_samplerate": 8000.0,
     "default_low_input_latency": 0.05, "default_high_input_latency": 0.1,
     "default_low_output_latency": 0.05, "default_high_output_latency": 0.1},
]

HOSTAPIS = [
    {"name": "API-A", "devices": [0, 1],
     "default_input_device": 0, "default_output_device": 1},
    {"name": "API-B", "devices": [2],
     "default_input_device": -1, "default_output_device": -1},
]


@pytest.fixture
def fake_sd(monkeypatch):
    monkeypatch.setattr(audio_plugin, "_query_devices",
                        lambda: [dict(d) for d in DEVICES])
    monkeypatch.setattr(audio_plugin, "_query_hostapis",
                        lambda: [dict(a) for a in HOSTAPIS])
    return AudioPlugin()


def test_discover_maps_directions(fake_sd):
    found = {d["name"]: d for d in fake_sd.discover()}
    mic = found["mic-0-built-in-mic"]
    spk = found["spk-1-speakers-realtek"]
    assert mic["type"] == "microphone"
    assert mic["capabilities"]["audio"]["channels"] == 2
    assert mic["capabilities"]["audio"]["sample_rate"] == 48000.0
    assert mic["metadata"]["host_api"] == "API-A"
    assert mic["metadata"]["default"] == ["input"]
    assert spk["type"] == "speaker"
    assert spk["metadata"]["default"] == ["output"]
    assert mic["resource_uri"] == "portaudio://0"
    # duplex device emits one doc per direction
    assert "mic-2-usb-headset" in found
    assert "spk-2-usb-headset" in found


def test_discover_no_direction_means_no_device(fake_sd, monkeypatch):
    monkeypatch.setattr(
        audio_plugin, "_query_devices",
        lambda: [{"name": "dead", "hostapi": 0, "max_input_channels": 0,
                  "max_output_channels": 0}],
    )
    assert fake_sd.discover() == []


def test_available_and_test(fake_sd):
    assert fake_sd.available() == (True, None)
    ok, details = fake_sd.test()
    assert ok
    assert details["microphones"] == 2
    assert details["speakers"] == 2
    assert details["host_apis"] == ["API-A", "API-B"]


def test_unavailable_when_import_fails(monkeypatch):
    def boom():
        raise OSError("PortAudio library not found")

    monkeypatch.setattr(audio_plugin, "_query_devices", boom)
    plugin = AudioPlugin()
    available, reason = plugin.available()
    assert not available
    assert "PortAudio" in reason
    ok, details = plugin.test()
    assert not ok
    assert "PortAudio" in details["error"]


def test_registered_in_plugin_registry():
    from opal_device.plugins import plugin_registry

    assert "audio" in plugin_registry()
    assert "microphone" in plugin_registry()["audio"].supported_types


def test_plugin_view_and_scan(client, fake_sd):
    view = client.get("/api/v1/plugin/audio").json()
    assert view["id"] == "audio"
    assert view["available"] is True
    assert view["supported_types"] == ["microphone", "speaker"]

    resp = client.post("/api/v1/plugin/audio/discover", json={})
    body = resp.json()
    assert body["created"] == 4  # 2 mics + 2 speakers
    names = {d["name"] for d in body["discovered"]}
    assert "mic-0-built-in-mic" in names
    assert "spk-1-speakers-realtek" in names
    # second scan is idempotent (upsert keyed on qualified_name)
    again = client.post("/api/v1/plugin/audio/discover", json={}).json()
    assert again["created"] == 0


def test_scan_dry_run_persists_nothing(client, fake_sd):
    body = client.post(
        "/api/v1/discovery/scan",
        json={"plugin_ids": ["audio"], "dry_run": True},
    ).json()
    assert body["created"] == 4
    assert client.get("/api/v1/device/").json()["items"] == []
