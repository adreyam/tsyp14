from __future__ import annotations

from typing import Optional

import httpx

from ..config import PartnerConfig
from .rest import RestGuardian, RestTwin


def make_twin(cfg: PartnerConfig, client: Optional[httpx.Client] = None):
    if cfg.adapter == "rest":
        return RestTwin(cfg, client)
    if cfg.adapter == "mcp":
        from .mcp import McpTwin
        return McpTwin(cfg, client)
    raise NotImplementedError(f"twin adapter '{cfg.adapter}' is not implemented (available: rest, mcp)")


def make_guardian(cfg: PartnerConfig, client: Optional[httpx.Client] = None):
    if cfg.adapter == "rest":
        return RestGuardian(cfg, client)
    raise NotImplementedError(f"guardian adapter '{cfg.adapter}' is not implemented yet (available: rest)")
