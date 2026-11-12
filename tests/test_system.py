"""Step 2: kernel info/health surface."""

from __future__ import annotations

import re

TOKEN_RE = re.compile(r"^[!-~]{1,128}$")
CORR = ("x-opal-transaction-id", "x-opal-request-id")


def _assert_corr(resp):
    for header in CORR:
        assert TOKEN_RE.match(resp.headers[header])


def test_info(client):
    resp = client.get("/api/v1/info/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "opal-device"
    assert body["version"]
    assert body["api_version"] == "v1"
    _assert_corr(resp)


def test_info_ping(client):
    resp = client.get("/api/v1/info/ping")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    _assert_corr(resp)


def test_health_live(client):
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ready"] is True
    _assert_corr(resp)


def test_health_ready_ok(client):
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ready"] is True
    _assert_corr(resp)


def test_health_ready_unavailable_503_probe_body(client, app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(app.state.store, "fetchone", boom)
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    # Probe 503 carries OpalHealthProbe, not the OpalError envelope.
    assert body["status"] == "unavailable"
    assert body["ready"] is False
    _assert_corr(resp)


def test_health_aggregate(client):
    resp = client.get("/api/v1/health/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["ready"] is True
    assert body["database_ready"] is True
    assert body["uptime_seconds"] >= 0
    assert body["checked_at"]
    assert body["components"]["database"]["state"] == "ok"
    _assert_corr(resp)


def test_health_aggregate_degraded(client, app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(app.state.store, "fetchone", boom)
    resp = client.get("/api/v1/health/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["ready"] is False
    assert body["database_ready"] is False


def test_method_not_allowed_envelope(client):
    resp = client.post("/api/v1/info/ping")
    assert resp.status_code == 405
    assert "error" in resp.json()
    _assert_corr(resp)


def test_trailing_slash_behavior(client):
    # Contract mounts /api/v1/health/ (trailing slash); bare path redirects.
    resp = client.get("/api/v1/health", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].endswith("/api/v1/health/")
    resp2 = client.get("/api/v1/health")
    assert resp2.status_code == 200
