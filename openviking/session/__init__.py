# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session management module."""

from typing import TYPE_CHECKING, Optional

from openviking.session.session import Session, SessionCompression, SessionMeta, SessionStats
from openviking.storage import VikingDBManager
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from openviking.session.compressor_v3 import SessionCompressorV3


def create_session_compressor(
    vikingdb: VikingDBManager,
    memory_version: Optional[str] = None,
    skill_processor=None,
) -> "SessionCompressorV3":
    """
    Create the session compressor.

    Args:
        vikingdb: VikingDBManager instance
        memory_version: Deprecated and ignored; v3 is always used. Existing
            configs that still set memory.version continue to load, but no
            longer select the implementation.

    Returns:
        v3 session compressor instance
    """
    if memory_version is not None:
        logger.warning("memory.version is deprecated and ignored; using v3 memory compressor")

    logger.info("Using v3 memory compressor (v2 + commit streaming train)")
    from openviking.session.compressor_v3 import SessionCompressorV3

    return SessionCompressorV3(vikingdb=vikingdb, skill_processor=skill_processor)


__all__ = [
    # Session
    "Session",
    "SessionCompression",
    "SessionMeta",
    "SessionStats",
    # Compressor
    "create_session_compressor",
]
