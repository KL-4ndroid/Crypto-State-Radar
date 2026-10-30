"""Deterministic UTC retry scheduling shared by scan and validation workers."""
from datetime import datetime, timedelta, timezone
import hashlib


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value,str) else value
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(timezone.utc)


def retry_at(now, attempt, identity):
    delay = min(3600,60 * 2 ** min(max(attempt-1,0),6))
    # Stable bounded jitter spreads jobs without compromising replayability.
    jitter = int(hashlib.sha256(f"{identity}:{attempt}".encode()).hexdigest()[:4],16) % 16
    return (utc(now)+timedelta(seconds=delay+jitter)).isoformat()


def ready(item, now):
    return not item.get("next_attempt_at") or utc(item["next_attempt_at"])<=utc(now)
