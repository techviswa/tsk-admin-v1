"""Preserve POS backoff instructions across HTTP and queued sync failures."""
import math
from email.utils import parsedate_to_datetime


def retry_after_seconds(value, now, fallback=300):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, OverflowError, AttributeError):
            return fallback
    return max(1, math.ceil(seconds)) if math.isfinite(seconds) else fallback


def rate_limit_delay(detail):
    if isinstance(detail, list):
        return max((rate_limit_delay(item) for item in detail), default=0)
    if not isinstance(detail, dict):
        return 0
    delay = 0
    if detail.get("code") == "POS_RATE_LIMITED" or str(detail.get("status_code")) == "429":
        delay = retry_after_seconds(detail.get("retry_after_seconds"), 0)
    return max(delay, max((rate_limit_delay(value) for value in detail.values()
                           if isinstance(value, (dict, list))), default=0))
