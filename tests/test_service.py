"""Step 21: pidfile-managed service lifecycle + log tail."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path


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


def _wait(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def test_stop_kills_spawned_process_tree(client, tmp_path):
    # Launcher shims (e.g. uv-managed pythons on Windows) spawn the real
    # process as a child: stop must take down the whole tree, not just
    # the pid the launcher reported.
    grandchild_pid_file = tmp_path / "grandchild.pid"
    client.app.state.service_command = [
        sys.executable, "-c",
        "import subprocess,sys,time\n"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(600)'])\n"
        f"open(r'{grandchild_pid_file}','w').write(str(p.pid))\n"
        "time.sleep(600)\n",
    ]
    body = client.post("/api/v1/service/start").json()
    launcher_pid = body["pid"]
    assert _wait(lambda: grandchild_pid_file.exists(), timeout=10)
    grandchild_pid = int(grandchild_pid_file.read_text())

    client.post("/api/v1/service/stop")
    assert _wait_exit(launcher_pid)
    assert _wait_exit(grandchild_pid)


def test_stop_frees_listening_port(client, tmp_path, monkeypatch):
    port = _free_port()
    monkeypatch.setenv("OPAL_DEVICE_SERVICE_PORT", str(port))
    client.app.state.service_command = [
        sys.executable, "-m", "opal_device",
    ]
    body = client.post("/api/v1/service/start").json()
    assert _wait(lambda: _port_open(port), timeout=30)

    # once serving, the pidfile holds the server's own (real) pid —
    # which may differ from the spawned launcher pid on shim pythons
    data_dir = Path(body["log_file"]).parent.parent
    from opal_device.service import PID_NAME, _alive
    real_pid = int((data_dir / PID_NAME).read_text().strip())
    assert _alive(real_pid)

    client.post("/api/v1/service/stop")
    assert _wait(lambda: not _port_open(port), timeout=15)
    assert _wait_exit(real_pid)


def test_main_records_real_pid(tmp_path, monkeypatch):
    import opal_device.main as m

    monkeypatch.setenv("OPAL_DEVICE_DATA_DIR", str(tmp_path))
    for var in ("OPAL_DEVICE_WAIT_PID", "OPAL_DEVICE_HOST",
                "OPAL_DEVICE_PORT", "OPAL_DEVICE_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    called = {}
    monkeypatch.setattr(
        m.uvicorn, "run",
        lambda app, host, port: called.update(host=host, port=port),
    )
    m.main()
    assert called["port"] == 8080
    from opal_device.service import PID_NAME
    pid_file = tmp_path / PID_NAME
    assert int(pid_file.read_text().strip()) == os.getpid()
    pid_file.unlink()
