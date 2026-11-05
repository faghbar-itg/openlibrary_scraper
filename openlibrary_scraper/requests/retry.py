from __future__ import annotations

import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def backoff_seconds(attempt: int, retry_after: str | None = None) -> float:
    specified = retry_after_seconds(retry_after)
    if specified is not None:
        return specified
    return min(60.0, (2**attempt) + random.uniform(0, 0.5))
