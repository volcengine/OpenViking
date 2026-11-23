# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Immutable records: decisions that change the upstream conversation, and reply owners."""

from enum import Enum


class RecordKind(str, Enum):
    ROOT = "root"
    INJECTION = "injection"
    DISABLED = "disabled"
    # The gateway tools frozen for the session, and the one-way switch that turns them off.
    TOOLS = "tools"
    TOOLS_CLOSED = "tools_closed"
    # The session's client sent tools of its own, which its turns then always carry.
    CLIENT_TOOLS = "client_tools"
    HIDDEN = "hidden"
    REPLACEMENT = "replacement"
    # The session whose model produced the reply ending at the anchor.
    REPLY = "reply"
    # Reasoning the upstream returned for the reply item at the anchor, for clients that drop it.
    REASONING = "reasoning"
