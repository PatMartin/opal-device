"""Step 4: settings map + per-key ops + If-Match/ETag concurrency."""

from __future__ import annotations

import pytest


def _etag(client) -> str:
    resp = client.get("/api/v1/setting/")
    assert resp.status_code == 200
    return resp.headers["etag"]


def test_get_map_returns_defaults_and_etag(client):
    resp = client.get("/api/v1/setting/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["federation.auto_discovery_enabled"] is False
    assert body["federation.default_visibility"] == "allow_trusted"
    assert body["events.publish_enabled"] is False
    assert resp.headers["etag"] == '"cfg-0"'


def test_patch_requires_if_match_428(client):
    resp = client.patch("/api/v1/setting/", json={"a": 1})
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "precondition_required"


@pytest.mark.parametrize(
    "tag", ['W/"cfg-0"', '"a", "b"', "*", "cfg-0", '"unclosed']
)
def test_malformed_if_match_422(client, tag):
    resp = client.patch(
        "/api/v1/setting/", json={"a": 1}, headers={"If-Match": tag}
    )
    assert resp.status_code == 422, tag
    assert resp.json()["error"]["code"] == "invalid_if_match"


def test_stale_if_match_412(client):
    resp = client.patch(
        "/api/v1/setting/", json={"a": 1}, headers={"If-Match": '"cfg-999"'}
    )
    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "precondition_failed"


def test_patch_applies_atomically_and_bumps_etag(client):
    tag = _etag(client)
    resp = client.patch(
        "/api/v1/setting/",
        json={"federation.auto_discovery_enabled": True, "custom.x": [1, 2]},
        headers={"If-Match": tag},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["federation.auto_discovery_enabled"] is True
    assert body["custom.x"] == [1, 2]
    new_tag = resp.headers["etag"]
    assert new_tag != tag

    # Old tag is now stale
    resp2 = client.patch(
        "/api/v1/setting/", json={"b": 1}, headers={"If-Match": tag}
    )
    assert resp2.status_code == 412


def test_failed_mutation_does_not_bump_revision(client):
    tag = _etag(client)
    client.patch(
        "/api/v1/setting/", json={"a": 1}, headers={"If-Match": '"cfg-99"'}
    )
    assert _etag(client) == tag


def test_patch_rejects_reserved_key(client):
    resp = client.patch(
        "/api/v1/setting/",
        json={"pending_restart_fields": ["x"]},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "reserved_setting"


def test_patch_non_object_body_422(client):
    resp = client.patch(
        "/api/v1/setting/", json=[1, 2], headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 422


def test_get_key_default(client):
    resp = client.get("/api/v1/setting/events.publish_enabled")
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "events.publish_enabled"
    assert body["value"] is False
    assert body["is_default"] is True
    assert body["default"] is False


def test_get_unknown_key_404(client):
    resp = client.get("/api/v1/setting/nope")
    assert resp.status_code == 404


def test_put_creates_and_updates_key(client):
    tag = _etag(client)
    resp = client.put(
        "/api/v1/setting/discovery.interval_s",
        json={"value": 30},
        headers={"If-Match": tag},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["value"] == 30
    assert body["is_default"] is False
    assert body["updated_at"]

    resp2 = client.put(
        "/api/v1/setting/discovery.interval_s",
        json={"value": 60},
        headers={"If-Match": _etag(client)},
    )
    assert resp2.json()["value"] == 60


def test_put_requires_if_match(client):
    assert (
        client.put("/api/v1/setting/x", json={"value": 1}).status_code == 428
    )


def test_put_missing_value_422(client):
    resp = client.put(
        "/api/v1/setting/x", json={}, headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 422


def test_put_extra_field_422(client):
    resp = client.put(
        "/api/v1/setting/x",
        json={"value": 1, "extra": 2},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_put_reserved_key_422(client):
    resp = client.put(
        "/api/v1/setting/pending_restart_fields",
        json={"value": []},
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 422


def test_delete_reverts_to_default(client):
    tag = _etag(client)
    client.put(
        "/api/v1/setting/events.publish_enabled",
        json={"value": True},
        headers={"If-Match": tag},
    )
    resp = client.delete(
        "/api/v1/setting/events.publish_enabled",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 204
    body = client.get("/api/v1/setting/events.publish_enabled").json()
    assert body["is_default"] is True
    assert body["value"] is False


def test_delete_unknown_key_404(client):
    resp = client.delete(
        "/api/v1/setting/nope", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 404


def test_delete_requires_if_match(client):
    assert client.delete("/api/v1/setting/events.publish_enabled").status_code == 428


def test_correlation_headers_on_settings(client):
    resp = client.get("/api/v1/setting/")
    assert "x-opal-transaction-id" in resp.headers
    assert "x-opal-request-id" in resp.headers
