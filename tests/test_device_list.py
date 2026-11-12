"""Step 6: GET /api/v1/device/ filters + cursor pagination."""

from __future__ import annotations

import json
import uuid

import pytest


def _store(client):
    return client.app.state.store


def _seed(client, **kw) -> dict:
    doc = {
        "id": str(uuid.uuid4()),
        "name": kw.pop("name", "dev"),
        "qualified_name": kw.pop("qualified_name", "h/dev"),
        "host_id": kw.pop("host_id", "h1"),
        "host_name": kw.pop("host_name", "h"),
        "type": kw.pop("type", "cpu"),
        "backend": kw.pop("backend", "os"),
        "status": kw.pop("status", "available"),
        "capabilities": kw.pop("capabilities", {}),
        "labels": kw.pop("labels", {}),
        "tags": kw.pop("tags", []),
        "access": kw.pop("access", "local"),
    }
    doc.update(kw)
    qn = doc.pop("qualified_name")
    _store(client).execute(
        "INSERT INTO devices (id, qualified_name, name, host_id, host_name,"
        " type, backend, status, origin_peer_id, doc) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            doc["id"], qn, doc["name"], doc["host_id"], doc["host_name"],
            doc["type"], doc["backend"], doc["status"],
            doc.get("origin_peer_id"), json.dumps(doc),
        ),
    )
    return doc


def test_empty_list(client):
    resp = client.get("/api/v1/device/")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "next_cursor": None, "total": 0}
    assert "etag" in resp.headers


def test_list_returns_all(client):
    _seed(client, name="a", qualified_name="h/a")
    _seed(client, name="b", qualified_name="h/b")
    body = client.get("/api/v1/device/").json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_filter_type(client):
    _seed(client, name="c", qualified_name="h/c", type="camera")
    _seed(client, name="g", qualified_name="h/g", type="gpu")
    body = client.get("/api/v1/device/?type=gpu").json()
    assert [i["name"] for i in body["items"]] == ["g"]


def test_filter_status_and_include_stale(client):
    _seed(client, name="ok", qualified_name="h/ok", status="available")
    _seed(client, name="old", qualified_name="h/old", status="stale")
    assert client.get("/api/v1/device/?status=stale").json()["total"] == 1
    assert (
        client.get("/api/v1/device/?include_stale=false").json()["total"] == 1
    )
    assert client.get("/api/v1/device/").json()["total"] == 2


def test_filter_host_backend_peer(client):
    _seed(client, name="a", qualified_name="h/a", host_name="node1",
          host_id="nid-1", backend="alsa")
    _seed(client, name="b", qualified_name="h/b", host_name="node2",
          backend="cuda", origin_peer_id=str(uuid.uuid4()))
    assert client.get("/api/v1/device/?host=node1").json()["total"] == 1
    assert client.get("/api/v1/device/?host=nid-1").json()["total"] == 1
    assert client.get("/api/v1/device/?backend=cuda").json()["total"] == 1


def test_filter_peer_id(client):
    pid = str(uuid.uuid4())
    _seed(client, name="a", qualified_name="h/a", origin_peer_id=pid)
    _seed(client, name="b", qualified_name="h/b")
    body = client.get(f"/api/v1/device/?peer_id={pid}").json()
    assert [i["name"] for i in body["items"]] == ["a"]


def test_filter_tag_and_label(client):
    _seed(client, name="a", qualified_name="h/a", tags=["gpu-pool", "rack1"],
          labels={"zone": "east"})
    _seed(client, name="b", qualified_name="h/b", tags=["rack1"],
          labels={"zone": "west"})
    assert client.get("/api/v1/device/?tag=gpu-pool").json()["total"] == 1
    body = client.get("/api/v1/device/?label=zone%3Dwest").json()
    assert [i["name"] for i in body["items"]] == ["b"]


def test_label_malformed_422(client):
    resp = client.get("/api/v1/device/?label=noequals")
    assert resp.status_code == 422


