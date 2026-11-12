"""Step 20: backup export/validate/import + managed archives."""

from __future__ import annotations

import json


def _etag(client) -> str:
    return client.get("/api/v1/setting/").headers["etag"]


def _device(client, name="mic"):
    return client.post(
        "/api/v1/device/",
        headers={"If-Match": _etag(client)},
        json={
            "name": name, "host_id": "h1", "host_name": "h",
            "type": "microphone", "backend": "alsa", "access": "local",
        },
    ).json()


def test_export_shape(client):
    _device(client)
    body = client.get("/api/v1/backup/export").json()
    assert body["format_version"]
    assert body["exported_at"]
    assert isinstance(body["settings"], dict)
    assert len(body["devices"]) == 1
    assert "peers" in body and "profiles" in body


def test_validate_post_and_get(client):
    resp = client.post(
        "/api/v1/backup/validate",
        json={"format_version": "1.0.0", "devices": []},
    )
    assert resp.json()["valid"] is True
    bad = client.post("/api/v1/backup/validate", json={"devices": "x"})
    body = bad.json()
    assert body["valid"] is False
    assert body["errors"]
    # GET validates the live config
    assert client.get("/api/v1/backup/validate").json()["valid"] is True


def test_import_round_trip(client):
    dev = _device(client)
    bundle = client.get("/api/v1/backup/export").json()
    # wipe the device
    client.delete(
        f"/api/v1/device/{dev['id']}", headers={"If-Match": _etag(client)}
    )
    assert client.get("/api/v1/device/").json()["total"] == 0
    resp = client.post(
        "/api/v1/backup/import",
        headers={"If-Match": _etag(client)},
        json=bundle,
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["counts"]["devices"] == 1
    assert client.get("/api/v1/device/").json()["total"] == 1


def test_import_requires_if_match_and_valid_bundle(client):
    bundle = client.get("/api/v1/backup/export").json()
    assert client.post(
        "/api/v1/backup/import", json=bundle
    ).status_code == 428
    assert client.post(
        "/api/v1/backup/import",
        headers={"If-Match": _etag(client)},
        json={"devices": "nope"},
    ).status_code == 422


def test_managed_archive_lifecycle(client):
    _device(client)
    created = client.post("/api/v1/backup/create")
    assert created.status_code == 201
    filename = created.json()["filename"]
    assert filename.endswith(".json")

    items = client.get("/api/v1/backup/list").json()
    assert [i["filename"] for i in items] == [filename]
    assert items[0]["size_bytes"] > 0

    dl = client.get(f"/api/v1/backup/download/{filename}")
    assert dl.status_code == 200
    doc = json.loads(dl.content)
    assert doc["format_version"]
    assert len(doc["devices"]) == 1

    # wipe + restore
    store_doc = doc["devices"][0]
    client.delete(
        f"/api/v1/device/{store_doc['id']}",
        headers={"If-Match": _etag(client)},
    )
    resp = client.post(
        f"/api/v1/backup/restore/{filename}",
        headers={"If-Match": _etag(client)},
    )
    assert resp.status_code == 200
    assert resp.json()["backup_filename"] == filename
    assert client.get("/api/v1/device/").json()["total"] == 1

    assert client.delete(f"/api/v1/backup/{filename}").status_code == 204
    assert client.get("/api/v1/backup/list").json() == []


def test_backup_filename_safety(client):
    # traversal is rejected — either normalized away (404) or refused (422)
    assert client.get(
        "/api/v1/backup/download/%2E%2E/x.json"
    ).status_code in (404, 422)
    assert client.delete("/api/v1/backup/nope.json").status_code == 404
    assert client.get(
        "/api/v1/backup/download/evil"
    ).status_code == 422


def test_restore_requires_if_match(client):
    client.post("/api/v1/backup/create")
    filename = client.get("/api/v1/backup/list").json()[0]["filename"]
    resp = client.post(f"/api/v1/backup/restore/{filename}")
    assert resp.status_code == 428
