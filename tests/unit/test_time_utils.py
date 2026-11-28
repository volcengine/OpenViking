from datetime import timezone
from zoneinfo import ZoneInfo

from openviking.core.context import Context
from openviking.utils.time_utils import parse_iso_datetime, parse_iso_datetime_in_timezone


def test_parse_iso_datetime_accepts_z_suffix():
    dt = parse_iso_datetime("2026-03-03T01:26:14.481Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timezone.utc.utcoffset(dt)


def test_parse_iso_datetime_in_timezone_converts_aware_and_naive_values():
    shanghai = ZoneInfo("Asia/Shanghai")

    assert (
        parse_iso_datetime_in_timezone("2026-11-21T18:18:09.765Z", shanghai).isoformat()
        == "2026-11-22T02:18:09.765000+08:00"
    )
    assert (
        parse_iso_datetime_in_timezone("2026-11-21T18:18:09", shanghai).isoformat()
        == "2026-11-22T02:18:09+08:00"
    )
    assert (
        parse_iso_datetime_in_timezone("2026-11-21T09:00:00-05:00", shanghai).isoformat()
        == "2026-11-21T22:00:00+08:00"
    )


def test_context_from_dict_accepts_z_timestamps():
    ctx = Context.from_dict(
        {
            "uri": "viking://user/default/memories/entities/mem_x.md",
            "created_at": "2026-03-03T01:26:14.481Z",
            "updated_at": "2026-03-03T01:27:14.481Z",
            "is_leaf": True,
            "context_type": "memory",
        }
    )
    assert ctx.created_at is not None
    assert ctx.updated_at is not None
