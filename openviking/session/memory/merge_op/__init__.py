# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Merge operation implementations.
"""

from openviking.session.memory.merge_op.base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.merge_op.factory import MergeOpFactory
from openviking.session.memory.merge_op.immutable import ImmutableOp
from openviking.session.memory.merge_op.patch import PatchOp
from openviking.session.memory.merge_op.replace import ReplaceOp
from openviking.session.memory.merge_op.patch_handler import (
    MemoryPatchHandler,
    PatchParseError,
    apply_str_patch,
)
from openviking.session.memory.merge_op.sum import SumOp

__all__ = [
    "MergeOp",
    "MergeOpBase",
    "FieldType",
    "SearchReplaceBlock",
    "StrPatch",
    "PatchOp",
    "ReplaceOp",
    "SumOp",
    "ImmutableOp",
    "MergeOpFactory",
    "MemoryPatchHandler",
    "PatchParseError",
    "apply_str_patch",
]
