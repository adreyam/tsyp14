"""
What City Brain needs from its two partners. Any object with these methods
works: a mock for running alone (R1), or an HTTP/MQTT client for a real partner.
Partner addresses belong in a config file, never in this code.
"""
from typing import Protocol

from .models import BinReading, TrustVerdict


class GuardianClient(Protocol):
    def get_trust_verdict(self, reading: BinReading) -> TrustVerdict: ...


class TwinClient(Protocol):
    def send_dispatch(self, message: dict) -> None: ...

    # Crew mode only (a Twin that empties a bin on request, e.g. City Ground Zero).
    def send_collection(self, bin_id: str) -> object: ...
