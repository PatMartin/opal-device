from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opal_device.app import create_app
from opal_device.store import Store


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(str(tmp_path / "registry.db"))
    yield s
    s.close()


@pytest.fixture
def app(tmp_path):
    return create_app(data_dir=tmp_path)


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_real_audio(monkeypatch):
    # discovery scans would enumerate real host audio hardware
    monkeypatch.setattr(
        "opal_device.audio_plugin._query_devices", lambda: []
    )
    monkeypatch.setattr(
        "opal_device.audio_plugin._query_hostapis", lambda: []
    )
