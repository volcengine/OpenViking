# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Immutable records: decisions that change the upstream conversation, and reply owners."""

from enum import Enum


class RecordKind(str, Enum):
    ROOT = "root"
    INJECTION = "injection"
    DISABLED = "disabled"
    HIDDEN = "hidden"
    REPLACEMENT = "replacement"
    # The session whose model produced the reply ending at the anchor.
    REPLY = "reply"
    # Reasoning the upstream returned for the reply item at the anchor, for clients that drop it.
    REASONING = "reasoning"
