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


def test_legacy_row_with_extra_fields_gets_both_kinds_of_schema_default():
    meta = {
        "name": {"FieldType": "string", "DefaultValue": None},
        "count": {"FieldType": "int64", "DefaultValue": None},
        "status": {"FieldType": "string", "DefaultValue": "pending"},
        "score": {"FieldType": "int64", "DefaultValue": 3},
    }
    legacy = {"name": "hello", "score": 7, "persisted": 5, "legacy": "value"}
    expected = {**legacy, "count": 0, "status": "pending"}

    assert fix_fields_data(legacy, meta) == expected
    assert fix_fields_data(legacy, meta) == expected
