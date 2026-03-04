from datetime import timezone

from openviking.core.context import Context
from openviking.utils.time_utils import parse_iso_datetime


def test_parse_iso_datetime_accepts_z_suffix():
    dt = parse_iso_datetime("2026-03-03T01:26:14.481Z")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timezone.utc.utcoffset(dt)


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
