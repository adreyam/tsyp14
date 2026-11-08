"""Loads and validates brain/config/brain.yaml. A wrong setting stops the Brain at start with a clear message."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .contracts import AXES, ZONES, explain_validation

BRAIN_DIR = Path(__file__).resolve().parents[2]          # brain/
DEFAULT_CONFIG = BRAIN_DIR / "config" / "brain.yaml"


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrainCfg(_C):
    host: str = "127.0.0.1"
    port: int = Field(default=8500, ge=1, le=65535)
    token_env: str = "BRAIN_TOKEN"
    token_default: Optional[str] = None
    max_body_bytes: int = Field(default=262144, ge=1024)

    def token(self) -> Optional[str]:
        return os.environ.get(self.token_env) or self.token_default


class TwinCfg(_C):
    host: str = "127.0.0.1"
    port: Union[int, Literal["auto"]] = "auto"
    port_file: str = "/tmp/city_ground_zero_api_port"
    default_port: int = 8820
    timeout_s: float = Field(default=3.0, gt=0)
    poll_s: float = Field(default=1.0, gt=0)
    incidents_poll_s: float = Field(default=2.0, gt=0)
    heartbeat_s: float = Field(default=15.0, gt=0)
    families: List[str] = Field(default_factory=list)
    deadband: Dict[str, float] = Field(default_factory=lambda: {"default": 0.5})

    def resolve_port(self) -> int:
        """Same lookup as the Twin's own MCP server (twin/tools/mcp/city_mcp_server.py)."""
        if isinstance(self.port, int):
            return self.port
        text = os.environ.get("CITY_SIM_API_PORT", "")
        if text.isdigit() and int(text) > 0:
            return int(text)
        try:
            value = Path(self.port_file).read_text().strip()
            if value.isdigit() and int(value) > 0:
                return int(value)
        except OSError:
            pass
        return self.default_port


class GuardianCfg(_C):
    mode: Literal["mock", "http", "off"] = "mock"
    url: str = "http://127.0.0.1:8300"
    verdict_path: str = "/v1/trust-verdict"
    token_env: str = "GUARDIAN_TOKEN"
    timeout_s: float = Field(default=1.5, gt=0)
    on_missing: Literal["degraded", "ignore", "trust"] = "degraded"


class FallbackCfg(_C):
    auto_switch_to_mock: bool = False
    after_s: float = Field(default=20, gt=0)


class PolicyCfg(_C):
    cooldown_s: float = Field(default=20, ge=0)
    resource_lock_s: float = Field(default=45, ge=0)
    axis_priority: List[str] = Field(default_factory=lambda: list(AXES))
    protected_zones: List[str] = Field(default_factory=list)

    @field_validator("axis_priority")
    @classmethod
    def all_axes(cls, v: List[str]) -> List[str]:
        if sorted(v) != sorted(AXES):
            raise ValueError(f"axis_priority must list each of {list(AXES)} exactly once")
        return v

    @field_validator("protected_zones")
    @classmethod
    def zones(cls, v: List[str]) -> List[str]:
        bad = [z for z in v if z not in ZONES]
        if bad:
            raise ValueError(f"unknown zones {bad} (zones are A..P)")
        return v


class ScenarioCfg(_C):
    twin_camera: bool = True          # while a scenario runs, tint its zones and point the Twin's camera at it (spotlight.py)


class LogsCfg(_C):
    dir: str = "../logs"
    keep_in_memory: int = Field(default=4000, ge=100)


class Config(_C):
    source: Literal["live", "mock"] = "mock"
    brain: BrainCfg = BrainCfg()
    twin: TwinCfg = TwinCfg()
    guardian: GuardianCfg = GuardianCfg()
    fallback: FallbackCfg = FallbackCfg()
    policy: PolicyCfg = PolicyCfg()
    agents: Dict[str, Dict[str, object]] = Field(default_factory=dict)
    logs: LogsCfg = LogsCfg()
    scenario: ScenarioCfg = ScenarioCfg()
    path: Optional[str] = None

    def scenario_twin_camera(self) -> bool:
        return self.scenario.twin_camera

    def logs_dir(self) -> Path:
        base = Path(self.path).parent if self.path else BRAIN_DIR / "config"
        d = (base / self.logs.dir).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def agent(self, axis: str) -> Dict[str, object]:
        return dict(self.agents.get(axis, {}))


class ConfigError(Exception):
    pass


def load_config(path: Optional[str] = None, overrides: Optional[dict] = None) -> Config:
    p = Path(path or os.environ.get("CITY_BRAIN_CONFIG") or DEFAULT_CONFIG)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}")
    except yaml.YAMLError as e:
        raise ConfigError(f"config file {p} is not valid YAML: {e}")
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {p} must contain a mapping at the top level")
    env_source = os.environ.get("CITY_BRAIN_SOURCE")
    if env_source:
        raw["source"] = env_source
    for k, v in (overrides or {}).items():
        raw[k] = v
    raw["path"] = str(p.resolve())
    try:
        return Config(**raw)
    except ValidationError as e:
        raise ConfigError(f"invalid config {p}: {explain_validation(e, 'config')}")
