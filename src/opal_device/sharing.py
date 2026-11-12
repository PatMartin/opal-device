"""Sharing policies: metadata visibility rules for trusted peers.

Policies govern inventory metadata visibility only. Default visibility
comes from `federation.default_visibility` (allow_trusted | deny_trusted);
a policy matches when every selector it sets agrees (peer_id, device_id,
host, type); any matching deny wins over allow and default.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .dependencies import StoreDep
from .errors import ApiError
from .events import emit
from .precondition import check_if_match, require_if_match
from .schemas import SharingPolicyCreate
from .settings import merged_settings
from .store import Store

router = APIRouter()


def all_policies(store: Store) -> list[dict]:
    rows = store.fetchall("SELECT doc FROM sharing_policies ORDER BY id")
    return [json.loads(r["doc"]) for r in rows]


def _find(store: Store, policy_id: str) -> dict | None:
    row = store.fetchone(
        "SELECT doc FROM sharing_policies WHERE id = ?", (policy_id,)
    )
    return json.loads(row["doc"]) if row else None


def _matches(policy: dict, peer_id: str, device: dict) -> bool:
    if policy.get("peer_id") and policy["peer_id"] != peer_id:
        return False
    if policy.get("device_id") and policy["device_id"] != device.get("id"):
        return False
    if policy.get("host") and policy["host"] not in (
        device.get("host_id"), device.get("host_name"),
    ):
        return False
    if policy.get("type") and policy["type"] != device.get("type"):
        return False
    return True


def visible_devices(store: Store, peer: dict, devices: list[dict]) -> list[dict]:
    """Devices a trusted peer may see under the current policy set."""
    if not peer.get("trusted"):
        return []
    default = merged_settings(store).get(
        "federation.default_visibility", "allow_trusted"
    )
    policies = all_policies(store)
    out = []
    for dev in devices:
        denied = any(
            p["effect"] == "deny" and _matches(p, peer["id"], dev)
            for p in policies
        )
        if denied:
            continue
        if default == "deny_trusted":
            allowed = any(
                p["effect"] == "allow" and _matches(p, peer["id"], dev)
                for p in policies
            )
            if not allowed:
                continue
        out.append(dev)
    return out


# -- routes ---------------------------------------------------------------------


@router.get("/api/v1/sharing/policy/")
def list_policies(store: StoreDep):
    default = merged_settings(store).get(
        "federation.default_visibility", "allow_trusted"
    )
    return JSONResponse(
        content={
            "default_visibility": default,
            "items": all_policies(store),
        },
        headers={"ETag": store.etag()},
    )


@router.post("/api/v1/sharing/policy/", status_code=201)
def add_policy(request: Request, store: StoreDep, body: SharingPolicyCreate):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc: dict = {"id": str(uuid.uuid4()), "effect": body.effect}
        for f in ("peer_id", "device_id", "host", "type", "description"):
            v = getattr(body, f)
            if v is not None:
                doc[f] = v
        store.execute(
            "INSERT INTO sharing_policies (id, doc) VALUES (?, ?)",
            (doc["id"], json.dumps(doc)),
        )
        emit(store, "sharing_policy.created", doc)
        store.bump_revision()
    return JSONResponse(
        status_code=201,
        content=doc,
        headers={"Location": f"/api/v1/sharing/policy/{doc['id']}"},
    )


@router.delete("/api/v1/sharing/policy/{policy_id}", status_code=204)
def delete_policy(policy_id: str, request: Request, store: StoreDep):
    require_if_match(request)
    with store.write_txn():
        check_if_match(request, store)
        doc = _find(store, policy_id)
        if doc is None:
            raise ApiError(404, f"Sharing policy not found: {policy_id}")
        store.execute(
            "DELETE FROM sharing_policies WHERE id = ?", (doc["id"],)
        )
        emit(store, "sharing_policy.removed", doc)
        store.bump_revision()
    return Response(status_code=204)
