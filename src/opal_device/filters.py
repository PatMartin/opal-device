"""Shared declarative DeviceFilter evaluation.

Used by the device list query parameters and by dynamic group
selectors — the contract defines one DeviceFilter shape for both.
Capability expressions look like `audio.sample_rates contains 48000`
or `compute.memory_mb >= 8192`.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import ApiError

_MISSING = object()

_EXPR_RE = re.compile(
    r"^([^\s<>=!]+?)\s*(contains|>=|<=|!=|==|>|<)\s*(.+)$"
)


def _lookup(mapping: Any, dotted: str) -> Any:
    cur = mapping
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _coerce(raw: str) -> Any:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1]
    for conv in (int, float):
        try:
            return conv(s)
        except ValueError:
            pass
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    return s


def eval_capability(capabilities: dict, expr: str) -> bool:
    """Evaluate one capability comparison expression; False on mismatch."""
    m = _EXPR_RE.match(expr.strip())
    if not m:
        return False
    path, op, raw = m.groups()
    actual = _lookup(capabilities or {}, path)
    if actual is _MISSING:
        return False
    val = _coerce(raw)
    if op == "contains":
        if isinstance(actual, str):
            return str(val) in actual
        if isinstance(actual, (list, tuple)):
            return val in actual
        return False
    if op in ("=", "=="):
        if actual == val:
            return True
        return str(actual) == str(val)
    if op == "!=":
        return not eval_capability(capabilities, f"{path} = {raw}")
    try:
        a, b = float(actual), float(val)
    except (TypeError, ValueError):
        return False
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def parse_label_param(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise ApiError(
            422,
            "Invalid label filter: expected key=value",
            code="invalid_label_filter",
        )
    key, _, value = raw.partition("=")
    if not key:
        raise ApiError(
            422,
            "Invalid label filter: expected key=value",
            code="invalid_label_filter",
        )
    return key, value


def match_device(device: dict, filt: dict) -> bool:
    """True when the device satisfies every present DeviceFilter key."""
    if filt.get("type") is not None and device.get("type") != filt["type"]:
        return False
    if filt.get("status") is not None and device.get("status") != filt["status"]:
        return False
    if filt.get("host") is not None and filt["host"] not in (
        device.get("host_id"),
        device.get("host_name"),
    ):
        return False
    if filt.get("backend") is not None and device.get("backend") != filt["backend"]:
        return False
    if filt.get("peer_id") is not None and device.get("origin_peer_id") != filt["peer_id"]:
        return False
    tags = set(device.get("tags") or [])
    if any(t not in tags for t in filt.get("tags_all") or []):
        return False
    labels = device.get("labels") or {}
    for k, v in (filt.get("labels") or {}).items():
        if labels.get(k) != v:
            return False
    caps = device.get("capabilities") or {}
    for expr in filt.get("capabilities") or []:
        if not eval_capability(caps, expr):
            return False
    return True
