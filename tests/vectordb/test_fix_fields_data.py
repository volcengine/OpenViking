"""Regression test: fix_fields_data must fill every missing schema field.

The function backfills schema fields that are absent from a row's data with
their defaults. It previously short-circuited when
``len(field_data_dict) >= len(field_meta_dict)``, using field *count* as a
proxy for "all schema fields present". That proxy is wrong: a row can have as
many keys as the schema (or more) while still missing a specific field — for
example a row written before a new field was added that also carries an extra
non-schema/internal key. In that case the missing field was silently dropped
instead of defaulted.
"""

from openviking.storage.vectordb.utils.validation import fix_fields_data


def test_missing_field_is_filled_even_when_counts_match():
    meta = {
        "a": {"FieldType": "string", "DefaultValue": None},
        "b": {"FieldType": "int64", "DefaultValue": None},
        "vec": {"FieldType": "string", "DefaultValue": None},
    }
    # 3 keys == len(meta), but "b" is absent and an extra non-schema key exists.
    data = {"a": "hello", "vec": "x", "persisted": 5}

    result = fix_fields_data(dict(data), meta)

    assert "b" in result, "missing schema field must be backfilled"
    assert result["b"] == 0  # int64 type default


def test_default_value_is_used_when_present():
    meta = {
        "a": {"FieldType": "string", "DefaultValue": None},
        "status": {"FieldType": "string", "DefaultValue": "pending"},
        "extra": {"FieldType": "string", "DefaultValue": None},
    }
    data = {"a": "hello", "extra": "x", "legacy": "y"}

    result = fix_fields_data(dict(data), meta)

    assert result["status"] == "pending"


def test_complete_data_is_returned_unchanged():
    meta = {
        "a": {"FieldType": "string", "DefaultValue": None},
        "b": {"FieldType": "int64", "DefaultValue": None},
    }
    data = {"a": "hello", "b": 7}

    result = fix_fields_data(dict(data), meta)

    assert result == {"a": "hello", "b": 7}
