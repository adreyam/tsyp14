"""Configuration: partner addresses, secrets and tuning live here, never in code.

Values of the form ${ENV_VAR} or ${ENV_VAR:-default} are substituted from the
environment, so secrets never get committed. Point the Brain at a different
partner by editing config.yaml or setting env vars -- no code change.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .schemas import CityDescriptor

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(value):
    if isinstance(value, str):
        def sub(m):
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"config references ${{{name}}} but the environment variable is not set")
        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class C(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PartnerConfig(C):
    adapter: Literal["rest", "mcp"] = "rest"
    base_url: str
    api_key: Optional[str] = None
    auth_header: str = "X-API-Key"
    timeout_s: float = Field(default=3.0, gt=0)
    mcp_tools: Optional[dict[str, str]] = Field(
        default=None, description="MCP adapter only: our operation -> the partner's tool name")
    field_map: Optional[dict[str, dict]] = Field(
        default=None, description="per message (city/readings/switches/verdicts/ack): reshape a partner's JSON "
                                  "into ours without code; see adapters/mapping.py")
    signing_secret: Optional[str] = Field(
        default=None, description="HMAC-SHA256 secret: sign what we send (Twin) / require signed answers (Guardian)")


class TrustPolicy(C):
    verdict_ttl_s: float = Field(default=10.0, gt=0)
    degraded_min_consistent_samples: int = Field(default=3, ge=1)
    flap_changes: int = Field(default=4, ge=2, description="verdict changes within flap_window_s = flapping")
    flap_window_s: float = Field(default=20.0, gt=0)


class ForecastConfig(C):
    mode: Literal["predictive", "reactive"] = "predictive"   # reactive = baseline for comparison only
    model: Literal["adaptive", "trend"] = Field(default="adaptive", description="adaptive: weighted ensemble of "
                                                "trend windows + J-1 shape, calibrated band | trend: v0.6 single fit")
    adaptive_windows: list[int] = Field(default=[10, 20, 40], min_length=3, max_length=3)
    error_half_life: float = Field(default=60.0, gt=0, description="adaptive: forgetting of expert errors, samples")
    calibration_samples: int = Field(default=200, ge=20, description="adaptive: realized errors kept for calibration")
    window_samples: int = Field(default=20, ge=3, description="samples in the rolling trend fit")
    horizon_s: float = Field(default=45.0, gt=0)
    warmup_samples: int = Field(default=10, ge=2)
    band_sigma: float = Field(default=2.0, ge=0)
    risk_persistence: int = Field(default=3, ge=1)
    l0_alert_ratio: float = Field(default=1.3, gt=1, description="L0 zones: alert when forecast > ratio x own mean")
    clear_ratio: float = Field(default=0.97, gt=0, le=1, description="hysteresis: a risk clears only when the "
                               "forecast is below clear_ratio x capacity for risk_persistence samples")


class IngestConfig(C):
    stale_s: float = Field(default=10.0, gt=0, description="reading older than this is rejected")
    max_step_frac: float = Field(default=0.2, gt=0, description="jump > frac x capacity (or level) per sample is suspect")
    suspect_confirm_samples: int = Field(default=5, ge=1, description="trusted samples at the new level before accepting a jump")
    max_future_s: float = Field(default=5.0, ge=0, description="readings timestamped further in the future are rejected")
    plan_deviation_frac: float = Field(default=0.25, gt=0, description="deviation from the J-1 plan that is reported")
    plan_deviation_samples: int = Field(default=10, ge=1)


class ActionConfig(C):
    ack_timeout_s: float = Field(default=5.0, gt=0)
    verify_tolerance: float = Field(default=0.25, gt=0, le=1)
    switch_cooldown_s: float = Field(default=60.0, ge=0)
    switching_cost: float = Field(default=0.02, ge=0)
    priority_weight: float = Field(default=3.0, ge=1)
    priority_headroom: float = Field(default=0.85, gt=0, le=1,
                                     description="a priority zone may receive load only while staying below this x capacity")
    max_send_attempts: int = Field(default=2, ge=1)
    shed_above: float = Field(default=1.10, ge=1, description="last-resort shedding only if expected peak >= this x capacity")
    shed_penalty: float = Field(default=0.05, ge=0)
    alert_min_p: float = Field(default=0.5, ge=0, le=1, description="'no option' operator alerts only while the "
                               "do-nothing P(overload) is at least this (no alerts for risks already handled)")
    planner: bool = Field(default=True, description="multi-zone coordination: search plans of several tie "
                          "toggles (make-before-break) when single actions cannot solve the risk")
    max_plan_steps: int = Field(default=3, ge=1, le=4)
    plan_wait_max_s: float = Field(default=15.0, ge=0, description="a plan may wait this long for a switch in "
                                   "cooldown rather than fall back to a worse action (e.g. shedding)")
    restore_enabled: bool = True
    restore_margin: float = Field(default=0.85, gt=0, le=1, description="restore only if every zone stays <= this x capacity")
    restore_persistence: int = Field(default=10, ge=1, description="consecutive safe samples before restoring")
    restore_min_shed_s: float = Field(default=90.0, ge=0, description="a shed zone stays off at least this long; "
                                      "doubled after every failed re-energization (recloser-style back-off)")


class ConsistencyConfig(C):
    enabled: bool = True
    abs_tol_kw: float = Field(default=15.0, gt=0, description="power-balance residual tolerated, kW...")
    rel_tol: float = Field(default=0.03, ge=0, description="...or this fraction of the meter reading, whichever is larger")
    smoothing: float = Field(default=0.4, gt=0, le=1, description="EMA factor on the residual")
    persistence: int = Field(default=5, ge=1, description="samples above tolerance before flagging")
    clear_samples: int = Field(default=10, ge=1, description="samples below half the tolerance before clearing")
    cusum: bool = Field(default=True, description="CUSUM test for slow drifts (on top of the level rule)")
    cusum_k: float = Field(default=0.5, gt=0, description="CUSUM slack, in residual sigmas")
    cusum_h: float = Field(default=5.0, gt=0, description="CUSUM threshold, in residual sigmas")


class LearningConfig(C):
    enabled: bool = True
    confirm_s: float = Field(default=10.0, ge=0, description="an observation is kept only if its sensors stay trusted this long")
    min_observations: int = Field(default=2, ge=1)
    min_shift_frac: float = Field(default=0.05, gt=0, description="ignore load shifts smaller than this x zone load")
    fraction_tolerance: float = Field(default=0.08, gt=0)


class InboundConfig(C):
    api_key: Optional[str] = None
    max_hint_ttl_s: float = Field(default=900.0, gt=0)
    rate_per_min: int = Field(default=30, ge=1)


class DayAheadConfig(C):
    file: Optional[str] = Field(default=None, description="J-1 load profile per zone (JSON), see dayahead.py")
    offset_s: float = Field(default=0.0, description="time of day at Brain start, s")


class OrchestratorConfig(C):
    api_key: Optional[str] = Field(default=None, description="key the orchestrator must send to the agent's MCP endpoint")
    start_mode: Literal["autonomous", "advisory"] = "autonomous"
    notify_url: Optional[str] = Field(default=None, description="webhook: important events are POSTed here")
    notify_kinds: list[str] = ["risk_detected", "decision", "gate_blocked", "proposal", "operator_alert", "plan",
                               "consistency",
                               "actuator_anomaly", "degraded_mode", "verification", "restoration", "security"]


class BrainConfig(C):
    poll_interval_s: float = Field(default=1.0, gt=0)
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    inbound: InboundConfig = InboundConfig()
    dayahead: DayAheadConfig = DayAheadConfig()
    log_dir: str = "logs"
    timeline_url: Optional[str] = None
    forecast_log_every: int = Field(default=5, ge=1)
    ingest: IngestConfig = IngestConfig()
    learning: LearningConfig = LearningConfig()
    consistency: ConsistencyConfig = Field(default_factory=ConsistencyConfig)
    twin: PartnerConfig
    guardian: PartnerConfig
    trust: TrustPolicy = TrustPolicy()
    forecast: ForecastConfig = ForecastConfig()
    action: ActionConfig = ActionConfig()
    # Optional local knowledge about the city. Merged with whatever the Twin
    # advertises; Twin metadata wins on conflicts, local file fills gaps.
    city_file: Optional[str] = None


def load_config(path: str | os.PathLike) -> BrainConfig:
    p = Path(path)
    raw = _expand(yaml.safe_load(p.read_text()) or {})
    cfg = BrainConfig.model_validate(raw)
    if cfg.city_file and not Path(cfg.city_file).is_absolute():
        cfg.city_file = str((p.parent / cfg.city_file).resolve())
    return cfg


def load_city(path: str | os.PathLike) -> CityDescriptor:
    return CityDescriptor.model_validate(yaml.safe_load(Path(path).read_text()))
