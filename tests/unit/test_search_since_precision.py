from datetime import datetime, timezone

import pytest
from openviking.storage.vectordb.utils.data_processor import DataProcessor
from openviking.utils.search_filters import merge_time_filter


@pytest.fixture
def processor():
    return DataProcessor({"updated_at": {"FieldType": "date_time"}}, tz_policy="utc")


def matches_lower_bound(processor, since, stored, *, now=None):
    requested = merge_time_filter(None, since=since, now=now)
    indexed_filter = processor.convert_filter_for_index(requested)
    indexed_record = processor.convert_fields_dict_for_index({"updated_at": stored})["updated_at"]
    if "gt" in indexed_filter:
        return indexed_record > indexed_filter["gt"]
    return indexed_record >= indexed_filter["gte"]


@pytest.mark.parametrize(
    ("since", "stored", "expected"),
    [
        ("2026-11-24T15:22:40.123000Z", "2026-11-24T15:22:40.123Z", True),
        ("2026-11-24T15:22:40.123456Z", "2026-11-24T15:22:40.123Z", False),
        ("2026-11-24T15:22:40.123456Z", "2026-11-24T15:22:40.124Z", True),
        ("2026-11-24T23:22:40.123456+08:00", "2026-11-24T15:22:40.123Z", False),
        ("2026-11-24T15:22:40.999999Z", "2026-11-24T15:22:40.999Z", False),
        ("2026-11-24T15:22:40.999999Z", "2026-11-24T15:22:41.000Z", True),
    ],
)
def test_since_bounds_match_millisecond_index_records(processor, since, stored, expected):
    assert matches_lower_bound(processor, since, stored) is expected


def test_relative_lower_bound_respects_submillisecond_time(processor):
    now = datetime(2026, 11, 24, 17, 22, 40, 123456, tzinfo=timezone.utc)
    assert not matches_lower_bound(processor, "2h", "2026-11-24T15:22:40.123Z", now=now)
    assert matches_lower_bound(processor, "2h", "2026-11-24T15:22:40.124Z", now=now)


def test_fractional_lower_and_upper_can_form_an_empty_index_range(processor):
    query = merge_time_filter(
        None, since="2026-11-24T15:22:40.123456Z", until="2026-11-24T15:22:40.123999Z"
    )
    indexed_filter = processor.convert_filter_for_index(query)
    assert indexed_filter["gt"] == indexed_filter["lte"]


def test_fractional_since_preserves_existing_filter():
    existing = {"op": "must", "field": "kind", "conds": ["email"]}
    result = merge_time_filter(
        existing, since="2026-11-24T15:22:40.123456Z", time_field="created_at"
    )
    assert result == {
        "op": "and",
        "conds": [
            existing,
            {"op": "time_range", "field": "created_at", "gt": "2026-11-24T15:22:40.123Z"},
        ],
    }
