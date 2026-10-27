"""HTTP retry hints without retrying potentially billable POST operations."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_after_seconds(value: str, *, fallback: int = 3600, now: datetime = None) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - now).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = fallback
    if not math.isfinite(seconds):
        seconds = fallback
    return int(min(86400, max(60, seconds)))
