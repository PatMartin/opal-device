"""Step 21: pidfile-managed service lifecycle + log tail."""

from __future__ import annotations

import sys
import time


def _sleep_cmd(client):
    client.app.state.service_command = [
        sys.executable, "-c",
        "import time; print('ready', flush=True); time.sleep(60)",
    ]


def _wait_exit(pid, timeout=5.0):
    from opal_device.service import _alive

    end = time.time() + timeout
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_start_stop_cycle(client):
    _sleep_cmd(client)
    resp = client.post("/api/v1/service/start", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "running"
    pid = body["pid"]
    assert pid
    assert body["log_file"].endswith("opal_device.log")

    stop = client.post("/api/v1/service/stop").json()
    assert stop["state"] == "stopped"
    assert stop["pid"] is None
    assert _wait_exit(pid)


def test_start_replaces_running_instance(client):
    _sleep_cmd(client)
    first = client.post("/api/v1/service/start").json()
    second = client.post("/api/v1/service/start").json()
    assert second["previous_pid"] == first["pid"]
    assert second["pid"] != first["pid"]
    assert _wait_exit(first["pid"])
    client.post("/api/v1/service/stop")
    assert _wait_exit(second["pid"])


def test_stop_when_nothing_running_is_ok(client):
    body = client.post("/api/v1/service/stop").json()
    assert body["state"] == "stopped"
    assert body["pid"] is None


def test_log_tail(client):
    _sleep_cmd(client)
    client.post("/api/v1/service/start")
    time.sleep(1.0)  # let the child flush its banner
    log_file = client.post("/api/v1/service/stop").json()["log_file"]
    # the child printed 'ready' before sleeping
    for _ in range(40):
        resp = client.get("/api/v1/service/log?lines=5")
        if resp.status_code == 200 and resp.json()["lines"]:
            break
        time.sleep(0.05)
    body = client.get("/api/v1/service/log?lines=5").json()
    assert body["log_file"] == log_file
    assert any("ready" in line for line in body["lines"])


def test_log_missing_404(client):
    assert client.get("/api/v1/service/log").status_code == 404
