"""Step 7: device sub-resources + qualified-name sub-path dispatch."""

from __future__ import annotations

import json
import uuid

VALID = {
    "name": "mic1",
    "host_id": "host-1",
    "host_name": "studio",
    "type": "microphone",
    "backend": "alsa",
    "access": "local",
}


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _create(client, **over) -> dict:
    resp = client.post(
        "/api/v1/device/",
        json={**VALID, **over},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_caps(client, dev_id: str, caps: dict) -> None:
    store = client.app.state.store
    doc = json.loads(
        store.fetchone("SELECT doc FROM devices WHERE id=?", (dev_id,))["doc"]
    )
    doc["capabilities"] = caps
    store.execute(
        "UPDATE devices SET doc=? WHERE id=?", (json.dumps(doc), dev_id)
    )


def test_capabilities_empty(client):
    dev = _create(client)
    resp = client.get(f"/api/v1/device/{dev['id']}/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_capabilities_returned(client):
    dev = _create(client)
    _seed_caps(client, dev["id"], {"audio": {"channels": 2}})
    body = client.get(f"/api/v1/device/{dev['id']}/capabilities").json()
    assert body == {"audio": {"channels": 2}}


def test_capabilities_404(client):
    assert (
        client.get(f"/api/v1/device/{uuid.uuid4()}/capabilities").status_code
        == 404
    )


def test_status_local_origin(client):
    dev = _create(client)
    body = client.get(f"/api/v1/device/{dev['id']}/status").json()
    assert body["device_id"] == dev["id"]
    assert body["status"] == "unknown"
    assert body["origin"] == "local"


def test_status_federated_origin(client):
    dev = _create(client)
    store = client.app.state.store
    doc = json.loads(
        store.fetchone("SELECT doc FROM devices WHERE id=?", (dev["id"],))["doc"]
    )
    doc["origin_peer_id"] = str(uuid.uuid4())
    store.execute(
        "UPDATE devices SET doc=? WHERE id=?", (json.dumps(doc), dev["id"])
    )
    body = client.get(f"/api/v1/device/{dev['id']}/status").json()
    assert body["origin"] == "federated"


def test_status_404(client):
    assert client.get(f"/api/v1/device/{uuid.uuid4()}/status").status_code == 404


def test_device_test_runs_checks(client):
    dev = _create(client)
    resp = client.post(f"/api/v1/device/{dev['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == dev["id"]
    assert body["status"] == "available"
    names = {c["name"] for c in body["checks"]}
    assert {"metadata", "backend"} <= names
    assert all("ok" in c and "name" in c for c in body["checks"])
    assert body["checked_at"]
    # runtime result is now visible on status (no If-Match needed on test)
    assert (
        client.get(f"/api/v1/device/{dev['id']}/status").json()["status"]
        == "available"
    )


def test_device_test_bad_uri_unavailable(client):
    dev = _create(client, resource_uri="not a uri")
    body = client.post(f"/api/v1/device/{dev['id']}/test").json()
    assert body["status"] == "unavailable"
    uri_check = next(c for c in body["checks"] if c["name"] == "resource_uri")
    assert uri_check["ok"] is False


def test_device_test_404(client):
    assert client.post(f"/api/v1/device/{uuid.uuid4()}/test").status_code == 404


def test_qualified_name_subresources(client):
    dev = _create(client)
    base = "/api/v1/device/studio%2Fmic1"
    assert client.get(f"{base}/capabilities").status_code == 200
    assert client.get(f"{base}/status").json()["device_id"] == dev["id"]
    assert client.post(f"{base}/test").status_code == 200


def test_qualified_name_beats_suffix(client):
    # A device literally named 'mic1/status' resolves as an item, not a suffix
    _create(client, name="status", host_name="studio/mic1")
    resp = client.get("/api/v1/device/studio%2Fmic1%2Fstatus")
    assert resp.status_code == 200
    assert resp.json()["qualified_name"] == "studio/mic1/status"


def test_post_to_item_405(client):
    dev = _create(client)
    resp = client.post(f"/api/v1/device/{dev['id']}")
    assert resp.status_code == 405


def test_patch_on_subresource_405(client):
    dev = _create(client)
    resp = client.patch(
        f"/api/v1/device/{dev['id']}/status",
        json={"name": "x"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 405


def test_delete_on_subresource_405(client):
    dev = _create(client)
    resp = client.delete(
        f"/api/v1/device/{dev['id']}/capabilities",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 405
