"""Step 19: profile CRUD + activation applying settings presets."""

from __future__ import annotations


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _create(client, name="p1", **kw):
    return client.post(
        "/api/v1/profile/",
        headers={"If-Match": _etag(client)},
        json={"name": name, **kw},
    )


def _setting(client, key):
    return client.get(f"/api/v1/setting/{key}").json()["value"]


def test_create_list_get(client):
    resp = _create(client, "p1", description="first",
                   settings={"federation.default_visibility": "deny_trusted"})
    assert resp.status_code == 201
    assert resp.headers["location"].startswith("/api/v1/profile/")
    prof = resp.json()
    assert prof["is_active"] is False
    assert prof["settings"]["federation.default_visibility"] == "deny_trusted"
    assert prof["created_at"] and prof["updated_at"]
    items = client.get("/api/v1/profile/").json()["items"]
    assert [p["id"] for p in items] == [prof["id"]]
    assert client.get(f"/api/v1/profile/{prof['id']}").json() == prof
    assert client.get("/api/v1/profile/p1").json()["id"] == prof["id"]


def test_create_requires_if_match_and_409_dup(client):
    assert client.post(
        "/api/v1/profile/", json={"name": "x"}
    ).status_code == 428
    _create(client, "dup")
    assert _create(client, "dup").status_code == 409


def test_activate_applies_settings(client):
    _create(client, "quiet", settings={
        "federation.default_visibility": "deny_trusted"})
    prof = client.get("/api/v1/profile/quiet").json()
    assert _setting(client, "federation.default_visibility") == \
        "allow_trusted"
    resp = client.post(
        f"/api/v1/profile/{prof['id']}/activate",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True
    assert _setting(client, "federation.default_visibility") == \
        "deny_trusted"


def test_only_one_active(client):
    _create(client, "a", is_active=True)
    _create(client, "b", is_active=True)
    items = client.get("/api/v1/profile/").json()["items"]
    assert [p["is_active"] for p in items] == [False, True]


def test_patch_is_active_activates(client):
    _create(client, "a", is_active=True)
    _create(client, "b", settings={"x.k": 1})
    prof_b = client.get("/api/v1/profile/b").json()
    resp = client.patch(
        f"/api/v1/profile/{prof_b['id']}",
        headers={"If-Match": _etag(client)},
        json={"is_active": True},
    )
    assert resp.json()["is_active"] is True
    items = client.get("/api/v1/profile/").json()["items"]
    assert [p["is_active"] for p in items] == [False, True]
    assert _setting(client, "x.k") == 1


def test_patch_fields_and_deactivate(client):
    _create(client, "a", is_active=True)
    prof = client.get("/api/v1/profile/a").json()
    resp = client.patch(
        f"/api/v1/profile/{prof['id']}",
        headers={"If-Match": _etag(client)},
        json={"description": "d", "is_active": False},
    )
    assert resp.json()["description"] == "d"
    assert resp.json()["is_active"] is False


def test_delete_inactive_ok_active_409(client):
    _create(client, "a", is_active=True)
    _create(client, "b")
    active = client.get("/api/v1/profile/a").json()
    inactive = client.get("/api/v1/profile/b").json()
    assert client.delete(
        f"/api/v1/profile/{active['id']}",
        headers={"If-Match": _etag(client)},
    ).status_code == 409
    assert client.delete(
        f"/api/v1/profile/{inactive['id']}",
        headers={"If-Match": _etag(client)},
    ).status_code == 204


def test_reserved_settings_key_422(client):
    assert _create(
        client, "x", settings={"pending_restart_fields": ["k"]}
    ).status_code == 422


def test_profile_404s(client):
    assert client.get("/api/v1/profile/nope").status_code == 404
    assert client.post(
        "/api/v1/profile/nope/activate",
        headers={"If-Match": _etag(client)},
    ).status_code == 404
