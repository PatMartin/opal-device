"""Steps 17-18: federation endpoints, peer authz, sync, remote cache."""

from __future__ import annotations

import json
import tempfile
import time

import httpx
from fastapi.testclient import TestClient


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


# -- remote-cache sync ----------------------------------------------------------


def _remote_app(tmp_path):
    from opal_device.app import create_app

    return create_app(tmp_path / "remote")


def _wire_remote(client, remote_app):
    client.app.state.http_client_factory = (
        lambda peer: TestClient(remote_app)
    )


def _trusted_peer(client, base_url="http://remote"):
    return client.post(
        "/api/v1/peer/",
        headers={"If-Match": _etag(client)},
        json={
            "name": "remote", "base_url": base_url, "trusted": True,
        },
    ).json()


def test_sync_pulls_remote_inventory(client, tmp_path):
    remote = _remote_app(tmp_path)
    rc = TestClient(remote)
    remote_etag = rc.get("/api/v1/setting/").headers["etag"]
    rc.post(
        "/api/v1/device/",
        headers={"If-Match": remote_etag},
        json={
            "name": "mic", "host_id": "rh", "host_name": "rhost",
            "type": "microphone", "backend": "alsa", "access": "local",
        },
    )
    _wire_remote(client, remote)
    peer = _trusted_peer(client)
    resp = client.post(f"/api/v1/peer/{peer['id']}/sync")
    assert resp.status_code == 200
    body = resp.json()
    assert body["peer_id"] == peer["id"]
    assert body["added"] == 1
    assert body["updated"] == 0
    assert body["stale"] == 0
    assert body["errors"] == []
    # remote device now cached, marked federated
    items = client.get("/api/v1/device/").json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "mic"
    assert client.get(
        f"/api/v1/device/{items[0]['id']}/status"
    ).json()["origin"] == "federated"
    # peer freshness updated
    p = client.get(f"/api/v1/peer/{peer['id']}").json()
    assert p["status"] == "available"
    assert p["last_synced"]


def test_sync_stale_on_remote_removal(client, tmp_path):
    remote = _remote_app(tmp_path)
    rc = TestClient(remote)
    etag = rc.get("/api/v1/setting/").headers["etag"]
    dev = rc.post(
        "/api/v1/device/",
        headers={"If-Match": etag},
        json={
            "name": "mic", "host_id": "rh", "host_name": "rhost",
            "type": "microphone", "backend": "alsa", "access": "local",
        },
    ).json()
    _wire_remote(client, remote)
    peer = _trusted_peer(client)
    client.post(f"/api/v1/peer/{peer['id']}/sync")
    # remove remotely, re-sync -> cached copy goes stale
    rc.delete(
        f"/api/v1/device/{dev['id']}",
        headers={"If-Match": rc.get('/api/v1/setting/').headers['etag']},
    )
    body = client.post(f"/api/v1/peer/{peer['id']}/sync").json()
    assert body["stale"] == 1
    item = client.get("/api/v1/device/").json()["items"][0]
    assert item["status"] == "stale"
    assert client.get(
        "/api/v1/device/?include_stale=false"
    ).json()["items"] == []


def test_sync_unreachable_peer_503(client):
    client.app.state.http_client_factory = None
    peer = _trusted_peer(client, base_url="http://127.0.0.1:9")
    resp = client.post(f"/api/v1/peer/{peer['id']}/sync")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "peer_unreachable"
    assert client.get(f"/api/v1/peer/{peer['id']}").json()["status"] == \
        "unavailable"


def test_peer_cred_sends_bearer_to_remote(client, tmp_path, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "remote-secret")
    remote = _remote_app(tmp_path)
    monkeypatch.delenv("OPAL_DEVICE_TOKEN")
    monkeypatch.setenv("OPAL_DEVICE_CRED_PA", "remote-secret")
    _wire_remote(client, remote)
    peer = client.post(
        "/api/v1/peer/",
        headers={"If-Match": _etag(client)},
        json={
            "name": "remote", "base_url": "http://remote",
            "trusted": True, "credential_ref": "PA",
        },
    ).json()
    resp = client.post(f"/api/v1/peer/{peer['id']}/sync")
    assert resp.status_code == 200


def test_peers_sync_all_admission(client, tmp_path):
    remote = _remote_app(tmp_path)
    _wire_remote(client, remote)
    _trusted_peer(client)
    resp = client.post("/api/v1/peer/sync")
    assert resp.status_code == 202
    assert resp.json()["accepted"] is True
    # worker finishes quickly on an empty remote
    for _ in range(40):
        if client.get("/api/v1/peer/").json()["items"][0].get(
            "last_synced"
        ):
            break
        time.sleep(0.05)


def test_peers_discover_enabled_empty(client):
    client.put(
        "/api/v1/setting/federation.auto_discovery_enabled",
        headers={"If-Match": _etag(client)},
        json={"value": True},
    )
    resp = client.post("/api/v1/peer/discover")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_delete_peer_purges_remote_cache(client, tmp_path):
    remote = _remote_app(tmp_path)
    rc = TestClient(remote)
    rc.post(
        "/api/v1/device/",
        headers={"If-Match": rc.get("/api/v1/setting/").headers["etag"]},
        json={
            "name": "mic", "host_id": "rh", "host_name": "rhost",
            "type": "microphone", "backend": "alsa", "access": "local",
        },
    )
    _wire_remote(client, remote)
    peer = _trusted_peer(client)
    client.post(f"/api/v1/peer/{peer['id']}/sync")
    assert client.get("/api/v1/device/").json()["total"] == 1
    client.delete(
        f"/api/v1/peer/{peer['id']}", headers={"If-Match": _etag(client)}
    )
    assert client.get("/api/v1/device/").json()["total"] == 0
