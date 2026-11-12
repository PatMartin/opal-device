"""Step 16: sharing policy CRUD + deny-wins evaluation."""

from __future__ import annotations

import uuid


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


def _peer(client, trusted=True):
    return client.post(
        "/api/v1/peer/",
        headers={"If-Match": _etag(client)},
        json={
            "name": f"p-{uuid.uuid4().hex[:6]}",
            "base_url": f"https://{uuid.uuid4().hex[:6]}.example",
            "trusted": trusted,
        },
    ).json()


def _policy(client, **kw):
    return client.post(
        "/api/v1/sharing/policy/",
        headers={"If-Match": _etag(client)},
        json=kw,
    )


def test_list_empty_and_default(client):
    body = client.get("/api/v1/sharing/policy/").json()
    assert body["default_visibility"] == "allow_trusted"
    assert body["items"] == []


def test_create_list_delete(client):
    resp = _policy(client, effect="deny", type="camera",
                   description="no cams")
    assert resp.status_code == 201
    pol = resp.json()
    assert pol["effect"] == "deny"
    assert pol["type"] == "camera"
    items = client.get("/api/v1/sharing/policy/").json()["items"]
    assert [p["id"] for p in items] == [pol["id"]]
    assert client.delete(
        f"/api/v1/sharing/policy/{pol['id']}",
        headers={"If-Match": _etag(client)},
    ).status_code == 204
    assert client.delete(
        f"/api/v1/sharing/policy/{pol['id']}",
        headers={"If-Match": _etag(client)},
    ).status_code == 404


def test_policy_mutations_require_if_match(client):
    assert client.post(
        "/api/v1/sharing/policy/", json={"effect": "deny"}
    ).status_code == 428


def test_visibility_deny_wins(client):
    from opal_device.sharing import visible_devices

    store = client.app.state.store
    dev = _device(client, type="camera")
    peer = _peer(client)
    _policy(client, effect="deny", type="camera")
    _policy(client, effect="allow", device_id=dev["id"])
    # deny wins over the device-specific allow
    assert visible_devices(store, peer, [dev]) == []


def test_visibility_untrusted_sees_nothing(client):
    from opal_device.sharing import visible_devices

    store = client.app.state.store
    dev = _device(client)
    peer = _peer(client, trusted=False)
    assert visible_devices(store, peer, [dev]) == []


def test_visibility_default_deny_requires_allow(client):
    from opal_device.sharing import visible_devices

    store = client.app.state.store
    dev = _device(client)
    peer = _peer(client)
    # flip default to deny_trusted
    client.put(
        "/api/v1/setting/federation.default_visibility",
        headers={"If-Match": _etag(client)},
        json={"value": "deny_trusted"},
    )
    assert visible_devices(store, peer, [dev]) == []
    _policy(client, effect="allow", peer_id=peer["id"])
    assert visible_devices(store, peer, [dev]) == [dev]


def test_policy_host_and_peer_selectors(client):
    from opal_device.sharing import visible_devices

    store = client.app.state.store
    dev1 = _device(client, name="a", host="hx")
    dev2 = _device(client, name="b", host="hy")
    peer = _peer(client)
    _policy(client, effect="deny", host="hx")
    visible = visible_devices(store, peer, [dev1, dev2])
    assert visible == [dev2]
