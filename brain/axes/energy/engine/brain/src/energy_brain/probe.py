"""Connectivity probe: check that the configured Twin and Guardian answer in a
format the Brain accepts, and report the knowledge level for this city.
Use it as the readiness check before a demo, and as the first thing to run
against a new partner.

  python -m energy_brain.probe --config config/config.yaml
"""
from __future__ import annotations

import argparse
import sys

from .adapters import PartnerError, make_guardian, make_twin
from .config import load_city, load_config
from .eventlog import EventLog
from .knowledge import assess, merge


def run(config_path: str, city_file: str | None = None) -> int:
    cfg = load_config(config_path)
    log = EventLog(cfg.log_dir, "probe", echo=True)
    twin, guardian = make_twin(cfg.twin), make_guardian(cfg.guardian)
    ok = True
    try:
        city = twin.get_city()
        local = load_city(city_file or cfg.city_file) if (city_file or cfg.city_file) else None
        merged = merge(city, local)
        k = assess(merged)
        log.emit("knowledge_level", f"city '{merged.city_id}' -> {k.level}", **k.as_payload())

        batch = twin.get_readings()
        for raw, why in batch.rejected:
            log.emit("reading_rejected", f"rejected reading: {why}", raw=raw)
        for r in batch.valid:
            cap = next((z.capacity_kw for z in merged.zones if z.zone_id == r.zone_id), None)
            pct = f" ({100 * r.kw / cap:.0f}% of {cap:g} kW)" if cap else " (capacity unknown)"
            print(f"    {r.sensor_id:<10} {r.zone_id:<8} {r.kw:8.1f} kW{pct}")
        switches = twin.get_switches()
        for s in switches:
            print(f"    {s.switch_id:<10} {s.state.value}")

        subjects = [r.sensor_id for r in batch.valid] + [s.switch_id for s in switches]
        try:
            verdicts = guardian.get_verdicts(subjects)
            trusted = sum(v.verdict.value == "trusted" for v in verdicts)
            log.emit("knowledge_level", f"guardian answered {len(verdicts)} verdicts ({trusted} trusted)")
        except PartnerError as e:
            ok = False
            log.emit("partner_error", str(e), partner=e.partner, operation=e.operation)
    except PartnerError as e:
        ok = False
        log.emit("partner_error", str(e), partner=e.partner, operation=e.operation)
    finally:
        log.close()
    print(f"log: {log.path}")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--city-file", default=None, help="optional local knowledge file (overrides config)")
    a = ap.parse_args()
    sys.exit(run(a.config, a.city_file))


if __name__ == "__main__":
    main()
