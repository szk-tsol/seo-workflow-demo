from __future__ import annotations

import hmac
import hashlib
import time


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    tolerance_sec: int = 60 * 5,
) -> None:
    if not signing_secret:
        raise ValueError("missing signing_secret")
    if not timestamp or not signature:
        raise ValueError("missing slack headers")

    try:
        ts_int = int(timestamp)
    except Exception:
        raise ValueError("invalid timestamp")

    now = int(time.time())
    if abs(now - ts_int) > tolerance_sec:
        raise ValueError("timestamp too old")

    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    expected = "v0=" + digest

    if not hmac.compare_digest(expected, signature):
        raise ValueError("signature mismatch")
