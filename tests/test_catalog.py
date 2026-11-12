"""Step 12: catalog."""

from __future__ import annotations


def test_catalog_shape(client):
    resp = client.get("/api/v1/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "device_types", "backends", "event_types", "capability_namespaces"
    }
    for key in body:
        assert isinstance(body[key], list)
        assert all(isinstance(x, str) for x in body[key])


def test_catalog_contents(client):
    body = client.get("/api/v1/catalog").json()
    for t in ("camera", "microphone", "cpu", "gpu", "other"):
        assert t in body["device_types"]
    assert "host" in body["backends"]
    assert "device.registered" in body["event_types"]
    assert "compute" in body["capability_namespaces"]
