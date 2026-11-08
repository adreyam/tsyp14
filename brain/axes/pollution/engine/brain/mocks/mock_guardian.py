"""
Stand-in for the Guardian layer (R1: City Brain must run alone).
Trusts every sensor unless a scenario says otherwise.
"""
from datetime import datetime
from typing import Dict, Optional, Tuple

from src.models import BinReading, TrustStatus, TrustVerdict


class ScriptedGuardian:
    def __init__(self) -> None:
        self._rules: Dict[str, Tuple[TrustStatus, str]] = {}
        self.down = False

    def distrust(self, sensor_id: str, reason: str,
                 status: TrustStatus = TrustStatus.NOT_TRUSTED) -> None:
        self._rules[sensor_id] = (status, reason)

    def trust(self, sensor_id: str) -> None:
        self._rules.pop(sensor_id, None)

    def get_trust_verdict(self, reading: BinReading) -> TrustVerdict:
        if self.down:
            raise ConnectionError("guardian unreachable")
        status, reason = self._rules.get(reading.sensor_id, (TrustStatus.TRUSTED, None))
        return TrustVerdict(
            sensor_id=reading.sensor_id, status=status,
            confidence=0.95 if status == TrustStatus.TRUSTED else 0.1,
            reason=reason, checked_at=reading.timestamp)


class WatchingGuardian(ScriptedGuardian):
    """Mock Guardian for the live demo: it also watches the Twin's stream (as the
    real Guardian does) and applies one simple rule, so an attack started from
    the control page is caught without scripting the verdict.

    Rule: a bin that rises by `jump_pct` points or more in one reading is
    not_trusted, until its reading comes back near the last trusted value.
    A drop is never suspicious here: it is a truck emptying the bin.
    Scripted verdicts (distrust/trust) still take priority."""

    def __init__(self, jump_pct: float = 40.0, back_pct: float = 15.0) -> None:
        super().__init__()
        self.jump_pct = jump_pct
        self.back_pct = back_pct
        self._trusted_fill: Dict[str, float] = {}
        self._flagged: Dict[str, str] = {}

    def observe(self, message: dict) -> None:
        if message.get("type") == "air_quality":
            return self._observe_air(message)
        try:
            sid = message["sensor_id"]
            fill = message["weight_kg"] / message["capacity_kg"] * 100.0
        except (KeyError, TypeError, ZeroDivisionError):
            return
        last = self._trusted_fill.get(sid)
        if sid in self._flagged:
            if last is not None and abs(fill - last) <= self.back_pct:
                del self._flagged[sid]
                self._trusted_fill[sid] = fill
            return
        if last is not None and fill - last >= self.jump_pct:
            self._flagged[sid] = (f"jumped from {last:.0f}% to {fill:.0f}% in one reading")
            return
        self._trusted_fill[sid] = fill

    def forget(self, sensor_id: str, baseline: Optional[float] = None) -> None:
        """The attack on this sensor was stopped from the control page. The flag stays until a
        reading comes back near `baseline` (the real level, known to the mock city), instead of
        near the value trusted before the attack: a bin that really filled during the attack
        would otherwise stay blocked for ever, hiding a real full bin. A fake reading still in
        flight is not near the real level, so it is not taken as the new baseline."""
        if baseline is None:
            self._flagged.pop(sensor_id, None)
            self._trusted_fill.pop(sensor_id, None)
        else:
            self._trusted_fill[sensor_id] = baseline

    def get_trust_verdict(self, reading: BinReading) -> TrustVerdict:
        if not self.down and reading.sensor_id not in self._rules \
                and reading.sensor_id in self._flagged:
            return TrustVerdict(sensor_id=reading.sensor_id, status=TrustStatus.NOT_TRUSTED,
                                confidence=0.1, reason=self._flagged[reading.sensor_id],
                                checked_at=reading.timestamp)
        return super().get_trust_verdict(reading)

    def _observe_air(self, message: dict) -> None:
        """Air rule: PM2.5 cannot rise by 60 ug/m3 or more in one reading (10 min)."""
        try:
            sid, pm = message["sensor_id"], float(message["pm25"])
        except (KeyError, TypeError, ValueError):
            return
        last = self._trusted_fill.get(sid)
        if sid in self._flagged:
            if last is not None and abs(pm - last) <= 15.0:
                del self._flagged[sid]
                self._trusted_fill[sid] = pm
            return
        if last is not None and pm - last >= 60.0:
            self._flagged[sid] = (f"PM2.5 jumped from {last:.0f} to {pm:.0f} ug/m3 in one reading, "
                                  f"physically impossible")
            return
        self._trusted_fill[sid] = pm
