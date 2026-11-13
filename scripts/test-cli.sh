#!/bin/bash
# Full opal-device CLI exercise: every command group against a freshly
# started managed service. Uses an isolated data dir by default so the
# backup restore step cannot clobber real state. Exits non-zero if any
# command fails.

set -u

export OPAL_DEVICE_DATA_DIR="${OPAL_DEVICE_DATA_DIR:-./data/cli-test}"

PASS=0
FAIL=0

run() {
  echo
  echo "+ opal-device $*"
  if opal-device "$@"; then
    PASS=$((PASS + 1))
  else
    echo "!! FAILED (rc=$?)"
    FAIL=$((FAIL + 1))
  fi
}

# extract a "key": "value" field from JSON on stdin
jqv() {
  grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" \
    | head -n 1 | sed 's/.*:[[:space:]]*"//; s/"$//'
}

wait_ready() {
  for _ in $(seq 1 40); do
    opal-device health >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "service did not become ready"
  exit 1
}

QN="studio/cam1"

# -- service bootstrap -----------------------------------------------------

run service stop
run service start
wait_ready
run service log --lines 5

# -- top level ---------------------------------------------------------------

run --help
run info
run health
run health --ready
run health --live
run catalog

# -- settings ------------------------------------------------------------------

run setting list
run setting set cli.test '"value"'
run setting show cli.test
run setting update '{"cli.test2": 7}'
run setting show cli.test2
run setting remove cli.test
run setting remove cli.test2

# -- scoped defaults -------------------------------------------------------------

run default list
run default set type camera '{"backend": "v4l2"}'
run default list
run default remove type camera

# -- devices ---------------------------------------------------------------------

run device list
run device add '{"name":"cam1","host_id":"host-1","host_name":"studio","type":"camera","backend":"v4l2","access":"local"}'
run device list --type camera --limit 5
run device show "$QN"
run device update "$QN" '{"metadata": {"note": "cli-test"}}'
run device capabilities "$QN"
run device status "$QN"
run device test "$QN"

run device label set "$QN" room a
run device label list "$QN"
run device label remove "$QN" room
run device tag add "$QN" front
run device tag list "$QN"
run device tag remove "$QN" front

# -- groups ------------------------------------------------------------------------

GID=$(opal-device group add '{"name":"g1","mode":"static"}' | jqv id)
run group list
run group show "$GID"
run group update "$GID" '{"description": "cli group"}'
run group member add "$GID" "$QN"
run group member list "$GID"
run group member remove "$GID" "$QN"

DYN=$(opal-device group add '{"name":"dyn","mode":"dynamic","selector":{"type":"camera"}}' | jqv id)
run group show "$DYN"
run group member list "$DYN"
run group remove "$GID"
run group remove "$DYN"

# -- discovery / plugins -------------------------------------------------------------

run discover --dry-run
run discover --plugin host --dry-run
run plugin list
run plugin show host
run plugin test host
run plugin discover host
run plugin disable host
run plugin enable host

# -- events ----------------------------------------------------------------------------

run event list --limit 5
echo
echo "+ opal-device watch   (5s timeout, rc 124 = ok)"
timeout 5 opal-device watch
rc=$?
if [ "$rc" -eq 0 ] || [ "$rc" -eq 124 ]; then
  PASS=$((PASS + 1))
else
  echo "!! FAILED (rc=$rc)"
  FAIL=$((FAIL + 1))
fi

# -- federation ---------------------------------------------------------------------------

run federation inventory
run federation registry

# -- sharing policies -----------------------------------------------------------------------

POL_ALLOW=$(opal-device sharing allow '{"type":"camera"}' | jqv id)
POL_DENY=$(opal-device sharing deny '{"type":"npu"}' | jqv id)
POL_ADD=$(opal-device sharing add '{"effect":"allow","host":"studio"}' | jqv id)
run sharing list
run sharing remove "$POL_ALLOW"
run sharing remove "$POL_DENY"
run sharing remove "$POL_ADD"

# the device must go before self-sync: a trusted peer mirroring this
# service marks our devices as remote-origin (they leave local scope)
run device remove "$QN"

# -- peers -------------------------------------------------------------------------------------

# the peer points at the service itself: sync succeeds in loopback dev
# mode (no OPAL_DEVICE_TOKEN), exercising the real federation path
PID=$(opal-device peer add '{"name":"self-peer","base_url":"http://127.0.0.1:8080","trusted":true}' | jqv id)
run peer list
run peer show "$PID"
run peer update "$PID" '{"name": "self-peer-renamed"}'
run peer status "$PID"
run peer sync "$PID"
run peer sync
run setting set federation.auto_discovery_enabled true
run peer discover
run setting set federation.auto_discovery_enabled false
run peer remove "$PID"

# -- profiles -------------------------------------------------------------------------------------

PRID=$(opal-device profile add '{"name":"cli-profile","description":"smoke","settings":{"federation.default_visibility":"allow_trusted"}}' | jqv id)
run profile list
run profile show "$PRID"
run profile update "$PRID" '{"description": "updated"}'
run profile activate "$PRID"
PRID2=$(opal-device profile add '{"name":"cli-profile-2"}' | jqv id)
run profile remove "$PRID2"

# -- backup ----------------------------------------------------------------------------------------

FNAME=$(opal-device backup create | jqv filename)
BUNDLE=$(opal-device backup export)
run backup list
run backup validate
run backup validate "$BUNDLE"
run backup import "$BUNDLE"
run backup download "$FNAME" --out "$OPAL_DEVICE_DATA_DIR"
run backup restore "$FNAME"
run backup remove "$FNAME"

# -- service shutdown ---------------------------------------------------------------------

run service stop
run service start
wait_ready
run service log --lines 10
run service stop

# -- summary -------------------------------------------------------------------------------------------

echo
echo "=================================="
echo "PASS: $PASS   FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
