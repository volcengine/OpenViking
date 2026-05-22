# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for removed text patch API.
"""

import openviking.session.memory.merge_op as merge_op
from openviking.session.memory.merge_op import patch_handler


def test_text_patch_handler_api_is_removed():
    assert not hasattr(patch_handler, "MemoryPatchHandler")
    assert not hasattr(merge_op, "MemoryPatchHandler")