def test_capability_expressions(client):
    _seed(
        client, name="big", qualified_name="h/big",
        capabilities={
            "compute": {"memory_mb": 16384},
            "audio": {"sample_rates": [44100, 48000]},
        },
    )
    _seed(
        client, name="small", qualified_name="h/small",
        capabilities={"compute": {"memory_mb": 4096}},
    )
    assert (
        client.get("/api/v1/device/?capability=compute.memory_mb%3E%3D8192")
        .json()["total"]
        == 1
    )
    assert (
        client.get(
            "/api/v1/device/?capability=audio.sample_rates%20contains%2048000"
        ).json()["total"]
        == 1
    )
    assert (
        client.get("/api/v1/device/?min_memory_mb=8192").json()["total"] == 1
    )
    assert (
        client.get("/api/v1/device/?min_memory_mb=20000").json()["total"] == 0
    )


def test_invalid_enum_params_422(client):
    assert client.get("/api/v1/device/?type=hologram").status_code == 422
    assert client.get("/api/v1/device/?status=wobbly").status_code == 422


def test_group_filter(client):
    a = _seed(client, name="a", qualified_name="h/a")
    _seed(client, name="b", qualified_name="h/b")
    group_doc = {
        "id": str(uuid.uuid4()),
        "name": "statics",
        "mode": "static",
        "member_ids": [a["id"]],
    }
    _store(client).execute(
        "INSERT INTO groups (id, name, mode, doc) VALUES (?,?,?,?)",
        (group_doc["id"], group_doc["name"], "static", json.dumps(group_doc)),
    )
    body = client.get("/api/v1/device/?group=statics").json()
    assert [i["name"] for i in body["items"]] == ["a"]
    assert client.get("/api/v1/device/?group=nosuch").json()["items"] == []


def test_dynamic_group_filter(client):
    _seed(client, name="a", qualified_name="h/a", type="gpu")
    _seed(client, name="b", qualified_name="h/b", type="cpu")
    group_doc = {
        "id": str(uuid.uuid4()),
        "name": "gpus",
        "mode": "dynamic",
        "selector": {"type": "gpu"},
        "member_ids": [],
    }
    _store(client).execute(
        "INSERT INTO groups (id, name, mode, doc) VALUES (?,?,?,?)",
        (group_doc["id"], group_doc["name"], "dynamic", json.dumps(group_doc)),
    )
    body = client.get("/api/v1/device/?group=gpus").json()
    assert [i["name"] for i in body["items"]] == ["a"]


def test_pagination_round_trip(client):
    for i in range(5):
        _seed(client, name=f"d{i}", qualified_name=f"h/d{i}")
    r1 = client.get("/api/v1/device/?limit=2").json()
    assert len(r1["items"]) == 2 and r1["total"] == 5
    assert r1["next_cursor"]
    r2 = client.get(
        f"/api/v1/device/?limit=2&cursor={r1['next_cursor']}"
    ).json()
    r3 = client.get(
        f"/api/v1/device/?limit=2&cursor={r2['next_cursor']}"
    ).json()
    seen = r1["items"] + r2["items"] + r3["items"]
    assert len(seen) == 5
    assert len({d["id"] for d in seen}) == 5  # no overlap
    assert r3["next_cursor"] is None


def test_limit_bounds(client):
    _seed(client, name="a", qualified_name="h/a")
    assert client.get("/api/v1/device/?limit=0").status_code == 422
    assert client.get("/api/v1/device/?limit=-1").status_code == 422
    assert client.get("/api/v1/device/?limit=9999").status_code == 200


def test_invalid_cursor_422(client):
    assert client.get("/api/v1/device/?cursor=garbage!!").status_code == 422
    assert client.get("/api/v1/device/?cursor=bm9wZQ==").status_code == 422


def test_refresh_param_accepted(client):
    _seed(client, name="a", qualified_name="h/a")
    resp = client.get("/api/v1/device/?refresh=true")
    assert resp.status_code == 200
    # refresh runs enabled plugins; the builtin host plugin adds cpus0
    assert resp.json()["total"] == 2


def test_combined_filters(client):
    _seed(client, name="a", qualified_name="h/a", type="gpu", backend="cuda",
          tags=["x"], labels={"z": "1"})
    _seed(client, name="b", qualified_name="h/b", type="gpu", backend="rocm")
    body = client.get(
        "/api/v1/device/?type=gpu&backend=cuda&tag=x&label=z%3D1"
    ).json()
    assert [i["name"] for i in body["items"]] == ["a"]
