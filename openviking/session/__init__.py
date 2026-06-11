# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session management module."""

from typing import TYPE_CHECKING, Optional

from openviking.session.session import Session, SessionCompression, SessionMeta, SessionStats
from openviking.storage import VikingDBManager
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from openviking.session.compressor_v2 import SessionCompressorV2


def create_session_compressor(
    vikingdb: VikingDBManager,
    memory_version: Optional[str] = None,
    skill_processor=None,
) -> "SessionCompressorV2":
    """
    Create the v2 session compressor.

    Args:
        vikingdb: VikingDBManager instance
        memory_version: Deprecated optional override. Only "v2" is supported.

    Returns:
        SessionCompressorV2 instance
    """
    if memory_version not in (None, "v2"):
        raise ValueError("memory.version only supports 'v2'; legacy memory v1 has been removed")

    logger.info("Using v2 memory compressor (templating system)")
    from openviking.session.compressor_v2 import SessionCompressorV2

    return SessionCompressorV2(vikingdb=vikingdb, skill_processor=skill_processor)


__all__ = [
    # Session
    "Session",
    "SessionCompression",
    "SessionMeta",
    "SessionStats",
    # Compressor
    "create_session_compressor",
]
