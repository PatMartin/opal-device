"""Preferred-device defaults keyed by (scope, target).

Consumers resolve precedence explicit > device > type > host > global;
the registry just stores the scoped value maps.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from .dependencies import StoreDep
from .errors import ApiError
from .precondition import check_if_match, require_if_match

router = APIRouter()

SCOPES = ("global", "host", "device", "type")


class DefaultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str
    target: str
    values: dict[str, Any]


def _check_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ApiError(
            422, f"Invalid default scope: {scope}", code="validation_error"
        )


@router.get("/api/v1/default/")
def list_defaults(store: StoreDep):
    rows = store.fetchall(
        "SELECT scope, target, value_map AS v FROM defaults ORDER BY scope, target"
    )
    items = [
        {"scope": r["scope"], "target": r["target"], "values": json.loads(r["v"])}
        for r in rows
    ]
    return JSONResponse(content={"items": items})


@router.put("/api/v1/default/{scope}/{target:path}")
def set_default(
    scope: str, target: str, request: Request, store: StoreDep, body: DefaultBody
):
    _check_scope(scope)
    if body.scope != scope or body.target != target:
        raise ApiError(
            422,
            "Request body scope/target must match the path",
            code="validation_error",
        )
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        store.execute(
            "INSERT INTO defaults (scope, target, value_map) VALUES (?, ?, ?) "
            "ON CONFLICT(scope, target) DO UPDATE SET value_map = "
            "excluded.value_map",
            (scope, target, json.dumps(body.values)),
        )
        store.bump_revision()
        return JSONResponse(
            content={"scope": scope, "target": target, "values": body.values},
            headers={"ETag": store.etag()},
        )


@router.delete("/api/v1/default/{scope}/{target:path}")
def remove_default(scope: str, target: str, request: Request, store: StoreDep):
    _check_scope(scope)
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        cur = store.execute(
            "DELETE FROM defaults WHERE scope = ? AND target = ?",
            (scope, target),
        )
        if cur.rowcount == 0:
            raise ApiError(
                404, f"Default not found: {scope}/{target}", code="not_found"
            )
        store.bump_revision()
    return Response(status_code=204)
