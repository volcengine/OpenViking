# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Message module - based on opencode Part design.

Message = role + parts
"""

from openviking.message.message import Message
from openviking.message.part import ContextPart, Part, TextPart, ToolPart

__all__ = [
    "Message",
    "Part",
    "TextPart",
    "ContextPart",
    "ToolPart",
]
