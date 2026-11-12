"""Step 15: federation peer CRUD + status."""

from __future__ import annotations


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _add(client, name="peer-a", base_url="https://peer-a.example/api"):
    return client.post(
        "/api/v1/peer/",
        headers={"If-Match": _etag(client)},
        json={"name": name, "base_url": base_url},
    )


def test_create_list_get_peer(client):
    resp = _add(client)
    assert resp.status_code == 201
    assert resp.headers["location"].startswith("/api/v1/peer/")
    peer = resp.json()
    assert peer["name"] == "peer-a"
    assert peer["trusted"] is False
    assert peer["status"] == "unknown"
    assert peer["auto_discovered"] is False
    assert "credential_ref" not in peer

    items = client.get("/api/v1/peer/").json()["items"]
    assert [p["id"] for p in items] == [peer["id"]]

    got = client.get(f"/api/v1/peer/{peer['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == peer["id"]
    assert got.headers["etag"]


def test_create_requires_if_match(client):
    assert client.post(
        "/api/v1/peer/", json={"name": "p", "base_url": "https://x"}
    ).status_code == 428


def test_create_duplicate_base_url_409(client):
    _add(client)
    assert _add(client, name="other").status_code == 409


def test_patch_peer(client):
    peer = _add(client).json()
    resp = client.patch(
        f"/api/v1/peer/{peer['id']}",
        headers={"If-Match": _etag(client)},
        json={"trusted": True, "name": "renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["trusted"] is True
    assert resp.json()["name"] == "renamed"


def test_delete_peer(client):
    peer = _add(client).json()
    assert client.delete(
        f"/api/v1/peer/{peer['id']}", headers={"If-Match": _etag(client)}
    ).status_code == 204
    assert client.get(f"/api/v1/peer/{peer['id']}").status_code == 404


def test_peer_status_returns_peer(client):
    peer = _add(client).json()
    resp = client.get(f"/api/v1/peer/{peer['id']}/status")
    assert resp.status_code == 200
    assert resp.json()["id"] == peer["id"]
    assert resp.json()["status"] == "unknown"


def test_peer_404s(client):
    assert client.get("/api/v1/peer/nope").status_code == 404
    assert client.patch(
        "/api/v1/peer/nope", headers={"If-Match": _etag(client)},
        json={"name": "x"},
    ).status_code == 404
    assert client.delete(
        "/api/v1/peer/nope", headers={"If-Match": _etag(client)}
    ).status_code == 404


def test_credential_ref_stored_never_echoed(client):
    resp = client.post(
        "/api/v1/peer/",
        headers={"If-Match": _etag(client)},
        json={
            "name": "p", "base_url": "https://p.example",
            "credential_ref": "peer_a",
        },
    )
    assert "credential_ref" not in resp.json()
    assert "credential_ref" not in client.get("/api/v1/peer/").text


def test_peers_sync_admission_route_exists(client):
    # registered before /peer/{peer_id}; needs at least one trusted peer
    resp = client.post("/api/v1/peer/sync")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_trusted_peers"


def test_peers_discover_disabled_503(client):
    resp = client.post("/api/v1/peer/discover")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "federation_discovery_disabled"


def test_peer_sync_route_404_then_422_untrusted(client):
    assert client.post("/api/v1/peer/nope/sync").status_code == 404
    peer = _add(client).json()  # untrusted by default
    resp = client.post(f"/api/v1/peer/{peer['id']}/sync")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "peer_untrusted"


def test_peer_events_emitted(client):
    peer = _add(client).json()
    client.delete(
        f"/api/v1/peer/{peer['id']}", headers={"If-Match": _etag(client)}
    )
    names = [
        e["event"] for e in client.get("/api/v1/event/").json()["items"]
    ]
    assert "peer.registered" in names
    assert "peer.removed" in names
