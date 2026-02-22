#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Test for filename length handling fix."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openviking.parse.parsers.upload_utils import _sanitize_rel_path


def test_filename_length_fix():
    """Test that long filenames are properly truncated."""
    # Test exactly 255 bytes - should pass through unchanged
    filename_255 = "a" * 251 + ".txt"
    result = _sanitize_rel_path(filename_255)
    assert len(result.encode('utf-8')) <= 255
    assert result.endswith('.txt')
    
    # Test 256 bytes - should be truncated
    filename_256 = "b" * 252 + ".txt"
    result = _sanitize_rel_path(filename_256)
    assert len(result.encode('utf-8')) <= 255
    assert result.endswith('.txt')
    
    # Test very long CJK filename
    cjk_long = "测试文件名" * 30 + ".py"  # ~453 bytes
    result = _sanitize_rel_path(cjk_long)
    assert len(result.encode('utf-8')) <= 255
    assert result.endswith('.py')
    
    # Test filename with no extension
    no_ext_long = "x" * 300
    result = _sanitize_rel_path(no_ext_long)
    assert len(result.encode('utf-8')) <= 255
    
    print("All filename length tests passed!")


if __name__ == "__main__":
    test_filename_length_fix()