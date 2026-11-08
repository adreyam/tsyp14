"""HMAC-SHA256 message signing (optional, per partner).

  X-Timestamp: <unix seconds>
  X-Signature: hex( HMAC_SHA256(secret, "<timestamp>.<raw body>") )

The Brain signs every request body it sends to a partner that has a
`signing_secret`, and requires that partner's responses to be signed the same
way. A signature older than MAX_SKEW_S is refused (replay protection).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

MAX_SKEW_S = 30.0


def sign(secret: str, body: bytes, ts: Optional[float] = None) -> dict[str, str]:
    ts_s = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(secret.encode(), ts_s.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Timestamp": ts_s, "X-Signature": sig}


def verify(secret: str, body: bytes, ts_header: Optional[str], sig_header: Optional[str],
           now: Optional[float] = None, max_skew_s: float = MAX_SKEW_S) -> Optional[str]:
    """None if valid, else the reason it is not."""
    if not ts_header or not sig_header:
        return "unsigned message"
    try:
        ts = float(ts_header)
    except ValueError:
        return "bad X-Timestamp"
    if abs((now if now is not None else time.time()) - ts) > max_skew_s:
        return f"signature timestamp outside +/-{max_skew_s:g} s (replay?)"
    expected = hmac.new(secret.encode(), ts_header.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        return "signature mismatch"
    return None
