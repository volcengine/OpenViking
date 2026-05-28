from datetime import datetime, timezone

from openviking.session.memory.dataclass import MemoryFile, VersionHistory, VersionHistoryItem
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.memory_version_utils import (
    apply_reverse_diff,
    is_version_visible,
    make_reverse_diff,
    materialize_memory_at_version,
    resolve_version_for_data_version,
    trim_versions,
)
from openviking.session.memory.utils.messages import parse_memory_file_with_fields


def test_parse_memory_file_without_version_history():
    raw = """hello\n\n<!-- MEMORY_FIELDS
{
  \"memory_type\": \"preferences\"
}
-->"""

    parsed = parse_memory_file_with_fields(raw)

    assert parsed["content"] == "hello"
    assert parsed["memory_type"] == "preferences"
    assert "version_history" not in parsed


def test_parse_memory_file_with_version_history():
    raw = """hello\n\n<!-- MEMORY_FIELDS
{
  \"memory_type\": \"preferences\"
}
-->\n\n<!-- VERSION_HISTORY
{
  \"data_version\": 123,
  \"updated_at\": \"2026-05-27T15:10:23.456Z\",
  \"status\": \"active\",
  \"versions\": [
    {\"data_version\": 123, \"op\": \"update\", \"reverse_diff\": \"abc\"}
  ]
}
-->"""

    parsed = parse_memory_file_with_fields(raw)

    assert parsed["content"] == "hello"
    assert parsed["version_history"]["data_version"] == 123
    assert parsed["version_history"]["status"] == "active"
    assert parsed["version_history"]["versions"][0]["reverse_diff"] == "abc"


def test_write_memory_file_with_version_history():
    memory_file = MemoryFile(
        content="hello",
        extra_fields={"memory_type": "preferences"},
        version_history=VersionHistory(
            data_version=123,
            updated_at=datetime.fromisoformat("2026-05-27T15:10:23.456+00:00"),
            status="active",
            versions=[VersionHistoryItem(data_version=123, op="update", reverse_diff="abc")],
        ),
    )

    raw = MemoryFileUtils.write(memory_file)

    assert "<!-- MEMORY_FIELDS" in raw
    assert "<!-- VERSION_HISTORY" in raw
    assert '"data_version": 123' in raw
    assert '"status": "active"' in raw

    parsed = MemoryFileUtils.read(raw)
    assert parsed.version_history is not None
    assert parsed.version_history.data_version == 123
    assert parsed.version_history.status == "active"
    assert parsed.version_history.versions[0].reverse_diff == "abc"


def test_make_and_apply_reverse_diff_round_trip():
    current = "new body"
    previous = "old body"

    reverse_diff = make_reverse_diff(current, previous)
    restored = apply_reverse_diff(current, reverse_diff)

    assert restored == previous


def test_materialize_returns_head_when_data_version_is_none():
    raw = MemoryFileUtils.write(
        MemoryFile(
            content="hello",
            extra_fields={"memory_type": "preferences"},
            version_history=VersionHistory(data_version=123, status="active"),
        )
    )

    assert materialize_memory_at_version(raw, None) == raw


def test_materialize_returns_head_when_head_version_lte_target():
    raw = MemoryFileUtils.write(
        MemoryFile(
            content="hello",
            extra_fields={"memory_type": "preferences"},
            version_history=VersionHistory(data_version=123, status="active"),
        )
    )

    assert materialize_memory_at_version(raw, 200) == raw


def test_materialize_replays_reverse_diffs():
    reverse_diff = make_reverse_diff("new body", "old body")
    raw = MemoryFileUtils.write(
        MemoryFile(
            content="new body",
            extra_fields={"memory_type": "preferences"},
            version_history=VersionHistory(
                data_version=200,
                updated_at=datetime.now(timezone.utc),
                status="active",
                versions=[
                    VersionHistoryItem(data_version=200, op="update", reverse_diff=reverse_diff)
                ],
            ),
        )
    )

    materialized = materialize_memory_at_version(raw, 199)
    parsed = MemoryFileUtils.read(materialized or "")
    assert parsed.content == "old body"


def test_materialize_returns_none_when_no_version_lte_target():
    raw = MemoryFileUtils.write(
        MemoryFile(
            content="new body",
            extra_fields={"memory_type": "preferences"},
            version_history=VersionHistory(
                data_version=200,
                status="active",
                versions=[VersionHistoryItem(data_version=200, op="create", reverse_diff=None)],
            ),
        )
    )

    assert materialize_memory_at_version(raw, 199) is None


def test_materialize_filters_deleted_version():
    raw = MemoryFileUtils.write(
        MemoryFile(
            content="hello",
            extra_fields={"memory_type": "preferences"},
            version_history=VersionHistory(data_version=123, status="deleted"),
        )
    )

    assert materialize_memory_at_version(raw, None) is None


def test_resolve_version_for_data_version_returns_none_for_unknown_historical_file():
    mf = MemoryFile(content="hello", extra_fields={"memory_type": "preferences"})
    assert resolve_version_for_data_version(mf, 100) is None


def test_resolve_version_for_data_version_uses_updated_at_fallback():
    mf = MemoryFile(
        content="hello",
        extra_fields={"memory_type": "preferences"},
        version_history=VersionHistory(
            updated_at=datetime.fromisoformat("2026-05-27T15:10:23.456+00:00"),
            status="active",
        ),
    )

    resolved = resolve_version_for_data_version(mf, 9999999999999)
    assert resolved is not None
    assert resolved["status"] == "active"


def test_is_version_visible():
    assert is_version_visible({"status": "active"}) is True
    assert is_version_visible({"status": "deleted"}) is False


def test_trim_versions_keeps_latest_100_versions():
    vh = VersionHistory(
        data_version=200,
        status="active",
        versions=[
            VersionHistoryItem(data_version=200 - i, op="update", reverse_diff=str(i))
            for i in range(150)
        ],
    )

    trimmed = trim_versions(vh, limit=100)
    assert trimmed is not None
    assert len(trimmed.versions) == 100
