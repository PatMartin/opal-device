"""Step 22: REST-only Typer CLI smoke coverage."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from opal_device import cli


class _Shim:
    """Client stand-in bound to the in-process app."""

    def __init__(self, tc):
        self.tc = tc

    def etag(self):
        return self.tc.get("/api/v1/setting/").headers["etag"]

    def req(self, method, path, *, mutate=False, soft=False, **kw):
        if mutate:
            kw.setdefault("headers", {})["If-Match"] = self.etag()
        r = self.tc.request(method, f"/api/v1{path}", **kw)
        if r.status_code >= 400:
            raise SystemExit(1)
        return r

    @property
    def c(self):
        return self.tc


@pytest.fixture
def runner(client, monkeypatch):
    shim = _Shim(client)
    monkeypatch.setattr(cli, "Client", lambda url, token: shim)
    return CliRunner()


def test_info_health_catalog(runner):
    assert runner.invoke(cli.app, ["info"]).exit_code == 0
    assert runner.invoke(cli.app, ["health"]).exit_code == 0
    r = runner.invoke(cli.app, ["catalog"])
    assert "device_types" in r.stdout


def test_setting_set_get(runner):
    r = runner.invoke(
        cli.app, ["setting", "set", "x.key", '"v"']
    )
    assert r.exit_code == 0
    body = json.loads(runner.invoke(
        cli.app, ["setting", "show", "x.key"]).stdout)
    assert body["value"] == "v"


def test_device_crud_via_cli(runner):
    spec = json.dumps({
        "name": "mic", "host_id": "h", "host_name": "h",
        "type": "microphone", "backend": "b", "access": "local",
    })
    r = runner.invoke(cli.app, ["device", "add", spec])
    assert r.exit_code == 0, r.stdout
    dev = json.loads(r.stdout)
    assert runner.invoke(
        cli.app, ["device", "show", dev["id"]]).exit_code == 0
    assert runner.invoke(
        cli.app, ["device", "tag", "add", dev["id"], "t1"]
    ).exit_code == 0
    assert runner.invoke(
        cli.app, ["device", "remove", dev["id"]]).exit_code == 0
    r = runner.invoke(cli.app, ["device", "show", dev["id"]])
    assert r.exit_code == 1  # error path exits non-zero


def test_group_peer_profile_events(runner):
    spec = json.dumps({"name": "g1", "mode": "static"})
    r = runner.invoke(cli.app, ["group", "add", spec])
    assert r.exit_code == 0
    assert runner.invoke(cli.app, ["group", "list"]).exit_code == 0
    r = runner.invoke(cli.app, ["peer", "add", json.dumps(
        {"name": "p", "base_url": "https://p.example"})])
    assert r.exit_code == 0
    assert runner.invoke(cli.app, ["event", "list"]).exit_code == 0
    assert runner.invoke(
        cli.app, ["federation", "registry"]).exit_code == 0


def test_backup_service_verbs(runner):
    assert runner.invoke(cli.app, ["backup", "create"]).exit_code == 0
    assert runner.invoke(cli.app, ["backup", "list"]).exit_code == 0
    assert runner.invoke(
        cli.app, ["backup", "validate"]).exit_code == 0
    assert runner.invoke(cli.app, ["service", "stop"]).exit_code == 0
