"""
Partner addresses, ports, timeouts and city settings, read from a JSON file (R1:
pointing the Brain at another partner is a config change, never a code change).

Secrets are NOT in the file: the file only names the environment variables that
hold the tokens, so the config can be committed and shared with partners.

    python -m src.service --config config/brain.json
"""
import json
import os
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "brain.json")


class ConfigError(Exception):
    """The configuration cannot be used. The message says what to fix."""


class Listen(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8100, ge=0, le=65535)


class McpSettings(BaseModel):
    """How to reach a Twin that speaks MCP over stdio (City Ground Zero)."""
    command: List[str]                        # e.g. ["python3", "tools/mcp/city_mcp_server.py"]
    env: Dict[str, str] = {}                  # e.g. {"CITY_SIM_API_PORT": "8820"}
    cwd: Optional[str] = None                 # relative to this config file
    timeout_s: float = Field(10.0, gt=0)
    origin_lat: float = 36.80                 # reference point for x/z metres -> lat/lon
    origin_lon: float = 10.18
    default_capacity_kg: Dict[str, float] = {"WGT-T": 25.0, "WGT-C": 120.0}
    zone_types: Dict[str, str] = {}           # zone letter -> "crowded" | "open"
    focus_on_order: bool = False              # demo: frame each ordered bin in the Godot viewport


class TwinPartner(BaseModel):
    transport: Literal["http", "mcp_stdio"] = "http"
    url: Optional[str] = None                 # http: e.g. http://192.168.1.20:8200
    dispatch_path: str = "/v1/dispatch"
    map_path: str = "/v1/map"
    timeout_s: float = Field(2.0, gt=0)
    mcp: Optional[McpSettings] = None         # mcp_stdio

    @model_validator(mode="after")
    def _needs(self):
        if self.transport == "http" and not self.url:
            raise ValueError("twin.url is required when twin.transport = http")
        if self.transport == "mcp_stdio" and self.mcp is None:
            raise ValueError("twin.mcp is required when twin.transport = mcp_stdio")
        return self


class GuardianPartner(BaseModel):
    url: str
    verdict_path: str = "/v1/trust-verdict"
    timeout_s: float = Field(2.0, gt=0)
    retry_after_s: float = Field(5.0, ge=0)   # after a failure, don't call again before this


class Auth(BaseModel):
    # Names of environment variables, not the tokens themselves.
    brain_token_env: str = "BRAIN_TOKEN"      # the Brain sends this to the Twin and the Guardian
    twin_token_env: str = "TWIN_TOKEN"        # the Brain accepts readings only with this one


class MapSource(BaseModel):
    source: Literal["twin", "file"] = "twin"
    path: Optional[str] = None                # when source = file
    retries: int = Field(30, ge=1)            # the Twin may start after the Brain
    retry_every_s: float = Field(2.0, gt=0)


class City(BaseModel):
    decision: Literal["trucks", "crew"] = "trucks"
    crew_max_orders_per_cycle: int = Field(3, ge=1)
    crew_reorder_after_s: float = Field(600.0, ge=0)
    # Projection horizon per zone type (minutes): orange = full before this. Default 4 h / 8 h.
    pass_interval_minutes: Dict[str, float] = {"crowded": 240.0, "open": 480.0}
    fallback_pass_interval_minutes: float = Field(240.0, gt=0)
    depot_node: Optional[str] = None
    truck_capacity_kg: float = Field(2000.0, gt=0)
    horizon_stops: int = Field(3, ge=1)
    reading_interval_s: float = Field(300.0, gt=0)
    # Bin colours and readings
    red_pct: float = Field(90.0, gt=0, le=100)          # fill at or above: red
    green_pct: float = Field(10.0, ge=0, lt=100)        # fill below: green
    emptied_drop_pct: float = Field(30.0, gt=0, le=100) # a drop this big = emptied by a truck
    history_size: int = Field(6, ge=2)                  # readings kept per bin for the fill rate
    silence_factor: float = Field(3.0, gt=0)            # silent for this many intervals = suspect

    @model_validator(mode="after")
    def _colours(self):
        if self.green_pct >= self.red_pct:
            raise ValueError("city.green_pct must be lower than city.red_pct")
        return self


class AirSettings(BaseModel):
    """Air-quality axis (src/air.py). The formula's constants stay in the code (AirConfig)."""
    enabled: bool = True
    lag_s: float = Field(3600.0, ge=0)            # t-1 = the reading this much older
    silence_s: float = Field(900.0, gt=0)         # older readings no longer count
    min_level_s: float = Field(3600.0, ge=0)      # a level holds at least this long
    exit_margin: float = Field(5.0, ge=0)
    exit_readings: int = Field(2, ge=1)
    warm_months: List[int] = [4, 5, 6, 7, 8, 9]


class AgentSettings(BaseModel):
    """Agent interface for an orchestrator (src/agent.py): POST /v1/agent/call."""
    enabled: bool = True
    token_env: str = "ORCHESTRATOR_TOKEN"     # the orchestrator's Bearer token, from this variable
    allow_act: bool = True                    # False: only read and what-if tools


class BrainSettings(BaseModel):
    listen: Listen = Listen()
    twin: TwinPartner
    guardian: GuardianPartner
    auth: Auth = Auth()
    map: MapSource = MapSource()
    city: City = City()
    air: AirSettings = AirSettings()
    agent: AgentSettings = AgentSettings()
    cycle_s: float = Field(1.0, gt=0)         # how often the Brain runs one decision cycle
    clock: Literal["twin", "wall"] = "twin"   # city time = Twin timestamps, or the wall clock
    log_path: Optional[str] = None
    max_body_bytes: int = Field(1_000_000, gt=0)

    # ---- secrets -----------------------------------------------------------
    def token(self, env_name: str) -> str:
        value = os.environ.get(env_name, "")
        if len(value) < 16:
            raise ConfigError(
                f"environment variable {env_name} must hold a token of at least 16 "
                f"characters (got {len(value)}). Agree on it with your partner and set it, "
                f"e.g. on Windows CMD:  set {env_name}=<token>")
        return value

    @property
    def brain_token(self) -> str:
        return self.token(self.auth.brain_token_env)

    @property
    def twin_token(self) -> str:
        return self.token(self.auth.twin_token_env)


def load(path: Optional[str] = None) -> BrainSettings:
    path = path or os.environ.get("BRAIN_CONFIG") or DEFAULT_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"config file {path} is not valid JSON: {e}")
    try:
        settings = BrainSettings(**data)
    except ValidationError as e:
        fields = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise ConfigError(f"config file {path}: {fields}")
    base = os.path.dirname(os.path.abspath(path))
    if settings.map.source == "file":
        if not settings.map.path:
            raise ConfigError(f"config file {path}: map.path is required when map.source = file")
        settings.map.path = os.path.normpath(os.path.join(base, settings.map.path))
    mcp = settings.twin.mcp
    if mcp and mcp.cwd:
        mcp.cwd = os.path.normpath(os.path.join(base, mcp.cwd))
    if settings.log_path:
        settings.log_path = os.path.normpath(os.path.join(base, settings.log_path))
    return settings
