# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from openviking.storage.queuefs.semantic_processor import _truncate_text_by_token_budget


def test_truncate_text_by_token_budget_reduces_long_prompt_content():
    text = "token " * 20000

    truncated = _truncate_text_by_token_budget(text, "unknown-model", 100)

    assert len(truncated) < len(text)
    assert truncated.endswith("...(truncated)")
