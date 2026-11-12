"""Step 17: federation endpoints + trusted-peer authorization."""

from __future__ import annotations

import json


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _device(client, name="mic", host="h1", type="microphone"):
    return client.post(
        "/api/v1/device/",
        headers={"If-Match": _etag(client)},
        json={
            "name": name, "host_id": host, "host_name": host,
            "type": type, "backend": "b", "access": "local",
        },
    ).json()


def _peer(client, name, url, trusted=True, cred="PA"):
    return client.post(
        "/api/v1/peer/",
        headers={"If-Match": _etag(client)},
        json={
            "name": name, "base_url": url,
            "trusted": trusted, "credential_ref": cred,
        },
    ).json()


def test_registry_identity(client):
    body = client.get("/api/v1/federation/registry").json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["registry_id"]
    # stable across calls
    assert (
        client.get("/api/v1/federation/registry").json()["registry_id"]
        == body["registry_id"]
    )


def test_inventory_management_sees_all_local(client):
    _device(client)
    body = client.get("/api/v1/federation/inventory").json()
    assert body["registry_id"]
    assert body["host"]
    assert body["snapshot_at"]
    assert len(body["devices"]) == 1
    assert body["next_cursor"] is None


def test_peer_token_inventory(client, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "mgmt-token")
    monkeypatch.setenv("OPAL_DEVICE_CRED_PA", "peer-secret")
    peer = _peer(client, "pa", "https://pa.example", trusted=True)
    resp = client.get(
        "/api/v1/federation/inventory",
        headers={"Authorization": "Bearer peer-secret"},
    )
    assert resp.status_code == 200


def test_peer_token_forbidden_elsewhere(client, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "mgmt-token")
    monkeypatch.setenv("OPAL_DEVICE_CRED_PA", "peer-secret")
    _peer(client, "pa", "https://pa.example", trusted=True)
    resp = client.get(
        "/api/v1/device/",
        headers={"Authorization": "Bearer peer-secret"},
    )
    assert resp.status_code == 403


def test_untrusted_peer_403(client, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "mgmt-token")
    monkeypatch.setenv("OPAL_DEVICE_CRED_PA", "peer-secret")
    _peer(client, "pa", "https://pa.example", trusted=False)
    resp = client.get(
        "/api/v1/federation/inventory",
        headers={"Authorization": "Bearer peer-secret"},
    )
    assert resp.status_code == 403


def test_wrong_token_401(client, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "mgmt-token")
    monkeypatch.setenv("OPAL_DEVICE_CRED_PA", "peer-secret")
    resp = client.get(
        "/api/v1/federation/inventory",
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_inventory_sharing_policy_applies(client, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_CRED_PA", "peer-secret")
    dev1 = _device(client, name="cam", host="h1", type="camera")
    _device(client, name="mic", host="h1", type="microphone")
    peer = _peer(client, "pa", "https://pa.example", trusted=True)
    # deny cameras
    client.post(
        "/api/v1/sharing/policy/",
        headers={"If-Match": _etag(client)},
        json={"effect": "deny", "type": "camera"},
    )
    resp = client.get(
        "/api/v1/federation/inventory",
        headers={"Authorization": "Bearer peer-secret"},
    )
    ids = [d["id"] for d in resp.json()["devices"]]
    assert dev1["id"] not in ids


def test_inventory_excludes_remote_devices(client):
    dev = _device(client)
    # mark it federated
    store = client.app.state.store
    doc = json.loads(
        store.fetchone(
            "SELECT doc FROM devices WHERE id = ?", (dev["id"],)
        )["doc"]
    )
    doc["origin_peer_id"] = "peer-x"
    store.execute(
        "UPDATE devices SET doc = ?, origin_peer_id = ? WHERE id = ?",
        (json.dumps(doc), "peer-x", dev["id"]),
    )
    body = client.get("/api/v1/federation/inventory").json()
    assert body["devices"] == []
