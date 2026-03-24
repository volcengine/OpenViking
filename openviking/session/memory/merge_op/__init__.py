# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Merge operation implementations.
"""

from openviking.session.memory.merge_op.base import (
    MergeOp,
    MergeOpBase,
    FieldType,
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.merge_op.patch import PatchOp
from openviking.session.memory.merge_op.sum import SumOp
from openviking.session.memory.merge_op.immutable import ImmutableOp
from openviking.session.memory.merge_op.factory import MergeOpFactory
from openviking.session.memory.merge_op.patch_handler import (
    MemoryPatchHandler,
    PatchParseError,
    apply_str_patch,
)

__all__ = [
    "MergeOp",
    "MergeOpBase",
    "FieldType",
    "SearchReplaceBlock",
    "StrPatch",
    "PatchOp",
    "SumOp",
    "ImmutableOp",
    "MergeOpFactory",
    "MemoryPatchHandler",
    "PatchParseError",
    "apply_str_patch",
]
