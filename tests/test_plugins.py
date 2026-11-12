"""Step 11: plugin registry + enable/disable/test."""

from __future__ import annotations


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def test_list_plugins_includes_builtin_host(client):
    body = client.get("/api/v1/plugin/").json()
    assert "items" in body
    host = next(p for p in body["items"] if p["id"] == "host")
    assert host["installed"] is True
    assert host["enabled"] is True
    assert host["available"] is True
    assert "cpu" in host["supported_types"]
    assert host["name"]
    assert host["version"]


def test_get_plugin(client):
    body = client.get("/api/v1/plugin/host").json()
    assert body["id"] == "host"


def test_get_plugin_404(client):
    assert client.get("/api/v1/plugin/nope").status_code == 404


def test_disable_enable_round_trip(client):
    resp = client.post(
        "/api/v1/plugin/host/disable", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert client.get("/api/v1/plugin/host").json()["enabled"] is False

    resp = client.post(
        "/api/v1/plugin/host/enable", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def test_redundant_toggle_409(client):
    resp = client.post(
        "/api/v1/plugin/host/enable", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 409


def test_toggle_unknown_404(client):
    resp = client.post(
        "/api/v1/plugin/nope/enable", headers={"If-Match": _etag(client)}
    )
    assert resp.status_code == 404


def test_toggle_requires_if_match(client):
    assert client.post("/api/v1/plugin/host/disable").status_code == 428


def test_plugin_test(client):
    body = client.post("/api/v1/plugin/host/test").json()
    assert body["plugin_id"] == "host"
    assert body["ok"] is True
    assert isinstance(body["details"], dict)


def test_plugin_test_unknown_404(client):
    assert client.post("/api/v1/plugin/nope/test").status_code == 404
