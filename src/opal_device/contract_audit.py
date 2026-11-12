"""Contract audit: verify the mounted app covers the authoritative spec.

Resolves path-level $refs across the shared kernel files, collects every
contract operation (method + path + operationId), then checks each is
served by a FastAPI route. The raw-path device dispatcher
(`/api/v1/device/{rest:path}`) counts for all /device/* contract paths.

Usage:
    python -m opal_device.contract_audit [--strict] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import yaml

SPEC_ROOT = Path(__file__).resolve().parents[2] / "openapi"
MAIN_SPEC = SPEC_ROOT / "opal_device_v1.7.1.yaml"

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def contract_operations() -> list[dict]:
    main = _load(MAIN_SPEC)
    ops = []
    for path, item in main["paths"].items():
        if not isinstance(item, dict):
            continue
        ref = item.get("$ref")
        if ref:
            file_part, frag = ref.split("#")
            target = _load(SPEC_ROOT / file_part)
            for key in frag.split("/")[1:]:
                target = target[key]
            item = target
        for method, op in item.items():
            if method in HTTP_METHODS and isinstance(op, dict):
                ops.append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "operation_id": op.get("operationId"),
                    }
                )
    return ops


def app_routes() -> dict[str, set[str]]:
    os.environ.setdefault(
        "OPAL_DEVICE_DATA_DIR", tempfile.mkdtemp(prefix="opal-audit-")
    )
    from .app import create_app

    routes: dict[str, set[str]] = {}
    stack = list(create_app().routes)
    while stack:
        r = stack.pop()
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", None)
        if methods and path:
            routes.setdefault(path, set()).update(m for m in methods)
        else:
            inner = getattr(r, "routes", None)
            if inner is None and hasattr(r, "original_router"):
                inner = r.original_router.routes
            stack.extend(inner or [])
    return routes


def _normalize(path: str) -> str:
    # Starlette converters like {target:path} mount the same contract path
    import re
    return re.sub(r"\{([A-Za-z_]+):[^}]+\}", r"{\1}", path)


def _covered(op: dict, routes: dict[str, set[str]]) -> bool:
    path, method = op["path"], op["method"]
    normalized = {_normalize(p): m for p, m in routes.items()}
    if method in normalized.get(path, set()):
        return True
    # device raw-path dispatcher covers all /api/v1/device/* contract paths
    if path.startswith("/api/v1/device/"):
        return method in routes.get("/api/v1/device/{rest:path}", set())
    return False


def audit() -> dict:
    ops = contract_operations()
    routes = app_routes()
    missing = [op for op in ops if not _covered(op, routes)]
    mounted_paths = {_normalize(op["path"]) for op in ops}
    extra = sorted(
        p for p in routes
        if p.startswith("/api/v1")
        and _normalize(p) not in mounted_paths
        and p != "/api/v1/device/{rest:path}"
    )
    return {
        "total_operations": len(ops),
        "covered": len(ops) - len(missing),
        "missing": missing,
        "extra_paths": extra,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="opal-device-audit")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"CONFORMANT: {report['covered']}/{report['total_operations']}"
            f" contract operations mounted"
        )
        for op in report["missing"]:
            print(
                f"  MISSING {op['method']} {op['path']}"
                f" ({op['operation_id']})"
            )
        for p in report["extra_paths"]:
            print(f"  EXTRA   {p}")
    if args.strict and (report["missing"] or report["extra_paths"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
