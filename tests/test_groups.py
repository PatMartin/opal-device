"""Step 9: group CRUD + members + dynamic selectors."""

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


def _device(client, **over) -> dict:
    resp = client.post(
        "/api/v1/device/",
        json={**VALID, **over},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 201
    return resp.json()


def _group(client, **over) -> dict:
    body = {"name": "g1", "mode": "static", **over}
    resp = client.post(
        "/api/v1/group/", json=body, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_static_group(client):
    resp = client.post(
        "/api/v1/group/",
        json={"name": "g1", "mode": "static", "description": "d"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["mode"] == "static"
    assert body["member_ids"] == []
    assert body["description"] == "d"
    assert resp.headers["location"] == f"/api/v1/group/{body['id']}"
    assert "selector" not in body


def test_create_dynamic_requires_selector(client):
    resp = client.post(
        "/api/v1/group/",
        json={"name": "g", "mode": "dynamic"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_create_static_forbids_selector(client):
    resp = client.post(
        "/api/v1/group/",
        json={"name": "g", "mode": "static", "selector": {"type": "gpu"}},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_create_dynamic_group(client):
    grp = _group(client, mode="dynamic", selector={"type": "camera"})
    assert grp["mode"] == "dynamic"
    assert grp["selector"] == {"type": "camera"}


def test_create_duplicate_name_409(client):
    _group(client)
    resp = client.post(
        "/api/v1/group/",
        json={"name": "g1", "mode": "static"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 409


def test_list_groups(client):
    _group(client)
    body = client.get("/api/v1/group/").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "g1"


def test_get_group_404(client):
    assert client.get(f"/api/v1/group/{uuid.uuid4()}").status_code == 404


def test_patch_group(client):
    grp = _group(client)
    resp = client.patch(
        f"/api/v1/group/{grp['id']}",
        json={"description": "updated"},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated"


def test_patch_selector_on_static_422(client):
    grp = _group(client)
    resp = client.patch(
        f"/api/v1/group/{grp['id']}",
        json={"selector": {"type": "gpu"}},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_patch_selector_on_dynamic(client):
    grp = _group(client, mode="dynamic", selector={"type": "camera"})
    resp = client.patch(
        f"/api/v1/group/{grp['id']}",
        json={"selector": {"backend": "v4l2"}},
        headers={"If-Match": _etag(client)},
    )
    assert resp.json()["selector"] == {"backend": "v4l2"}


def test_delete_group(client):
    grp = _group(client)
    assert (
        client.delete(
            f"/api/v1/group/{grp['id']}", headers={"If-Match": _etag(client)}
        ).status_code
        == 204
    )
    assert client.get(f"/api/v1/group/{grp['id']}").status_code == 404


def test_static_member_add_remove(client):
    dev = _device(client)
    grp = _group(client)
    resp = client.put(
        f"/api/v1/group/{grp['id']}/member/{dev['id']}",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["member_ids"] == [dev["id"]]

    members = client.get(f"/api/v1/group/{grp['id']}/member").json()
    assert [d["id"] for d in members["items"]] == [dev["id"]]
    assert members["total"] == 1

    assert (
        client.delete(
            f"/api/v1/group/{grp['id']}/member/{dev['id']}",
            headers={"If-Match": _etag(client)},
        ).status_code
        == 204
    )
    assert client.get(f"/api/v1/group/{grp['id']}/member").json()["items"] == []


def test_member_add_duplicate_409(client):
    dev = _device(client)
    grp = _group(client)
    client.put(
        f"/api/v1/group/{grp['id']}/member/{dev['id']}",
        headers={"If-Match": _etag(client)},
    )
    resp = client.put(
        f"/api/v1/group/{grp['id']}/member/{dev['id']}",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 409


def test_member_ops_dynamic_422(client):
    dev = _device(client)
    grp = _group(client, mode="dynamic", selector={"type": "camera"})
    assert (
        client.put(
            f"/api/v1/group/{grp['id']}/member/{dev['id']}",
            headers={"If-Match": _etag(client)},
        ).status_code
        == 422
    )
    assert (
        client.delete(
            f"/api/v1/group/{grp['id']}/member/{dev['id']}",
            headers={"If-Match": _etag(client)},
        ).status_code
        == 422
    )


def test_member_add_unknown_device_404(client):
    grp = _group(client)
    resp = client.put(
        f"/api/v1/group/{grp['id']}/member/{uuid.uuid4()}",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 404


def test_member_remove_nonmember_404(client):
    dev = _device(client)
    grp = _group(client)
    resp = client.delete(
        f"/api/v1/group/{grp['id']}/member/{dev['id']}",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 404


def test_dynamic_member_list_evaluates_selector(client):
    cam = _device(client, name="cam")
    _device(client, name="mic", type="microphone")
    grp = _group(client, mode="dynamic", selector={"type": "camera"})
    members = client.get(f"/api/v1/group/{grp['id']}/member").json()
    assert [d["id"] for d in members["items"]] == [cam["id"]]
    # member_ids on the group view reflect live eval too
    view = client.get(f"/api/v1/group/{grp['id']}").json()
    assert view["member_ids"] == [cam["id"]]


def test_member_add_by_qualified_name(client):
    dev = _device(client)
    grp = _group(client)
    resp = client.put(
        f"/api/v1/group/{grp['id']}/member/studio%2Fcam1",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["member_ids"] == [dev["id"]]


def test_group_mutations_require_if_match(client):
    assert client.post("/api/v1/group/", json={"name": "g", "mode": "static"}).status_code == 428
