"""Service entrypoint: `python -m opal_device` or `opal-device serve`."""

from __future__ import annotations

import os

import uvicorn

from . import constants
from .app import create_app


def main() -> None:
    host = os.environ.get(constants.ENV_HOST, "127.0.0.1")
    port = int(os.environ.get(constants.ENV_PORT, "8080"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
