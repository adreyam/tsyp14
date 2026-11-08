"""Config-driven reshaping of a partner's JSON into our canonical messages.

Phase 2 pairs us with an unknown Twin/Guardian. If their messages carry the same
information under other names or nesting, a `field_map` in config translates
them, so no code changes. Example (twin.field_map in config.yaml):

  readings:
    path: data.meters                 # where the list is in their response
    fields:                           # canonical field: their field (dot path) | "=constant"
      sensor_id: meter_id
      zone_id: location.zone
      value: power_kw
      unit: "=kW"
      ts: time
      seq: counter
    ts_format: epoch_s                # iso (default) | epoch_s | epoch_ms
  switches:
    fields: {switch_id: name, state: position}
    values: {state: {ON: closed, OFF: open}}
  city:
    zones: {path: zones, fields: {zone_id: id, capacity_kw: max_kw}}
    switches: {path: breakers, fields: {switch_id: id}}

Canonical fields that are not mapped are copied when the partner uses the same name.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

_MISSING = object()


def get_path(obj: Any, path: str) -> Any:
    if path == "" or path == ".":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _ts(v: Any, fmt: str) -> Any:
    if fmt == "epoch_s":
        return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
    if fmt == "epoch_ms":
        return datetime.fromtimestamp(float(v) / 1000, tz=timezone.utc).isoformat()
    return v


def map_item(item: Any, spec: dict, canonical: list[str]) -> Any:
    if not isinstance(item, dict):
        return item
    fields: dict = spec.get("fields", {})
    values: dict = spec.get("values", {})
    out: dict = {}
    for name in canonical:
        src = fields.get(name)
        if src is None:
            v = item.get(name, _MISSING)
        elif isinstance(src, str) and src.startswith("="):
            v = src[1:]
        else:
            v = get_path(item, src)
        if v is _MISSING:
            continue
        if name in values and isinstance(values[name], dict):
            v = values[name].get(str(v), v)
        if name in ("ts", "expires_at") and spec.get("ts_format"):
            v = _ts(v, spec["ts_format"])
        out[name] = v
    return out


def remap(data: Any, spec: Optional[dict], model, many: bool) -> Any:
    """Apply `spec` (or nothing) and return data shaped for `model` (list of items if many)."""
    if not spec:
        return data
    if "path" in spec:
        data = get_path(data, spec["path"])
        if data is _MISSING:
            return None
    canonical = [n for n in model.model_fields if n != "type"]
    if many:
        return [map_item(x, spec, canonical) for x in data] if isinstance(data, list) else data
    return map_item(data, spec, canonical)


def remap_city(data: Any, spec: Optional[dict]) -> Any:
    from ..schemas import Switch, Zone
    if not spec:
        return data
    root = get_path(data, spec["path"]) if "path" in spec else data
    out = {"city_id": get_path(root, spec.get("city_id", "city_id"))}
    if out["city_id"] is _MISSING:
        out["city_id"] = "unknown-city"
    for key, model in (("zones", Zone), ("switches", Switch)):
        sub = spec.get(key, {"path": key})
        items = get_path(root, sub.get("path", key))
        out[key] = [] if items is _MISSING else remap(items, {k: v for k, v in sub.items() if k != "path"} or None,
                                                         model, many=True) if sub.get("fields") or sub.get("values") else items
    return out
