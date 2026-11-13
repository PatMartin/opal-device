"""Service entrypoint: `python -m opal_device` or `opal-device serve`."""

from __future__ import annotations

import os

import uvicorn

from . import constants
from .app import create_app
from .auth import is_loopback_host


def _wait_for_exit(pid: int, timeout: float = 30.0) -> None:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)


def main() -> None:
    wait_pid = os.environ.get("OPAL_DEVICE_WAIT_PID")
    if wait_pid:
        _wait_for_exit(int(wait_pid))
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
