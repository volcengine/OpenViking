from datetime import datetime, timezone


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
    Format datetime object to simplified format: yyyy-MM-dd (if not in a day) or HH:mm:ss (if in a day).

    This format is more readable for humans and is used in VikingDB.
    """
    dt = dt.replace(tzinfo=None)
    # if in a day
    if (now - dt).days < 1:
        return dt.strftime("%H:%M:%S")
    else:
        return dt.strftime("%Y-%m-%d")


def get_current_timestamp() -> str:
    """
    Get current timestamp in ISO 8601 format compatible with VikingDB.

    Format: yyyy-MM-ddTHH:mm:ss.SSSZ (UTC)
    """
    now = datetime.now(timezone.utc)
    return format_iso8601(now)
