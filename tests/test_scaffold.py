"""Step 1: scaffold — OpalError envelope, correlation headers, Store."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from opal_device.app import create_app
from opal_device.errors import ApiError
from opal_device.store import Store

TOKEN_RE = re.compile(r"^[!-~]{1,128}$")


def test_unknown_route_returns_opal_error_envelope(client):
    resp = client.get("/api/v1/definitely-not-a-route")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == "not_found"
    assert err["message"]
    assert TOKEN_RE.match(err["request_id"])
    assert TOKEN_RE.match(err["transaction_id"])
    assert "retryable" in err


def test_correlation_headers_generated_on_every_response(client):
    resp = client.get("/api/v1/definitely-not-a-route")
    for header in ("x-opal-transaction-id", "x-opal-request-id"):
        assert TOKEN_RE.match(resp.headers[header])
    assert resp.headers["x-opal-transaction-id"] != resp.headers["x-opal-request-id"]


def test_supplied_correlation_ids_echoed(client):
    resp = client.get(
        "/api/v1/definitely-not-a-route",
        headers={
            "X-Opal-Transaction-Id": "txn-abc",
            "X-Opal-Session-Id": "sess-1",
            "X-Opal-Causation-Id": "cause-9",
            "X-Opal-Request-Id": "req-7",
        },
    )
    assert resp.headers["x-opal-transaction-id"] == "txn-abc"
    assert resp.headers["x-opal-session-id"] == "sess-1"
    assert resp.headers["x-opal-causation-id"] == "cause-9"
    assert resp.headers["x-opal-request-id"] == "req-7"
    err = resp.json()["error"]
    assert err["transaction_id"] == "txn-abc"
    assert err["session_id"] == "sess-1"
    assert err["causation_id"] == "cause-9"


@pytest.mark.parametrize(
    "header",
    [
        "X-Opal-Session-Id",
        "X-Opal-Transaction-Id",
        "X-Opal-Request-Id",
        "X-Opal-Causation-Id",
    ],
)
def test_invalid_correlation_header_rejected_422(client, header):
    resp = client.get("/api/v1/x", headers={header: "has space"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "invalid_correlation_header"
    assert err["details"]["header"] == header.lower()
    assert TOKEN_RE.match(resp.headers["x-opal-transaction-id"])


def test_too_long_correlation_header_rejected(client):
    resp = client.get("/api/v1/x", headers={"X-Opal-Transaction-Id": "x" * 129})
    assert resp.status_code == 422


def test_api_error_raised_in_route(client, app):
    @app.get("/api/v1/boom")
    def boom():
        raise ApiError(409, "nope", code="test_conflict")

    resp = client.get("/api/v1/boom")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "test_conflict"


def test_unhandled_exception_returns_500_envelope(app):
    @app.get("/api/v1/boom")
    def boom():
        raise RuntimeError("kaboom")

    with TestClient(app) as client:
        resp = client.get("/api/v1/boom")
    assert resp.status_code == 500
    err = resp.json()["error"]
    assert err["code"] == "internal_error"
    assert TOKEN_RE.match(resp.headers["x-opal-transaction-id"])


def test_store_revision_and_etag(store: Store):
    assert store.revision() == 0
    assert store.etag() == '"cfg-0"'
    with store.write_txn():
        assert store.bump_revision() == 1
    assert store.revision() == 1
    assert store.etag() == '"cfg-1"'


def test_write_txn_rolls_back_on_error(store: Store):
    with pytest.raises(ValueError):
        with store.write_txn():
            store.bump_revision()
            raise ValueError("fail")
    assert store.revision() == 0


def test_nested_write_txn_uses_savepoints(store: Store):
    with store.write_txn():
        store.bump_revision()
        with pytest.raises(RuntimeError):
            with store.write_txn():
                store.bump_revision()
                raise RuntimeError("inner")
        store.bump_revision()
    assert store.revision() == 2


def test_data_dir_created_and_db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OPAL_DEVICE_DATA_DIR", str(tmp_path / "dd"))
    app = create_app()
    assert (tmp_path / "dd" / "registry.db").exists()
    app.state.store.close()
