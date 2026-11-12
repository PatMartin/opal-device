"""Step 14: event history + SSE stream."""

from __future__ import annotations

import json


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _create(client, name="mic0") -> dict:
    return client.post(
        "/api/v1/device/",
        headers={"If-Match": _etag(client)},
        json={
            "name": name, "host_id": "h1", "host_name": "h",
            "type": "microphone", "backend": "alsa", "access": "local",
        },
    ).json()


def test_events_emitted_on_mutations(client):
    dev = _create(client)
    items = client.get("/api/v1/event/").json()["items"]
    assert [e["event"] for e in items] == ["device.registered"]
    assert items[0]["device_id"] == dev["id"]
    client.patch(
        f"/api/v1/device/{dev['id']}",
        headers={"If-Match": _etag(client)},
        json={"metadata": {"k": "v"}},
    )
    client.delete(
        f"/api/v1/device/{dev['id']}", headers={"If-Match": _etag(client)}
    )
    names = [e["event"] for e in client.get("/api/v1/event/").json()["items"]]
    assert names == ["device.registered", "device.updated", "device.removed"]


def test_event_shape_and_filters(client):
    dev = _create(client)
    items = client.get(
        "/api/v1/event/", params={"event": "device.registered"}
    ).json()["items"]
    assert items[0]["id"]
    assert items[0]["time"]
    assert items[0]["data"]["name"] == dev["name"]
    by_dev = client.get(
        "/api/v1/event/", params={"device_id": dev["id"]}
    ).json()["items"]
    assert len(by_dev) == 1
    assert (
        client.get("/api/v1/event/", params={"device_id": "nope"})
        .json()["items"]
        == []
    )


def test_event_cursor_pagination(client):
    dev = _create(client)
    client.post(f"/api/v1/device/{dev['id']}/test")
    client.delete(
        f"/api/v1/device/{dev['id']}", headers={"If-Match": _etag(client)}
    )
    first = client.get("/api/v1/event/", params={"limit": 1}).json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    rest = client.get(
        "/api/v1/event/", params={"cursor": first["next_cursor"]}
    ).json()
    assert len(rest["items"]) >= 1
    assert all(
        i["id"] != first["items"][0]["id"] for i in rest["items"]
    )
    assert client.get(
        "/api/v1/event/", params={"cursor": "bogus"}
    ).status_code == 422


def test_discovery_emits_events(client):
    client.post("/api/v1/discovery/scan", json={})
    names = {
        e["event"] for e in client.get("/api/v1/event/").json()["items"]
    }
    assert "device.registered" in names
    assert "discovery.completed" in names


def test_stream_replays_and_live(client):
    dev = _create(client)
    with client.stream("GET", "/api/v1/event/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = []
        for line in resp.iter_lines():
            if line:
                lines.append(line)
            if line.startswith("data:"):
                break
        body = json.loads(lines[-1][len("data:"):])
        assert body["event"] == "device.registered"
        assert body["device_id"] == dev["id"]
        assert lines[0].startswith("id: ")
        assert lines[1] == "event: device.registered"


def test_stream_last_event_id_resumes(client):
    dev = _create(client)
    client.post(f"/api/v1/device/{dev['id']}/test")
    items = client.get("/api/v1/event/").json()["items"]
    first_id = items[0]["id"]
    with client.stream(
        "GET",
        "/api/v1/event/stream",
        headers={"Last-Event-ID": first_id},
    ) as resp:
        got = []
        for line in resp.iter_lines():
            if line.startswith("data:"):
                got.append(json.loads(line[5:]))
                break
        # replay only events newer than first_id
        assert got[0]["id"] != first_id


def test_stream_invalid_last_event_id(client):
    with client.stream(
        "GET", "/api/v1/event/stream",
        headers={"Last-Event-ID": "nope"},
    ) as resp:
        assert resp.status_code == 422


def test_stream_replay_expired_guidance(client):
    with client.stream(
        "GET", "/api/v1/event/stream",
        headers={"Last-Event-ID": "-999999"},
    ) as resp:
        # no rows exist; fresh client sees no replay_expired
        pass
    dev = _create(client)
    with client.stream(
        "GET", "/api/v1/event/stream",
        headers={"Last-Event-ID": "-999999"},
    ) as resp:
        saw = []
        for line in resp.iter_lines():
            if line:
                saw.append(line)
            if line.startswith("data:"):
                break
        assert saw[0] == "event: replay_expired"
