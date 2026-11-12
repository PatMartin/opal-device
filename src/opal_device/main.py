"""Service entrypoint: `python -m opal_device` or `opal-device serve`."""

from __future__ import annotations

import os

import uvicorn

from . import constants
from .app import create_app
from .auth import is_loopback_host


def main() -> None:
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
