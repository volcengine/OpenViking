# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.privacy.validation import (
    PRIVACY_CHANGE_REASON_MAX_CHARS,
    PRIVACY_FIELD_NAME_MAX_BYTES,
    PRIVACY_MAX_FIELDS,
    PRIVACY_MAX_STRING_BYTES,
    PRIVACY_VALUES_MAX_BYTES,
    PRIVACY_VALUES_MAX_DEPTH,
    validate_privacy_identifier,
    validate_privacy_metadata,
    validate_privacy_values,
)
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "must not be empty"),
        ("..", "must not be '.' or '..'"),
        (" bad", "leading or trailing whitespace"),
        ("bad/path", "may contain only ASCII"),
        ("bad%2Fpath", "may contain only ASCII"),
        ("two@@signs", "at most one '@'"),
    ],
)
def test_privacy_identifier_errors_are_specific(value, reason):
    with pytest.raises(InvalidArgumentError, match=reason) as exc_info:
        validate_privacy_identifier(value, "target_key")
    assert exc_info.value.details["field"] == "target_key"
    assert exc_info.value.details["allowed_pattern"] == "[A-Za-z0-9_.@-]+"


def test_privacy_values_enforce_field_count_name_and_string_limits_without_echoing_value():
    with pytest.raises(InvalidArgumentError, match="at most 128"):
        validate_privacy_values({f"key_{index}": index for index in range(PRIVACY_MAX_FIELDS + 1)})

    with pytest.raises(InvalidArgumentError, match="field name"):
        validate_privacy_values({"x" * (PRIVACY_FIELD_NAME_MAX_BYTES + 1): "value"})

    secret = "s" * (PRIVACY_MAX_STRING_BYTES + 1)
    with pytest.raises(InvalidArgumentError, match="maximum size") as exc_info:
        validate_privacy_values({"pat": secret})
    assert secret not in str(exc_info.value)
    assert exc_info.value.details["actual_bytes"] == PRIVACY_MAX_STRING_BYTES + 1


def test_privacy_values_enforce_total_size_and_nesting_depth():
    chunk = "x" * PRIVACY_MAX_STRING_BYTES
    with pytest.raises(InvalidArgumentError, match="serialize") as exc_info:
        validate_privacy_values({f"key_{index}": chunk for index in range(17)})
    assert exc_info.value.details["actual_bytes"] > PRIVACY_VALUES_MAX_BYTES

    nested = "value"
    for _ in range(PRIVACY_VALUES_MAX_DEPTH):
        nested = {"child": nested}
    with pytest.raises(InvalidArgumentError, match="nesting depth"):
        validate_privacy_values({"root": nested})


def test_privacy_metadata_limits_change_reason():
    with pytest.raises(InvalidArgumentError, match="change_reason") as exc_info:
        validate_privacy_metadata(
            change_reason="x" * (PRIVACY_CHANGE_REASON_MAX_CHARS + 1),
            labels=None,
        )
    assert exc_info.value.details["max_characters"] == PRIVACY_CHANGE_REASON_MAX_CHARS
