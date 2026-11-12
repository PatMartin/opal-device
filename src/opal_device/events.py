"""Device/peer change events: persisted history + native SSE stream.

Mutations emit rows through `emit()` inside their write transaction.
The stream replays rows newer than Last-Event-ID (bounded replay
window; an expired cursor yields a `replay_expired` guidance event and
re-anchors live), then polls for new rows. No external opal-event
service required.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .dependencies import StoreDep
from .errors import ApiError
from .store import Store

router = APIRouter()

REPLAY_WINDOW = 5000  # retained rows available for Last-Event-ID resume
POLL_INTERVAL_S = 0.25
HEARTBEAT_EVERY = 8  # polls


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(
    store: Store,
    event: str,
    data: dict[str, Any],
    *,
    device_id: str | None = None,
    peer_id: str | None = None,
) -> int:
    cur = store.execute(
        "INSERT INTO events (event, device_id, peer_id, data, time)"
        " VALUES (?, ?, ?, ?, ?)",
        (event, device_id, peer_id, json.dumps(data), _now()),
    )
    store.execute(
        "DELETE FROM events WHERE id <= ? - ?",
        (cur.lastrowid, REPLAY_WINDOW),
    )
    return int(cur.lastrowid)


def _rows(
    store: Store,
    *,
    after: int = 0,
    device_id: str | None = None,
    event: str | None = None,
) -> list[dict]:
    sql = "SELECT id, event, device_id, peer_id, data, time FROM events WHERE id > ?"
    params: list[Any] = [after]
    if device_id is not None:
        sql += " AND device_id = ?"
        params.append(device_id)
    if event is not None:
        sql += " AND event = ?"
        params.append(event)
    sql += " ORDER BY id"
    return [
        {
            "id": r["id"],
            "event": r["event"],
            "time": r["time"],
            **({"device_id": r["device_id"]} if r["device_id"] else {}),
            **({"peer_id": r["peer_id"]} if r["peer_id"] else {}),
            "data": json.loads(r["data"]),
        }
        for r in store.fetchall(sql, tuple(params))
    ]


def _sse(row: dict) -> str:
    return f"id: {row['id']}\nevent: {row['event']}\ndata: {json.dumps(row)}\n\n"


@router.get("/api/v1/event/")
def event_list(
    store: StoreDep,
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    device_id: str | None = None,
    event: str | None = None,
):
    after = 0
    if cursor is not None:
        try:
            after = int(cursor)
        except ValueError:
            raise ApiError(422, "Invalid cursor", code="invalid_cursor")
    rows = _rows(store, after=after, device_id=device_id, event=event)
    eff = min(limit if limit is not None else 100, 500)
    page = rows[:eff]
    next_cursor = str(page[-1]["id"]) if len(rows) > eff else None
    items = [
        {k: v for k, v in r.items() if k != "id"} | {"id": str(r["id"])}
        for r in page
    ]
    return JSONResponse(content={"items": items, "next_cursor": next_cursor})


@router.get("/api/v1/event/stream")
async def event_stream(
    request: Request,
    store: StoreDep,
    device_id: str | None = None,
    event: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    after = 0
    replay_expired = False
    if last_event_id is not None:
        try:
            after = int(last_event_id)
        except ValueError:
            raise ApiError(
                422, "Invalid Last-Event-ID", code="invalid_last_event_id"
            )
        oldest = store.fetchone("SELECT MIN(id) AS m FROM events")
        if oldest and oldest["m"] is not None and after < int(oldest["m"]) - 1:
            replay_expired = True

    async def gen() -> AsyncIterator[str]:
        nonlocal after
        if replay_expired:
            yield (
                "event: replay_expired\n"
                'data: {"code":"replay_expired","message":"Last-Event-ID is '
                'older than the retained replay window; re-anchored to live '
                'stream"}\n\n'
            )
        last = after
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            rows = _rows(
                store, after=last, device_id=device_id, event=event
            )
            for row in rows:
                yield _sse(row)
                last = row["id"]
            if rows:
                idle = 0
            else:
                idle += 1
                if idle % HEARTBEAT_EVERY == 0:
                    yield ": heartbeat\n\n"
            await asyncio.sleep(POLL_INTERVAL_S)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
