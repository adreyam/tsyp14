"""REST/JSON adapters for the Twin and Guardian.

Every response is validated against the canonical schemas. A single bad item
in a readings batch is rejected (and reported) without discarding the rest;
a malformed command ack or verdict list is a hard PartnerError, because the
Brain must never act on a half-understood answer.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from pydantic import TypeAdapter, ValidationError

from ..config import PartnerConfig
from ..schemas import (BrainEvent, CityDescriptor, CommandAck, PowerReading, SwitchCommand,
                       SwitchStatus, TrustVerdict)
import json

from .base import PartnerError, ReadingsBatch
from .mapping import remap, remap_city
from .signing import sign, verify


def _fmt(err: ValidationError) -> str:
    parts = []
    for e in err.errors()[:3]:
        loc = ".".join(str(x) for x in e["loc"]) or "<root>"
        parts.append(f"{loc}: {e['msg']}")
    more = f" (+{err.error_count() - 3} more)" if err.error_count() > 3 else ""
    return "; ".join(parts) + more


class _RestClient:
    partner = "partner"

    def __init__(self, cfg: PartnerConfig, client: Optional[httpx.Client] = None):
        headers = {"Accept": "application/json", "User-Agent": "energy-brain/1.0"}
        if cfg.api_key:
            headers[cfg.auth_header] = cfg.api_key
        self._http = client or httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s)
        self._http.headers.update(headers)
        self.secret = cfg.signing_secret
        self.fmap = cfg.field_map or {}

    def _call(self, method: str, path: str, op: str, **kw) -> Any:
        if self.secret:
            body = json.dumps(kw.pop("json"), separators=(",", ":")).encode() if "json" in kw else b""
            if body:
                kw["content"] = body
            kw["headers"] = {**kw.get("headers", {}), **sign(self.secret, body),
                             **({"Content-Type": "application/json"} if body else {})}
        try:
            r = self._http.request(method, path, **kw)
        except httpx.TimeoutException:
            raise PartnerError(self.partner, op, "timed out")
        except httpx.HTTPError as exc:
            raise PartnerError(self.partner, op, f"unreachable ({exc.__class__.__name__}: {exc})")
        if r.status_code in (401, 403):
            raise PartnerError(self.partner, op, f"authentication refused (HTTP {r.status_code}) -- check api_key",
                               retryable=False)
        if r.status_code >= 400:
            raise PartnerError(self.partner, op, f"HTTP {r.status_code}: {r.text[:200]}",
                               retryable=r.status_code >= 500)
        if self.secret:
            bad = verify(self.secret, r.content, r.headers.get("x-timestamp"), r.headers.get("x-signature"))
            if bad:
                raise PartnerError(self.partner, op, f"response refused: {bad}", retryable=False)
        try:
            return r.json()
        except ValueError:
            raise PartnerError(self.partner, op, "response is not valid JSON", retryable=False)

    def close(self) -> None:
        self._http.close()


class RestTwin(_RestClient):
    partner = "twin"
    _status_list = TypeAdapter(list[SwitchStatus])

    def get_city(self) -> CityDescriptor:
        data = remap_city(self._call("GET", "/energy/city", "get_city"), self.fmap.get("city"))
        try:
            return CityDescriptor.model_validate(data)
        except ValidationError as e:
            raise PartnerError(self.partner, "get_city", f"invalid city descriptor: {_fmt(e)}", retryable=False)

    def get_readings(self) -> ReadingsBatch:
        data = remap(self._call("GET", "/energy/readings", "get_readings"), self.fmap.get("readings"),
                     PowerReading, many=True)
        if not isinstance(data, list):
            raise PartnerError(self.partner, "get_readings", "expected a JSON array of power_reading", False)
        batch = ReadingsBatch()
        for item in data:
            try:
                batch.valid.append(PowerReading.model_validate(item))
            except ValidationError as e:
                batch.rejected.append((item, _fmt(e)))
        return batch

    def get_switches(self) -> list[SwitchStatus]:
        data = remap(self._call("GET", "/energy/switches", "get_switches"), self.fmap.get("switches"),
                     SwitchStatus, many=True)
        try:
            return self._status_list.validate_python(data)
        except ValidationError as e:
            raise PartnerError(self.partner, "get_switches", f"invalid switch status: {_fmt(e)}", retryable=False)

    def send_command(self, cmd: SwitchCommand) -> CommandAck:
        data = remap(self._call("POST", f"/energy/switches/{cmd.switch_id}/command", "send_command",
                                json=cmd.model_dump(mode="json")), self.fmap.get("ack"), CommandAck, many=False)
        try:
            ack = CommandAck.model_validate(data)
        except ValidationError as e:
            raise PartnerError(self.partner, "send_command", f"invalid ack: {_fmt(e)}", retryable=False)
        if ack.command_id != cmd.command_id:
            raise PartnerError(self.partner, "send_command",
                               f"ack is for '{ack.command_id}', expected '{cmd.command_id}'", retryable=False)
        return ack


class RestGuardian(_RestClient):
    partner = "guardian"
    _verdicts = TypeAdapter(list[TrustVerdict])

    def get_verdicts(self, subject_ids: list[str]) -> list[TrustVerdict]:
        if not subject_ids:
            return []
        data = remap(self._call("GET", "/verdicts", "get_verdicts", params=[("subject", s) for s in subject_ids]),
                     self.fmap.get("verdicts"), TrustVerdict, many=True)
        try:
            return self._verdicts.validate_python(data)
        except ValidationError as e:
            raise PartnerError(self.partner, "get_verdicts", f"invalid verdicts: {_fmt(e)}", retryable=False)

    def report(self, event: BrainEvent) -> None:
        self._call("POST", "/events", "report", json=event.model_dump(mode="json"))
