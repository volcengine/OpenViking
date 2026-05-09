import re
from datetime import datetime, timezone

# Matches fractional seconds with more than 6 digits (e.g. .1470042)
_EXCESS_FRAC_RE = re.compile(r"(\.\d{6})\d+")


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, tolerating >6-digit fractional seconds.

    Windows may produce timestamps like ``2026-02-21T13:20:23.1470042+08:00``
    where the fractional seconds exceed Python's 6-digit microsecond limit.
    This helper truncates the excess digits before parsing.
    """
    normalized = _EXCESS_FRAC_RE.sub(r"\1", value)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def format_iso8601(dt: datetime) -> str:
    """
    Format datetime object to ISO 8601 format compatible with VikingDB.

    Format: yyyy-MM-ddTHH:mm:ss.SSSZ (UTC)
    """
    # Ensure dt is timezone-aware and in UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_simplified(dt: datetime, now: datetime) -> str:
    """
    Format datetime in UTC: HH:MM:SS if same UTC date as *now*, else YYYY-MM-DD.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc)
    if dt.date() == now.date():
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%Y-%m-%d")


def get_current_timestamp() -> str:
    """
    Get current timestamp in ISO 8601 format compatible with VikingDB.

    Format: yyyy-MM-ddTHH:mm:ss.SSSZ (UTC)
    """
    now = datetime.now(timezone.utc)
    return format_iso8601(now)
