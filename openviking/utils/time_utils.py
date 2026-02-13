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


def get_current_timestamp() -> str:
    """
    Get current timestamp in ISO 8601 format compatible with VikingDB.

    Format: yyyy-MM-ddTHH:mm:ss.SSSZ (UTC)
    """
    now = datetime.now(timezone.utc)
    return format_iso8601(now)
