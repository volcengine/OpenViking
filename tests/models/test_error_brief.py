# Copyright (c) 2026 Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.models.model_error import UNKNOWN_BRIEF, ModelErrorKind, brief_for


@pytest.mark.parametrize(
    "kind",
    [
        ModelErrorKind.AUTHENTICATION_ERROR,
        ModelErrorKind.PERMISSION_DENIED,
        ModelErrorKind.QUOTA_EXHAUSTED,
        ModelErrorKind.RATE_LIMIT_ERROR,
        ModelErrorKind.MODEL_NOT_FOUND,
        ModelErrorKind.UNSUPPORTED,
        ModelErrorKind.INVALID_REQUEST,
        ModelErrorKind.INPUT_TOO_LARGE,
        ModelErrorKind.CONTENT_SAFETY,
    ],
)
def test_actionable_model_kinds_keep_dedicated_brief(kind):
    brief = brief_for(kind.value)
    assert brief and brief != UNKNOWN_BRIEF


@pytest.mark.parametrize(
    "kind",
    [ModelErrorKind.TRANSIENT_SERVICE_ERROR, ModelErrorKind.TRANSIENT_NETWORK_ERROR],
)
def test_transient_kinds_collapse_to_unknown(kind):
    assert brief_for(kind.value) == UNKNOWN_BRIEF


def test_unknown_kind_returns_generic():
    assert brief_for(ModelErrorKind.UNKNOWN.value) == UNKNOWN_BRIEF


@pytest.mark.parametrize("bad", [None, "", "vector_db_write", "processing_failed", "nope"])
def test_internal_or_unrecognized_type_returns_none(bad):
    assert brief_for(bad) is None
