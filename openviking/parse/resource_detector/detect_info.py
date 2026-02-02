# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Detect resource type and understand the content before process.

We need to know:

"""

from dataclasses import dataclass
from enum import Enum


class VisitType(Enum):
    # Direct content that can be used immediately, e.g., strings containing conversation content, JSON content, etc.
    DIRECT_CONTENT = "DIRECT_CONTENT"
    # Data accessible via local or network filesystem tools, e.g., local files, folders, compressed files, etc.
    FILE_SYS = "FILE_SYS"
    # Resources that require download, e.g., files from network, web pages, remote objects, remote code repositories, etc.
    NEED_DOWNLOAD = "NEED_DOWNLOAD"
    # Pre-processed context pack conforming to OpenViking's structure, typically with .ovpack extension
    READY_CONTEXT_PACK = "READY_CONTEXT_PACK"


class SizeType(Enum):
    # Content can be processed directly in memory, e.g., small text segments
    IN_MEM = "IN_MEM"
    # Requires external storage for processing, e.g., multiple files, large files, etc.
    EXTERNAL = "EXTERNAL"
    # Content too large to process, e.g., exceeds X GB, may cause system crash or performance issues
    TOO_LARGE_TO_PROCESS = "TOO_LARGE_TO_PROCESS"


class RecursiveType(Enum):
    # Single file, no recursive processing required
    SINGLE = "SINGLE"
    # Recursive processing, e.g., all files in a directory, all files in subdirectories, etc.
    RECURSIVE = "RECURSIVE"
    # Files that need to be expanded for recursive processing, e.g., compressed files, READY_CONTEXT_PACK, etc.
    EXPAND_TO_RECURSIVE = "EXPAND_TO_RECURSIVE"


@dataclass
class DetectInfo:
    visit_type: VisitType
    size_type: SizeType
    recursive_type: RecursiveType
