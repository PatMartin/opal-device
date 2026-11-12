# OPAL Devices

Implementation of `openapi/opal_device_v1.7.1.yaml` + `openapi/shared/
opal_device_kernel.yaml` and `openapi/shared/opal_kernel.yaml` (OPAL Suite
Kernel 1.7.1) as the authoritative contract — do not edit the contract
files; amendments go upstream first. Python 3.12+ / FastAPI / SQLite.

## Commands

- Setup: `python -m venv .venv && .venv/Scripts/pip install -e .[dev]`
- Test: `.venv/Scripts/pytest`
- Run: `.venv/Scripts/python -m opal_device` or
  `uvicorn opal_device.app:create_app --factory`
- CLI: `opal-device <group> <verb>` (env: `OPAL_DEVICE_URL`,
  `OPAL_DEVICE_TOKEN`)
- Contract audit: `.venv/Scripts/python -m opal_device.contract_audit`
  (`--strict` = pass/fail gate, `--json` = machine-readable)
- Data dir: `OPAL_DEVICE_DATA_DIR` (default `./data`)

## Conventions / design patterns

- Persistence: single SQLite connection in `Store` (`store.py`), all
  statements serialized through `Store._lock`; `store.write_txn()` is the
  unit-of-work context manager for atomic check-and-mutate (nests via
  SAVEPOINT). Aggregates store a JSON `doc` plus indexed columns.
  `isolation_level=None` (autocommit) — `BEGIN IMMEDIATE` never collides
  with implicit transactions left open by non-txn writes.
- DI: handlers inject `StoreDep` (`dependencies.py`).
- DTOs: pydantic v2 models in `schemas.py` (+ module-local models);
  `extra="forbid"` on contract bodies unless the schema allows extras.
- Errors: OpalError envelope everywhere via `ApiError(status, message,
  code=...)` (`errors.py`) + handlers in `app.py` (validation -> 422,
  HTTPException -> matching code, unhandled -> 500).
- Correlation: `CorrelationMiddleware` validates/echoes the four
  `X-Opal-*` headers (printable ASCII 1..128 -> 422), generates missing
  transaction/request ids. Never trust them for authz.
- Concurrency: global config revision -> strong ETag (`"cfg-N"`).
  `require_if_match(request)` (428 missing / 422 malformed) +
  `check_if_match(request, store)` inside `write_txn()` (412 stale) on
  every config mutation; runtime state changes (device test, SSE emits)
  do not bump the revision.
- Auth: `BearerAuthMiddleware` inside correlation. `OPAL_DEVICE_TOKEN` =
  management principal (full access). Peer inbound tokens resolve via
  `OPAL_DEVICE_CRED_<REF>` env matching a registered peer's
  `credential_ref`; a peer principal may read only `/api/v1/federation/*`
  and only when `trusted` — everything else is 403. Unset token +
  loopback = dev mode; `main.py` refuses non-loopback binds without one.
- Qualified device names contain `/`: all `/api/v1/device/*` traffic is
  dispatched in `devices.py` on the raw path — the whole remainder is
  tried as a device id/qualified-name first, then sub-resource suffixes
  (capabilities/status/test/label[/key]/tag[/value]).
- Devices: `device_id` resolves UUID or qualified name. `origin_peer_id`
  marks the federated remote cache; federation inventory serves local
  devices only. `include_stale=false` hides `stale`.
- Filters: `filters.py` `match_device` evaluates the shared DeviceFilter
  (dotted capability paths + comparison expressions) — used by device
  list, dynamic group selectors, and defaults evaluation.
- Groups: `mode` static (member_ids) or dynamic (selector evaluated live);
  member add/remove is static-only (422 otherwise).
- Discovery: `DiscoveryPlugin` protocol + registry in `plugins.py`
  (entry-point group `opal_device.plugins`, builtin `host`).
  `POST /discovery/scan` upserts on qualified_name, `dry_run` computes
  without persisting, per-plugin failures become OpalErrorBody entries.
  `?refresh=true` on device list / group members runs the enabled scan.
- Events: `events.emit()` inside write txns persists rows; `/event/`
  serves cursor history; `/event/stream` is SSE — `id:`/`event:`/`data:`
  lines, `Last-Event-ID` resumes within the retained window (5000 rows),
  expired cursors emit a `replay_expired` guidance event then re-anchor
  live. Poll-based, no external event bus.
- Federation: `peers.py` CRUD (trusted explicit-only; `credential_ref`
  stored but never echoed). `federation.py` outbound sync pulls
  `/federation/inventory` over httpx into the `origin_peer_id` cache,
  retires unseen rows as `stale`; `sync-all` is 202-admitted async;
  `peer/discover` -> 503 while `federation.auto_discovery_enabled=false`
  (no mDNS provisioned -> empty candidate list when enabled).
  `app.state.http_client_factory` injects the outbound transport (tests
  pass a `TestClient` bound to a second app).
- Sharing: `sharing.visible_devices()` — selector-matched policies, deny
  wins over allow and `federation.default_visibility`; untrusted peers
  see nothing.
- Profiles: named settings-map presets; exactly one active; activation
  applies the preset atomically in the same txn.
- Backup: JSON export bundle (settings/profiles/defaults/groups/peers
  sans secrets/policies/local devices); managed archives under
  `data/backups/`; filename whitelist; restore/import under If-Match.
- Service lifecycle: pidfile `data/opal-device.pid`, log
  `data/logs/opal_device.log`; Windows liveness uses
  GetExitCodeProcess; `app.state.service_command` injects the spawn
  command for tests.
- CLI: `cli.py` mirrors x-opal-cli verbs; mutations auto-fetch the ETag
  and send If-Match.

## Contract coverage (verified)

| Area | Status |
|---|---|
| correlation envelope + errors | verified |
| health/info (/, live, ready, ping) | verified |
| settings (map + per-key, If-Match/ETag) | verified |
| devices (CRUD, list+filters, capabilities/status/test) | verified |
| labels + tags sub-resources | verified |
| groups (static + dynamic selector) | verified |
| scoped defaults | verified |
| catalog | verified |
| plugins (list/get/enable/disable/test/discover) | verified |
| discovery scan + upsert + dry_run | verified |
| events history + SSE stream (Last-Event-ID, replay_expired) | verified |
| peers CRUD + status + sync + sync-all + discover | verified |
| sharing policies + deny-wins evaluation | verified |
| federation inventory + registry + peer-token authz | verified |
| profiles CRUD + activate | verified |
| backup export/validate/import + managed archives | verified |
| service lifecycle start/stop/log | verified |
| CLI (opal-device, REST-only) | verified |
| conformance (`contract_audit --strict`: 77/77 ops) | verified |

Deferred (documented): real mDNS peer discovery (empty candidate list
when enabled), non-host discovery backends (pluggable via entry points),
TestClient/httpx deprecation warning in pytest output.
