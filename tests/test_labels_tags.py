"""Step 8: device label + tag sub-resources."""

from __future__ import annotations

import uuid

VALID = {
    "name": "cam1",
    "host_id": "h1",
    "host_name": "studio",
    "type": "camera",
    "backend": "v4l2",
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


# -- labels ------------------------------------------------------------------


def test_label_list_empty_then_populated(client):
    dev = _create(client)
    assert client.get(f"/api/v1/device/{dev['id']}/label").json() == {}
    client.put(
        f"/api/v1/device/{dev['id']}/label/zone",
        json={"value": "east"},
        headers={"If-Match": _etag(client)},
    )
    assert client.get(f"/api/v1/device/{dev['id']}/label").json() == {
        "zone": "east"
    }


def test_label_set_returns_full_device(client):
    dev = _create(client)
    resp = client.put(
        f"/api/v1/device/{dev['id']}/label/zone",
        json={"value": "east"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == dev["id"]
    assert body["labels"] == {"zone": "east"}


def test_label_set_overwrites(client):
    dev = _create(client)
    for v in ("a", "b"):
        client.put(
            f"/api/v1/device/{dev['id']}/label/k",
            json={"value": v},
            headers={"If-Match": _etag(client)},
        )
    assert client.get(f"/api/v1/device/{dev['id']}").json()["labels"] == {
        "k": "b"
    }


def test_label_set_requires_if_match(client):
    dev = _create(client)
    resp = client.put(
        f"/api/v1/device/{dev['id']}/label/k", json={"value": "v"}
    )
    assert resp.status_code == 428


def test_label_set_body_validation(client):
    dev = _create(client)
    for body in ({}, {"value": 5}, {"value": "x", "extra": 1}):
        resp = client.put(
            f"/api/v1/device/{dev['id']}/label/k",
            json=body,
            headers={"If-Match": _etag(client)},
        )
        assert resp.status_code == 422, body


def test_label_set_device_404(client):
    resp = client.put(
        f"/api/v1/device/{uuid.uuid4()}/label/k",
        json={"value": "v"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 404


def test_label_delete(client):
    dev = _create(client)
    client.put(
        f"/api/v1/device/{dev['id']}/label/k",
        json={"value": "v"},
        headers={"If-Match": _etag(client)},
    )
    resp = client.delete(
        f"/api/v1/device/{dev['id']}/label/k",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 204
    assert client.get(f"/api/v1/device/{dev['id']}/label").json() == {}


def test_label_delete_missing_404(client):
    dev = _create(client)
    resp = client.delete(
        f"/api/v1/device/{dev['id']}/label/nope",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 404


def test_labels_on_qualified_name(client):
    _create(client)
    base = "/api/v1/device/studio%2Fcam1"
    resp = client.put(
        f"{base}/label/z", json={"value": "1"}, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 200
    assert client.get(f"{base}/label").json() == {"z": "1"}
    assert (
        client.delete(f"{base}/label/z", headers={"If-Match": _etag(client)})
        .status_code
        == 204
    )


# -- tags --------------------------------------------------------------------


def test_tag_list_and_add(client):
    dev = _create(client)
    assert client.get(f"/api/v1/device/{dev['id']}/tag").json() == {
        "items": []
    }
    resp = client.post(
        f"/api/v1/device/{dev['id']}/tag",
        json={"tag": "rack1"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["rack1"]
    assert client.get(f"/api/v1/device/{dev['id']}/tag").json() == {
        "items": ["rack1"]
    }


def test_tag_add_duplicate_409(client):
    dev = _create(client, tags=["x"])
    resp = client.post(
        f"/api/v1/device/{dev['id']}/tag",
        json={"tag": "x"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 409


def test_tag_add_requires_if_match(client):
    dev = _create(client)
    resp = client.post(f"/api/v1/device/{dev['id']}/tag", json={"tag": "x"})
    assert resp.status_code == 428


def test_tag_add_body_validation(client):
    dev = _create(client)
    resp = client.post(
        f"/api/v1/device/{dev['id']}/tag",
        json={"nope": 1},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_tag_delete(client):
    dev = _create(client, tags=["a", "b"])
    resp = client.delete(
        f"/api/v1/device/{dev['id']}/tag/a",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 204
    assert client.get(f"/api/v1/device/{dev['id']}/tag").json() == {
        "items": ["b"]
    }


def test_tag_delete_missing_404(client):
    dev = _create(client)
    resp = client.delete(
        f"/api/v1/device/{dev['id']}/tag/nope",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 404


def test_tags_on_qualified_name(client):
    _create(client)
    base = "/api/v1/device/studio%2Fcam1"
    resp = client.post(
        f"{base}/tag", json={"tag": "t1"}, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 200
    assert (
        client.delete(f"{base}/tag/t1", headers={"If-Match": _etag(client)})
        .status_code
        == 204
    )


def test_get_on_label_item_405(client):
    dev = _create(client)
    resp = client.get(f"/api/v1/device/{dev['id']}/label/k")
    assert resp.status_code == 405
