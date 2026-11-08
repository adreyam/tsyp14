"""Configuration: config/brain.yaml + environment overrides. No partner address lives in the code."""
import copy
import os

import yaml

DEFAULTS = {
    "brain": {"port": 8000, "logs_dir": "../logs", "scenarios_dir": "../scenarios"},
    "twin": {"enabled": True, "url": "http://127.0.0.1:9001", "command_path": "/api/actuators/{actuator_id}/commands", "timeout_s": 2.0},
    "guardian": {"enabled": True, "url": "http://127.0.0.1:9002", "decision_path": "/api/decisions", "report_path": "/api/reports", "timeout_s": 2.0},
    "auth": {"inbound": {"twin_key": "twin-dev-key", "guardian_key": "guardian-dev-key"},
             "outbound": {"twin_key": "brain-to-twin-dev-key", "guardian_key": "brain-to-guardian-dev-key"}},
    "trust": {"unknown_device_policy": "trust", "verdict_ttl_s": 900, "rollback_on_untrusted": True},
    "limits": {"max_readings_per_s_per_device": 30, "max_batch": 200, "max_body_bytes": 200000, "queue_size": 500},
}

ENV = {  # environment variable -> (section, key[, subkey]), converter
    "BRAIN_PORT": (("brain", "port"), int), "BRAIN_LOGS_DIR": (("brain", "logs_dir"), str),
    "BRAIN_TWIN_URL": (("twin", "url"), str), "BRAIN_TWIN_ENABLED": (("twin", "enabled"), lambda v: v not in ("0", "false", "no")),
    "BRAIN_GUARDIAN_URL": (("guardian", "url"), str), "BRAIN_GUARDIAN_ENABLED": (("guardian", "enabled"), lambda v: v not in ("0", "false", "no")),
    "BRAIN_TWIN_KEY": (("auth", "inbound", "twin_key"), str), "BRAIN_GUARDIAN_KEY": (("auth", "inbound", "guardian_key"), str),
    "BRAIN_TO_TWIN_KEY": (("auth", "outbound", "twin_key"), str), "BRAIN_TO_GUARDIAN_KEY": (("auth", "outbound", "guardian_key"), str),
}


def _merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    path = path or os.environ.get("BRAIN_CONFIG", "config/brain.yaml")
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            _merge(cfg, yaml.safe_load(fh) or {})
    for var, (keys, conv) in ENV.items():
        if os.environ.get(var) not in (None, ""):
            node = cfg
            for k in keys[:-1]:
                node = node[k]
            node[keys[-1]] = conv(os.environ[var])
    return cfg
