# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Session management module."""

from openviking.session.compressor import ExtractionStats, SessionCompressor
from openviking.session.memory_deduplicator import (
    DedupDecision,
    DedupResult,
    ExistingMemoryAction,
    MemoryActionDecision,
    MemoryDeduplicator,
)
from openviking.session.memory_extractor import CandidateMemory, MemoryCategory, MemoryExtractor
from openviking.session.session import Session, SessionCompression, SessionStats

__all__ = [
    # Session
    "Session",
    "SessionCompression",
    "SessionStats",
    # Compressor
    "SessionCompressor",
    "ExtractionStats",
    # Memory Extractor
    "MemoryExtractor",
    "MemoryCategory",
    "CandidateMemory",
    # Memory Deduplicator
    "MemoryDeduplicator",
    "DedupDecision",
    "MemoryActionDecision",
    "ExistingMemoryAction",
    "DedupResult",
]
