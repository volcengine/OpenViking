# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Content extractor types for OpenViking."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ContentType(Enum):
    TEXT_ONLY = "text_only"
    IMAGE_ONLY = "image_only"
    MIXED = "mixed"


class PDFSubType(Enum):
    TEXT_NATIVE = "text_native"
    IMAGE_SLIDE = "image_slide"
    IMAGE_SCAN = "image_scan"
    MIXED_NATIVE = "mixed_native"


class MediaType(Enum):
    IMAGE = "image"
    TABLE = "table"
    CHART = "chart"
    FORMULA = "formula"


class MediaStrategy(Enum):
    TEXT_ONLY = "text_only"
    EXTRACT_AND_REPLACE = "extract"
    FULL_PAGE_VLM = "full_page_vlm"


@dataclass
class ImageInfo:
    path: Path
    page: int
    position: Tuple[float, float, float, float]
    media_type: MediaType = MediaType.IMAGE
    width: int = 0
    height: int = 0
    format: str = "png"
    context: str = ""
    placeholder: str = ""


@dataclass
class TableInfo:
    path: Path
    page: int
    position: Tuple[float, float, float, float]
    raw_data: Optional[List[List[str]]] = None
    media_type: MediaType = MediaType.TABLE
    rows: int = 0
    cols: int = 0
    context: str = ""
    placeholder: str = ""

    def has_structured_data(self) -> bool:
        return self.raw_data is not None and len(self.raw_data) > 0


@dataclass
class ExtractionResult:
    text_content: str
    images: List[ImageInfo] = field(default_factory=list)
    tables: List[TableInfo] = field(default_factory=list)
    content_type: ContentType = ContentType.TEXT_ONLY
    page_count: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
