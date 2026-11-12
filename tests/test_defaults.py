"""Step 10: scoped preferred defaults."""

from __future__ import annotations


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _put(client, scope="type", target="camera", values=None):
    return client.put(
        f"/api/v1/default/{scope}/{target}",
        json={"scope": scope, "target": target, "values": values or {"k": "v"}},
        headers={"If-Match": _etag(client)},
    )


def test_empty_defaults(client):
    assert client.get("/api/v1/default/").json() == {"items": []}


def test_put_creates_and_updates(client):
    resp = _put(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"scope": "type", "target": "camera", "values": {"k": "v"}}

    resp2 = _put(client, values={"k": "v2", "n": 1})
    assert resp2.json()["values"] == {"k": "v2", "n": 1}
    items = client.get("/api/v1/default/").json()["items"]
    assert len(items) == 1


def test_put_all_scopes(client):
    for scope in ("global", "host", "device", "type"):
        assert _put(client, scope=scope, target="t").status_code == 200
    items = client.get("/api/v1/default/").json()["items"]
    assert len(items) == 4


def test_put_invalid_scope_422(client):
    resp = _put(client, scope="universe")
    assert resp.status_code == 422


def test_put_body_path_mismatch_422(client):
    resp = client.put(
        "/api/v1/default/type/camera",
        json={"scope": "type", "target": "gpu", "values": {}},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422
    resp = client.put(
        "/api/v1/default/type/camera",
        json={"scope": "host", "target": "camera", "values": {}},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_put_requires_if_match(client):
    resp = client.put(
        "/api/v1/default/type/camera",
        json={"scope": "type", "target": "camera", "values": {}},
    )
    assert resp.status_code == 428


def test_put_missing_values_422(client):
    resp = client.put(
        "/api/v1/default/type/camera",
        json={"scope": "type", "target": "camera"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_delete_default(client):
    _put(client)
    resp = client.delete(
        "/api/v1/default/type/camera", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 204
    assert client.get("/api/v1/default/").json() == {"items": []}


def test_delete_missing_404(client):
    resp = client.delete(
        "/api/v1/default/type/camera", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 404


def test_delete_requires_if_match(client):
    _put(client)
    assert client.delete("/api/v1/default/type/camera").status_code == 428


def test_target_with_qualified_device_name(client):
    resp = client.put(
        "/api/v1/default/device/studio%2Fcam1",
        json={
            "scope": "device",
            "target": "studio/cam1",
            "values": {"prefer": True},
        },
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    items = client.get("/api/v1/default/").json()["items"]
    assert items[0]["target"] == "studio/cam1"
    assert (
        client.delete(
            "/api/v1/default/device/studio%2Fcam1",
            headers={"If-Match": _etag(client)},
        ).status_code
        == 204
    )
