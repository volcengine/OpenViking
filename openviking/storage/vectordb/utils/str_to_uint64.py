# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import xxhash


def str_to_uint64(input_string: str) -> int:
    """
    Generate a 64-bit unsigned integer hash from a string using xxHash.
    """
    return xxhash.xxh64(input_string).intdigest()
