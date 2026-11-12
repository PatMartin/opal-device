"""REST-only `opal-device` CLI (Typer + httpx).

All commands call the mounted HTTP API; nothing reaches into the store.
Connection: --url/--token or OPAL_DEVICE_URL / OPAL_DEVICE_TOKEN env.
Mutations auto-fetch the current ETag and send If-Match.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
import typer

app = typer.Typer(add_completion=False, no_args_is_help=True)

DEFAULT_URL = "http://127.0.0.1:8000"
API = "/api/v1"


class Client:
    def __init__(self, url: str, token: str | None):
        self.c = httpx.Client(base_url=url.rstrip("/"), timeout=30.0)
        if token:
            self.c.headers["Authorization"] = f"Bearer {token}"

    def etag(self) -> str:
        r = self.c.get(f"{API}/setting/")
        r.raise_for_status()
        return r.headers["etag"]

    def req(self, method: str, path: str, *, mutate: bool = False,
            **kw) -> Any:
        if mutate:
            kw.setdefault("headers", {})["If-Match"] = self.etag()
        r = self.c.request(method, f"{API}{path}", **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
                err = body.get("error", {})
                typer.echo(
                    f"error {r.status_code} {err.get('code')}: "
                    f"{err.get('message')}", err=True,
                )
            except Exception:
                typer.echo(f"error {r.status_code}", err=True)
            raise typer.Exit(1)
        return r


def _client(ctx: typer.Context) -> Client:
    return ctx.obj["client"]


def _out(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2))


@app.callback()
def main(
    ctx: typer.Context,
    url: str = typer.Option(
        None, "--url", envvar="OPAL_DEVICE_URL",
        help="Service base URL",
    ),
    token: str = typer.Option(
        None, "--token", envvar="OPAL_DEVICE_TOKEN",
        help="Bearer token",
    ),
):
    ctx.obj = {"client": Client(url or DEFAULT_URL, token)}


def _json_or_error(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        typer.echo("invalid JSON argument", err=True)
        raise typer.Exit(2)


# -- top-level verbs ------------------------------------------------------------


@app.command()
def info(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/info/").json())


@app.command()
def health(ctx: typer.Context, ready: bool = False, live: bool = False):
    path = "/health/ready" if ready else "/health/live" if live else "/health/"
    _out(_client(ctx).req("GET", path).json())


@app.command()
def catalog(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/catalog").json())


@app.command()
def discover(
    ctx: typer.Context,
    plugin: list[str] = typer.Option(None, "--plugin"),
    network: bool = typer.Option(False, "--network"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    body = {
        "plugin_ids": plugin or None,
        "include_network": network,
        "dry_run": dry_run,
    }
    _out(_client(ctx).req("POST", "/discovery/scan", json=body).json())


@app.command()
def watch(ctx: typer.Context, event: str = typer.Option(None)):
    """Stream events (SSE)."""
    c = _client(ctx)
    params = {"event": event} if event else {}
    with c.c.stream("GET", f"{API}/event/stream", params=params) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            typer.echo(line)


# -- device ---------------------------------------------------------------------

device_app = typer.Typer(no_args_is_help=True)
app.add_typer(device_app, name="device")


@device_app.command("list")
def device_list(
    ctx: typer.Context,
    type: str = typer.Option(None),
    status: str = typer.Option(None),
    host: str = typer.Option(None),
    backend: str = typer.Option(None),
    tag: str = typer.Option(None),
    label: str = typer.Option(None),
    group: str = typer.Option(None),
    refresh: bool = typer.Option(False),
    limit: int = typer.Option(None),
    cursor: str = typer.Option(None),
):
    params = {k: v for k, v in {
        "type": type, "status": status, "host": host, "backend": backend,
        "tag": tag, "label": label, "group": group,
        "refresh": refresh or None, "limit": limit, "cursor": cursor,
    }.items() if v is not None}
    _out(_client(ctx).req("GET", "/device/", params=params).json())


@device_app.command("add")
def device_add(ctx: typer.Context, spec: str = typer.Argument(
    ..., help="Device JSON body")):
    _out(_client(ctx).req(
        "POST", "/device/", json=_json_or_error(spec), mutate=True
    ).json())


@device_app.command("show")
def device_show(ctx: typer.Context, device_id: str):
    _out(_client(ctx).req("GET", f"/device/{device_id}").json())


@device_app.command("update")
def device_update(
    ctx: typer.Context, device_id: str,
    patch: str = typer.Argument(..., help="Patch JSON body"),
):
    _out(_client(ctx).req(
        "PATCH", f"/device/{device_id}", json=_json_or_error(patch),
        mutate=True,
    ).json())


@device_app.command("remove")
def device_remove(ctx: typer.Context, device_id: str):
    _client(ctx).req("DELETE", f"/device/{device_id}", mutate=True)


@device_app.command("capabilities")
def device_caps(ctx: typer.Context, device_id: str):
    _out(_client(ctx).req("GET", f"/device/{device_id}/capabilities").json())


@device_app.command("status")
def device_status(ctx: typer.Context, device_id: str):
    _out(_client(ctx).req("GET", f"/device/{device_id}/status").json())


@device_app.command("test")
def device_test(ctx: typer.Context, device_id: str):
    _out(_client(ctx).req("POST", f"/device/{device_id}/test").json())


label_app = typer.Typer(no_args_is_help=True)
tag_app = typer.Typer(no_args_is_help=True)


@label_app.command("list")
def label_list(ctx: typer.Context, device_id: str):
    _out(_client(ctx).req("GET", f"/device/{device_id}/label").json())


@label_app.command("set")
def label_set(ctx: typer.Context, device_id: str, key: str, value: str):
    _out(_client(ctx).req(
        "PUT", f"/device/{device_id}/label/{key}",
        json={"value": value}, mutate=True,
    ).json())


@label_app.command("remove")
def label_remove(ctx: typer.Context, device_id: str, key: str):
    _client(ctx).req(
        "DELETE", f"/device/{device_id}/label/{key}", mutate=True
    )


@tag_app.command("list")
def tag_list(ctx: typer.Context, device_id: str):
    _out(_client(ctx).req("GET", f"/device/{device_id}/tag").json())


@tag_app.command("add")
def tag_add(ctx: typer.Context, device_id: str, tag: str):
    _out(_client(ctx).req(
        "POST", f"/device/{device_id}/tag",
        json={"tag": tag}, mutate=True,
    ).json())


@tag_app.command("remove")
def tag_remove(ctx: typer.Context, device_id: str, tag: str):
    _client(ctx).req(
        "DELETE", f"/device/{device_id}/tag/{tag}", mutate=True
    )


device_app.add_typer(label_app, name="label")
device_app.add_typer(tag_app, name="tag")


# -- group ----------------------------------------------------------------------

group_app = typer.Typer(no_args_is_help=True)
member_app = typer.Typer(no_args_is_help=True)
app.add_typer(group_app, name="group")
group_app.add_typer(member_app, name="member")


@group_app.command("list")
def group_list(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/group/").json())


@group_app.command("add")
def group_add(ctx: typer.Context, spec: str):
    _out(_client(ctx).req(
        "POST", "/group/", json=_json_or_error(spec), mutate=True
    ).json())


@group_app.command("show")
def group_show(ctx: typer.Context, group_id: str):
    _out(_client(ctx).req("GET", f"/group/{group_id}").json())


@group_app.command("update")
def group_update(ctx: typer.Context, group_id: str, patch: str):
    _out(_client(ctx).req(
        "PATCH", f"/group/{group_id}", json=_json_or_error(patch),
        mutate=True,
    ).json())


@group_app.command("remove")
def group_remove(ctx: typer.Context, group_id: str):
    _client(ctx).req("DELETE", f"/group/{group_id}", mutate=True)


@member_app.command("list")
def member_list(ctx: typer.Context, group_id: str):
    _out(_client(ctx).req("GET", f"/group/{group_id}/member").json())


@member_app.command("add")
def member_add(ctx: typer.Context, group_id: str, device_id: str):
    _client(ctx).req(
        "PUT", f"/group/{group_id}/member/{device_id}", mutate=True
    )


@member_app.command("remove")
def member_remove(ctx: typer.Context, group_id: str, device_id: str):
    _client(ctx).req(
        "DELETE", f"/group/{group_id}/member/{device_id}", mutate=True
    )


# -- generic CRUD-style groups --------------------------------------------------


def _simple_group(name: str, base: str):
    g = typer.Typer(no_args_is_help=True)

    @g.command("list")
    def _list(ctx: typer.Context):
        _out(_client(ctx).req("GET", f"{base}/").json())

    @g.command("add")
    def _add(ctx: typer.Context, spec: str):
        _out(_client(ctx).req(
            "POST", f"{base}/", json=_json_or_error(spec), mutate=True
        ).json())

    @g.command("show")
    def _show(ctx: typer.Context, item_id: str):
        _out(_client(ctx).req("GET", f"{base}/{item_id}").json())

    @g.command("update")
    def _update(ctx: typer.Context, item_id: str, patch: str):
        _out(_client(ctx).req(
            "PATCH", f"{base}/{item_id}", json=_json_or_error(patch),
            mutate=True,
        ).json())

    @g.command("remove")
    def _remove(ctx: typer.Context, item_id: str):
        _client(ctx).req("DELETE", f"{base}/{item_id}", mutate=True)

    app.add_typer(g, name=name)
    return g


peer_app = _simple_group("peer", "/peer")


@peer_app.command("status")
def peer_status(ctx: typer.Context, peer_id: str):
    _out(_client(ctx).req("GET", f"/peer/{peer_id}/status").json())


@peer_app.command("sync")
def peer_sync(ctx: typer.Context, peer_id: str = typer.Argument(None)):
    path = f"/peer/{peer_id}/sync" if peer_id else "/peer/sync"
    _out(_client(ctx).req("POST", path).json())


@peer_app.command("discover")
def peer_discover(ctx: typer.Context):
    _out(_client(ctx).req("POST", "/peer/discover").json())


profile_app = _simple_group("profile", "/profile")


@profile_app.command("activate")
def profile_activate(ctx: typer.Context, profile_id: str):
    _out(_client(ctx).req(
        "POST", f"/profile/{profile_id}/activate", mutate=True
    ).json())


# -- settings / defaults --------------------------------------------------------

setting_app = typer.Typer(no_args_is_help=True)
app.add_typer(setting_app, name="setting")


@setting_app.command("list")
def setting_list(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/setting/").json())


@setting_app.command("show")
def setting_show(ctx: typer.Context, key: str):
    _out(_client(ctx).req("GET", f"/setting/{key}").json())


@setting_app.command("set")
def setting_set(ctx: typer.Context, key: str, value: str):
    _out(_client(ctx).req(
        "PUT", f"/setting/{key}", json={"value": _json_or_error(value)},
        mutate=True,
    ).json())


@setting_app.command("update")
def setting_update(ctx: typer.Context, patch: str):
    _out(_client(ctx).req(
        "PATCH", "/setting/", json=_json_or_error(patch), mutate=True
    ).json())


@setting_app.command("remove")
def setting_remove(ctx: typer.Context, key: str):
    _client(ctx).req("DELETE", f"/setting/{key}", mutate=True)


default_app = typer.Typer(no_args_is_help=True)
app.add_typer(default_app, name="default")


@default_app.command("list")
def default_list(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/default/").json())


@default_app.command("set")
def default_set(ctx: typer.Context, scope: str, target: str, values: str):
    _out(_client(ctx).req(
        "PUT", f"/default/{scope}/{target}",
        json={"scope": scope, "target": target,
              "values": _json_or_error(values)},
        mutate=True,
    ).json())


@default_app.command("remove")
def default_remove(ctx: typer.Context, scope: str, target: str):
    _client(ctx).req(
        "DELETE", f"/default/{scope}/{target}", mutate=True
    )


# -- sharing / plugin / event / federation --------------------------------------

sharing_app = typer.Typer(no_args_is_help=True)
app.add_typer(sharing_app, name="sharing")


@sharing_app.command("list")
def sharing_list(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/sharing/policy/").json())


@sharing_app.command("add")
def sharing_add(ctx: typer.Context, spec: str):
    _out(_client(ctx).req(
        "POST", "/sharing/policy/", json=_json_or_error(spec),
        mutate=True,
    ).json())


@sharing_app.command("allow")
def sharing_allow(ctx: typer.Context, spec: str):
    body = _json_or_error(spec)
    body["effect"] = "allow"
    _out(_client(ctx).req(
        "POST", "/sharing/policy/", json=body, mutate=True
    ).json())


@sharing_app.command("deny")
def sharing_deny(ctx: typer.Context, spec: str):
    body = _json_or_error(spec)
    body["effect"] = "deny"
    _out(_client(ctx).req(
        "POST", "/sharing/policy/", json=body, mutate=True
    ).json())


@sharing_app.command("remove")
def sharing_remove(ctx: typer.Context, policy_id: str):
    _client(ctx).req(
        "DELETE", f"/sharing/policy/{policy_id}", mutate=True
    )


plugin_app = typer.Typer(no_args_is_help=True)
app.add_typer(plugin_app, name="plugin")


@plugin_app.command("list")
def plugin_list(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/plugin/").json())


@plugin_app.command("show")
def plugin_show(ctx: typer.Context, plugin_id: str):
    _out(_client(ctx).req("GET", f"/plugin/{plugin_id}").json())


for verb in ("enable", "disable", "test", "discover"):
    def _make(v):
        def _cmd(ctx: typer.Context, plugin_id: str,
                 body: str = typer.Argument("{}")):
            payload = _json_or_error(body) if v == "discover" else None
            _out(_client(ctx).req(
                "POST", f"/plugin/{plugin_id}/{v}",
                json=payload, mutate=(v != "test" and v != "discover"),
            ).json())
        return _cmd
    plugin_app.command(verb)(_make(verb))


event_app = typer.Typer(no_args_is_help=True)
app.add_typer(event_app, name="event")


@event_app.command("list")
def event_list(ctx: typer.Context, device_id: str = typer.Option(None),
               event: str = typer.Option(None),
               limit: int = typer.Option(None)):
    params = {k: v for k, v in {
        "device_id": device_id, "event": event, "limit": limit,
    }.items() if v is not None}
    _out(_client(ctx).req("GET", "/event/", params=params).json())


federation_app = typer.Typer(no_args_is_help=True)
app.add_typer(federation_app, name="federation")


@federation_app.command("inventory")
def fed_inventory(ctx: typer.Context, cursor: str = typer.Option(None)):
    params = {"cursor": cursor} if cursor else {}
    _out(_client(ctx).req(
        "GET", "/federation/inventory", params=params
    ).json())


@federation_app.command("registry")
def fed_registry(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/federation/registry").json())


# -- backup / service -----------------------------------------------------------

backup_app = typer.Typer(no_args_is_help=True)
app.add_typer(backup_app, name="backup")


@backup_app.command("list")
def backup_list(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/backup/list").json())


@backup_app.command("create")
def backup_create(ctx: typer.Context):
    _out(_client(ctx).req("POST", "/backup/create").json())


@backup_app.command("export")
def backup_export(ctx: typer.Context):
    _out(_client(ctx).req("GET", "/backup/export").json())


@backup_app.command("download")
def backup_download(ctx: typer.Context, filename: str, out: str = "."):
    r = _client(ctx).req("GET", f"/backup/download/{filename}")
    with open(os.path.join(out, filename), "wb") as f:
        f.write(r.content)
    typer.echo(filename)


@backup_app.command("restore")
def backup_restore(ctx: typer.Context, filename: str):
    _out(_client(ctx).req(
        "POST", f"/backup/restore/{filename}", mutate=True
    ).json())


@backup_app.command("import")
def backup_import(ctx: typer.Context, bundle: str):
    _out(_client(ctx).req(
        "POST", "/backup/import", json=_json_or_error(bundle),
        mutate=True,
    ).json())


@backup_app.command("validate")
def backup_validate(ctx: typer.Context, bundle: str = typer.Argument(None)):
    if bundle is None:
        _out(_client(ctx).req("GET", "/backup/validate").json())
    else:
        _out(_client(ctx).req(
            "POST", "/backup/validate", json=_json_or_error(bundle)
        ).json())


@backup_app.command("remove")
def backup_delete(ctx: typer.Context, filename: str):
    _client(ctx).req("DELETE", f"/backup/{filename}")


service_app = typer.Typer(no_args_is_help=True)
app.add_typer(service_app, name="service")


@service_app.command("start")
def service_start(ctx: typer.Context,
                  append: bool = typer.Option(False, "--append")):
    _out(_client(ctx).req(
        "POST", "/service/start", json={"append": append}
    ).json())


@service_app.command("stop")
def service_stop(ctx: typer.Context):
    _out(_client(ctx).req("POST", "/service/stop").json())


@service_app.command("log")
def service_log(ctx: typer.Context, lines: int = typer.Option(10)):
    body = _client(ctx).req(
        "GET", "/service/log", params={"lines": lines}
    ).json()
    for line in body["lines"]:
        typer.echo(line)


if __name__ == "__main__":
    app()
