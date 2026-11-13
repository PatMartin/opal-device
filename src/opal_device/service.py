"""Service lifecycle: pidfile-managed background server process.

`start` launches the server detached (restarting any prior instance),
writing output to `logs/opal_device.log` under the data dir (truncated
unless `append`); `stop` terminates the recorded pid's process tree —
not an error when nothing is running; `log` tails the log file.

The pidfile may initially hold the spawned launcher's pid; the server
overwrites it with its own real pid at startup (`main.py`), since
launcher shims can spawn the interpreter as a separate child process.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .dependencies import StoreDep
from .errors import ApiError
from .store import Store

router = APIRouter()

LOG_NAME = "opal_device.log"
PID_NAME = "opal-device.pid"


class StartOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    append: bool = False


def _data_dir(store: Store) -> Path:
    return Path(store.path).parent


def _log_path(store: Store) -> Path:
    d = _data_dir(store) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / LOG_NAME


def _pid_path(store: Store) -> Path:
    return _data_dir(store) / PID_NAME


def _read_pid(store: Store) -> int | None:
    try:
        return int(_pid_path(store).read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) keeps succeeding on terminated-but-unreaped
        # pids on Windows; ask the kernel for the real exit status.
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(
            handle, ctypes.byref(code)
        )
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate(pid: int) -> None:
    # Kill the whole tree: launcher shims (e.g. uv-managed pythons on
    # Windows) spawn the real interpreter as a child, so signalling the
    # recorded pid alone can orphan the actual server.
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=10, check=False,
            )
            return
        except OSError:
            pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
            return
        except OSError:
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _terminate_later(pid: int, delay: float = 0.5) -> None:
    # Self-termination must be deferred so the HTTP response can flush
    # before this process dies.
    import threading
    import time

    def _run() -> None:
        time.sleep(delay)
        _terminate(pid)

    threading.Thread(target=_run, daemon=True).start()


def pid_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / PID_NAME


def record_own_pid(data_dir: Path) -> Path:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_path_for(data_dir)
    pid_file.write_text(str(os.getpid()))
    return pid_file


def release_own_pid(pid_file: Path) -> None:
    try:
        if int(Path(pid_file).read_text().strip()) == os.getpid():
            Path(pid_file).unlink()
    except (OSError, ValueError):
        pass


def _status(state: str, pid: int | None, log_file: Path,
            previous_pid: int | None = None) -> dict:
    return {
        "state": state,
        "pid": pid,
        "previous_pid": previous_pid,
        "log_file": str(log_file),
    }


@router.post("/api/v1/service/start")
def service_start(
    request: Request,
    store: StoreDep,
    body: StartOptions | None = Body(default=None),
):
    log_file = _log_path(store)
    append = bool(body and body.append)
    previous_pid: int | None = None
    self_restart = False
    pid = _read_pid(store)
    if pid is not None and _alive(pid):
        previous_pid = pid
        if pid == os.getpid():
            # self-restart: spawn the replacement first, then exit
            # after this response has been sent
            self_restart = True
        else:
            _terminate(pid)

    command = getattr(request.app.state, "service_command", None) or [
        sys.executable, "-m", "opal_device",
    ]
    env = dict(os.environ)
    env["OPAL_DEVICE_DATA_DIR"] = str(_data_dir(store))
    if previous_pid is not None:
        # restart: child waits for the old process to release the port
        env["OPAL_DEVICE_WAIT_PID"] = str(previous_pid)
    service_port = os.environ.get("OPAL_DEVICE_SERVICE_PORT")
    if service_port:
        env["OPAL_DEVICE_PORT"] = service_port
    flags = {} if os.name == "nt" else {"start_new_session": True}
    mode = "ab" if append else "wb"
    log_handle = open(log_file, mode)
    proc = subprocess.Popen(
        command, stdout=log_handle, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=env, **flags,
    )
    log_handle.close()
    _pid_path(store).write_text(str(proc.pid))
    if self_restart:
        _terminate_later(os.getpid())
    return JSONResponse(
        content=_status("running", proc.pid, log_file, previous_pid)
    )


@router.post("/api/v1/service/stop")
def service_stop(store: StoreDep):
    log_file = _log_path(store)
    pid = _read_pid(store)
    if pid is not None and _alive(pid):
        if pid == os.getpid():
            _terminate_later(pid)
        else:
            _terminate(pid)
    try:
        _pid_path(store).unlink()
    except OSError:
        pass
    return JSONResponse(content=_status("stopped", None, log_file))


@router.get("/api/v1/service/log")
def service_log(
    store: StoreDep,
    lines: int = Query(default=10, ge=1, le=1000),
):
    log_file = _log_path(store)
    if not log_file.exists():
        raise ApiError(404, f"Log file not found: {log_file}")
    tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    return JSONResponse(
        content={"log_file": str(log_file), "lines": tail[-lines:]}
    )
