"""Step 3: Bearer auth middleware + non-loopback refusal."""

from __future__ import annotations

import re

import pytest
import uvicorn
from fastapi.testclient import TestClient

from opal_device import main as main_mod
from opal_device.app import create_app
from opal_device.auth import is_loopback_host

TOKEN_RE = re.compile(r"^[!-~]{1,128}$")


@pytest.fixture
def authed_client(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.auth_token = "s3cret"
    with TestClient(app) as c:
        yield c


def test_missing_credentials_401(authed_client):
    resp = authed_client.get("/api/v1/info/")
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["code"] == "unauthorized"
    assert resp.headers["www-authenticate"] == "Bearer"
    # Correlation headers survive 401 (auth runs inside Correlation).
    assert TOKEN_RE.match(resp.headers["x-opal-transaction-id"])
    assert TOKEN_RE.match(err["request_id"])


def test_wrong_token_401(authed_client):
    resp = authed_client.get(
        "/api/v1/info/", headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


def test_correct_token_200(authed_client):
    resp = authed_client.get(
        "/api/v1/info/", headers={"Authorization": "Bearer s3cret"}
    )
    assert resp.status_code == 200


def test_malformed_scheme_401(authed_client):
    for header in ("Basic s3cret", "Bearer", "Bearer ", "bearer", "TOKEN s3cret"):
        resp = authed_client.get(
            "/api/v1/info/", headers={"Authorization": header}
        )
        assert resp.status_code == 401, header


def test_lowercase_scheme_accepted(authed_client):
    resp = authed_client.get(
        "/api/v1/info/", headers={"Authorization": "bearer s3cret"}
    )
    assert resp.status_code == 200


def test_token_in_query_string_not_accepted(authed_client):
    resp = authed_client.get("/api/v1/info/?access_token=s3cret&token=s3cret")
    assert resp.status_code == 401


def test_all_api_routes_protected(authed_client):
    for path in (
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/health/",
        "/api/v1/info/ping",
    ):
        resp = authed_client.get(path)
        assert resp.status_code == 401, path


def test_mutations_also_protected(authed_client):
    resp = authed_client.post("/api/v1/device/", json={})
    assert resp.status_code == 401  # auth precedes routing/validation


def test_non_api_paths_unaffected(authed_client):
    resp = authed_client.get("/somewhere/else")
    assert resp.status_code == 404  # not 401


def test_dev_mode_no_token_allows_all(client):
    # client fixture has no auth_token configured
    assert client.get("/api/v1/info/").status_code == 200
    assert client.get("/api/v1/health/ready").status_code == 200


def test_env_token_loaded_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "envtok")
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/v1/info/").status_code == 401
        assert (
            c.get(
                "/api/v1/info/", headers={"Authorization": "Bearer envtok"}
            ).status_code
            == 200
        )


def test_is_loopback_host():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("127.55.0.9")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.5")
    assert not is_loopback_host("example.com")


def test_main_refuses_non_loopback_without_token(monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_HOST", "0.0.0.0")
    monkeypatch.delenv("OPAL_DEVICE_TOKEN", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        main_mod.main()


def test_main_allows_non_loopback_with_token(monkeypatch):
    calls = {}
    monkeypatch.setenv("OPAL_DEVICE_HOST", "0.0.0.0")
    monkeypatch.setenv("OPAL_DEVICE_TOKEN", "tok")
    monkeypatch.setattr(
        uvicorn, "run", lambda app, host, port: calls.update(host=host, port=port)
    )
    monkeypatch.setenv("OPAL_DEVICE_PORT", "9090")
    main_mod.main()
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 9090
