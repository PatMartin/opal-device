"""Step 13: discovery scan + per-plugin discover + inventory upsert."""

from __future__ import annotations

import platform
import uuid


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def test_scan_creates_host_cpu(client):
    resp = client.post("/api/v1/discovery/scan", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["errors"] == []
    dev = body["discovered"][0]
    assert dev["type"] == "cpu"
    assert dev["backend"] == "host"
    assert dev["status"] == "available"
    assert dev["last_seen"]
    assert "compute" in dev["capabilities"]
    # persisted into inventory
    assert client.get("/api/v1/device/").json()["total"] == 1


def test_scan_is_idempotent_upsert(client):
    client.post("/api/v1/discovery/scan", json={})
    body = client.post("/api/v1/discovery/scan", json={}).json()
    assert body["created"] == 0
    assert client.get("/api/v1/device/").json()["total"] == 1


def test_scan_dry_run_persists_nothing(client):
    body = client.post(
        "/api/v1/discovery/scan", json={"dry_run": True}
    ).json()
    assert body["created"] == 1
    assert len(body["discovered"]) == 1
    assert client.get("/api/v1/device/").json()["total"] == 0


def test_scan_unknown_plugin_id_collects_error(client):
    body = client.post(
        "/api/v1/discovery/scan", json={"plugin_ids": ["nope"]}
    ).json()
    assert body["created"] == 0
    assert body["errors"][0]["code"] == "plugin_not_found"
    assert body["errors"][0]["request_id"]
    assert body["errors"][0]["transaction_id"]


def test_scan_explicit_plugin_ids(client):
    body = client.post(
        "/api/v1/discovery/scan", json={"plugin_ids": ["host"]}
    ).json()
    assert body["created"] == 1


def test_scan_disabled_plugin_runs_none(client):
    client.post(
        "/api/v1/plugin/host/disable", headers={"If-Match": _etag(client)}
    )
    body = client.post("/api/v1/discovery/scan", json={}).json()
    assert body["created"] == 0
    assert body["discovered"] == []
    # but an explicit request still runs it
    body = client.post(
        "/api/v1/discovery/scan", json={"plugin_ids": ["host"]}
    ).json()
    assert body["created"] == 1


def test_plugin_discover_route(client):
    resp = client.post("/api/v1/plugin/host/discover", json={})
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_plugin_discover_unknown_404(client):
    assert (
        client.post("/api/v1/plugin/nope/discover", json={}).status_code
        == 404
    )


def test_scan_requires_body(client):
    assert client.post("/api/v1/discovery/scan").status_code == 422


def test_refresh_true_runs_discovery(client):
    resp = client.get("/api/v1/device/?refresh=true")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_scan_bad_body_422(client):
    resp = client.post("/api/v1/discovery/scan", json={"plugin_ids": "x"})
    assert resp.status_code == 422
