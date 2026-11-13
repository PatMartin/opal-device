"""Service entrypoint: `python -m opal_device` or `opal-device serve`."""

from __future__ import annotations

import atexit
import os
from pathlib import Path

import uvicorn

from . import constants
from .app import create_app
from .auth import is_loopback_host
from .service import _alive, record_own_pid, release_own_pid


def _wait_for_exit(pid: int, timeout: float = 30.0) -> None:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _alive(pid):
            return
        time.sleep(0.05)


def main() -> None:
    wait_pid = os.environ.get("OPAL_DEVICE_WAIT_PID")
    if wait_pid:
        _wait_for_exit(int(wait_pid))
    data_dir = Path(
        os.environ.get(constants.ENV_DATA_DIR, constants.DEFAULT_DATA_DIR)
    )
    try:
        pid_file = record_own_pid(data_dir)
    except OSError:
        pid_file = None
    if pid_file is not None:
        atexit.register(release_own_pid, pid_file)
    host = os.environ.get(constants.ENV_HOST, "127.0.0.1")
    port = int(os.environ.get(constants.ENV_PORT, "8080"))
    if not os.environ.get(constants.ENV_TOKEN) and not is_loopback_host(host):
        raise SystemExit(
            f"Refusing to bind non-loopback host {host!r} without "
            f"{constants.ENV_TOKEN} set"
        )
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
