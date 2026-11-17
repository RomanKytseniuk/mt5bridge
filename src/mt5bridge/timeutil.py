"""Time handling.

MT5 reports every time in the broker's server time zone. mt5bridge keeps it that way, so the
numbers match what the trader sees on the MT5 chart. Epoch values here are "server wall clock
seconds", the same thing MQL5 calls datetime.
"""

import re
from datetime import datetime, timezone

_RELATIVE = re.compile(r"^(\d+)\s*(m|min|h|d|w)$", re.IGNORECASE)
_UNIT_S = {"m": 60, "min": 60, "h": 3600, "d": 86400, "w": 604800}
_DATE_ONLY = ("%Y-%m-%d",)
_DATETIME = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def parse_time(value: str | int | float | None, now: int, *, end_of_day: bool = False) -> int | None:
    """Parse a user-supplied time into server epoch seconds.

    Accepts: epoch seconds, "2026-11-14", "2026-11-14 10:30", "now", "today", "yesterday",
    or a look-back like "90m", "12h", "7d", "2w". A bare date used as an end bound
    (end_of_day=True) means the end of that day.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    low = s.lower()
    day_start = now - now % 86400
    if low == "now":
        return now
    if low == "today":
        return day_start + 86399 if end_of_day else day_start
    if low == "yesterday":
        return day_start - 1 if end_of_day else day_start - 86400
    m = _RELATIVE.match(s)
    if m:
        return now - int(m.group(1)) * _UNIT_S[m.group(2).lower()]
    for fmt in _DATETIME:
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    for fmt in _DATE_ONLY:
        try:
            ts = int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
            return ts + 86399 if end_of_day else ts
        except ValueError:
            pass
    raise ValueError(
        f"cannot parse time {value!r}: use YYYY-MM-DD, 'YYYY-MM-DD HH:MM', now, today, "
        "yesterday, or a look-back like 12h / 7d / 2w"
    )


def fmt_time(ts: int | float | None, *, seconds: bool = True) -> str | None:
    if ts is None:
        return None
    pattern = "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(pattern)


def fmt_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = [f"{d}d"] if d else []
    if h:
        parts.append(f"{h}h")
    if m and not d:
        parts.append(f"{m}m")
    if not parts:
        parts.append(f"{s}s")
    return " ".join(parts)
