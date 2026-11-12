"""Step 5: device create/get/patch/delete + qualified-name lookup."""

from __future__ import annotations

import uuid

import pytest

VALID = {
    "name": "cam1",
    "host_id": "host-1",
    "host_name": "studio",
    "type": "camera",
    "backend": "v4l2",
    "access": "local",
}


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _create(client, **over) -> dict:
    body = {**VALID, **over}
    resp = client.post(
        "/api/v1/device/", json=body, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_returns_201_location_and_contract_fields(client):
    resp = client.post(
        "/api/v1/device/",
        json={**VALID, "labels": {"room": "a"}, "tags": ["front"]},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 201
    loc = resp.headers["location"]
    body = resp.json()
    assert loc == f"/api/v1/device/{body['id']}"
    uuid.UUID(body["id"])
    assert body["qualified_name"] == "studio/cam1"
    assert body["status"] == "unknown"
    assert body["capabilities"] == {}
    assert body["labels"] == {"room": "a"}
    assert body["tags"] == ["front"]
    assert body["access"] == "local"
    # no server extras beyond contract fields
    assert set(body) <= {
        "id", "name", "qualified_name", "host_id", "host_name", "type",
        "backend", "status", "status_reason", "last_seen", "last_checked",
        "origin_peer_id", "capabilities", "configuration",
        "preferred_defaults", "labels", "tags", "access", "resource_uri",
        "metadata",
    }


def test_create_requires_if_match(client):
    assert client.post("/api/v1/device/", json=VALID).status_code == 428


def test_create_stale_etag_412(client):
    resp = client.post(
        "/api/v1/device/", json=VALID, headers={"If-Match": '"cfg-99"'}
    )
    assert resp.status_code == 412


@pytest.mark.parametrize(
    "body",
    [
        {k: v for k, v in VALID.items() if k != "name"},
        {**VALID, "type": "hologram"},
        {**VALID, "access": "moon"},
        {**VALID, "surprise": 1},
    ],
)
def test_create_validation_422(client, body):
    resp = client.post(
        "/api/v1/device/", json=body, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_duplicate_qualified_name_409(client):
    _create(client)
    resp = client.post(
        "/api/v1/device/", json=VALID, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 409


def test_get_by_uuid(client):
    dev = _create(client)
    resp = client.get(f"/api/v1/device/{dev['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == dev["id"]
    assert "etag" in resp.headers


def test_get_by_qualified_name_urlencoded(client):
    dev = _create(client)
    resp = client.get("/api/v1/device/studio%2Fcam1")
    assert resp.status_code == 200
    assert resp.json()["id"] == dev["id"]


def test_get_unknown_404(client):
    resp = client.get(f"/api/v1/device/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_patch_name_updates_qualified_name(client):
    dev = _create(client)
    resp = client.patch(
        f"/api/v1/device/{dev['id']}",
        json={"name": "cam2"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "cam2"
    assert body["qualified_name"] == "studio/cam2"
    assert "etag" in resp.headers
    # old qualified name no longer resolves
    assert client.get("/api/v1/device/studio%2Fcam1").status_code == 404
    assert client.get("/api/v1/device/studio%2Fcam2").status_code == 200


def test_patch_other_fields(client):
    dev = _create(client)
    resp = client.patch(
        f"/api/v1/device/{dev['id']}",
        json={"resource_uri": "v4l2:///dev/video0", "metadata": {"a": 1}},
        headers={"If-Match": _etag(client)},
    )
    body = resp.json()
    assert body["resource_uri"] == "v4l2:///dev/video0"
    assert body["metadata"] == {"a": 1}


def test_patch_by_qualified_name(client):
    _create(client)
    resp = client.patch(
        "/api/v1/device/studio%2Fcam1",
        json={"name": "camX"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["qualified_name"] == "studio/camX"


def test_patch_rename_conflict_409(client):
    _create(client)
    _create(client, name="cam2", host_name="studio")
    dev = client.get("/api/v1/device/studio%2Fcam2").json()
    resp = client.patch(
        f"/api/v1/device/{dev['id']}",
        json={"name": "cam1"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 409


def test_patch_not_found_404(client):
    resp = client.patch(
        f"/api/v1/device/{uuid.uuid4()}",
        json={"name": "x"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 404


def test_patch_rejects_labels_tags_via_extra_422(client):
    dev = _create(client)
    resp = client.patch(
        f"/api/v1/device/{dev['id']}",
        json={"labels": {"a": "b"}},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422  # labels move via sub-resources (Step 8)


def test_patch_missing_if_match_428(client):
    dev = _create(client)
    assert client.patch(f"/api/v1/device/{dev['id']}", json={}).status_code == 428


def test_delete_device(client):
    dev = _create(client)
    resp = client.delete(
        f"/api/v1/device/{dev['id']}", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 204
    assert client.get(f"/api/v1/device/{dev['id']}").status_code == 404


def test_delete_by_qualified_name(client):
    _create(client)
    resp = client.delete(
        "/api/v1/device/studio%2Fcam1", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 204


def test_delete_not_found_404(client):
    resp = client.delete(
        f"/api/v1/device/{uuid.uuid4()}", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 404


def test_delete_requires_if_match(client):
    dev = _create(client)
    assert client.delete(f"/api/v1/device/{dev['id']}").status_code == 428
