# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Which extractor should handle a given PDF.

The default path is ``pdf-inspector``: it agrees with pdfplumber word-for-word
on body text, has no duplicate-line noise, and is roughly 30x faster. The only
files sent elsewhere are ones it demonstrably cannot handle -- pages it leaves
blank, documents whose text it drops entirely, and layouts it flattens. Those
go to MinerU, which OCRs.

Every threshold below was read off a corpus of 1281 real PDFs rather than
picked. They are not round numbers and they do not interpolate: a threshold
moved by intuition will misfile documents that were measured. Re-derive one
against a corpus before changing it.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXTRACTOR_INSPECTOR = "pdf_inspector"
EXTRACTOR_MINERU = "mineru"

# pdf-inspector's own verdict on a page that carries no usable text.
SCANNED_TYPES = ("scanned", "image_based")

# Blank-page ratio above which the output is suspect rather than merely sparse.
EMPTY_PAGE_RATIO = 0.2
# ... but only when the pages that *do* have text are near-empty. Reads as
# "almost nothing came out", not "this is a picture book".
CHARS_PER_PAGE_FLOOR = 100
# Fenced-block share above which a document is treated as code-heavy.
CODE_RATIO = 0.05
# 16:9. Measured books sit at 0.71 and slides at 1.78; nothing lives between.
SLIDE_ASPECT = 1.7

_FENCE = re.compile(r"```.*?```", re.S)


@dataclass(frozen=True)
class RoutingSignals:
    """Evidence available about one PDF, gathered before the decision."""

    pdf_type: str = ""
    page_count: int = 0
    ocr_pages: int = 0
    empty_pages: int = 0
    chars: int = 0
    code_ratio: float = 0.0
    aspect: Optional[float] = None


def choose_extractor(signals: RoutingSignals) -> str:
    """Pick the extractor for one PDF.

    Order is the rule order: a document that fails an earlier check is already
    routed, so the signals never compete.
    """
    if signals.pdf_type in SCANNED_TYPES:
        return EXTRACTOR_MINERU

    # Any flagged page counts. pdf-inspector does not error on a partial scan,
    # it silently emits a blank page for it -- exactly the content we would
    # otherwise lose, and only OCR can recover it.
    if signals.ocr_pages > 0:
        return EXTRACTOR_MINERU

    # The silent failure: the classifier is happy and the text layer looks
    # fine, yet the extraction came back nearly empty. Needs both halves --
    # total characters alone misses a 806-page book that kept 2192 of them.
    if signals.page_count:
        empty_ratio = signals.empty_pages / signals.page_count
        chars_per_page = signals.chars / signals.page_count
        if empty_ratio >= EMPTY_PAGE_RATIO and chars_per_page < CHARS_PER_PAGE_FLOOR:
            return EXTRACTOR_MINERU

    if signals.aspect is not None and signals.aspect >= SLIDE_ASPECT:
        return EXTRACTOR_MINERU

    if signals.code_ratio >= CODE_RATIO:
        return EXTRACTOR_MINERU

    return EXTRACTOR_INSPECTOR


def code_ratio(markdown: str) -> float:
    """Share of a markdown document, by character, inside fenced code blocks."""
    if not markdown:
        return 0.0
    return sum(len(match.group(0)) for match in _FENCE.finditer(markdown)) / len(markdown)


def page_aspect(pdf_path: Path) -> Optional[float]:
    """Width/height of the first page, or None when it cannot be read.

    Reads the MediaBox only -- the page tree, never a content stream. Needs
    pdfminer (an optional dependency, pulled in by pdfplumber); a missing
    dependency and a damaged file degrade the same way, to "unknown", because
    aspect is one tiebreaker among several.
    """
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfpage import PDFPage
        from pdfminer.pdfparser import PDFParser
    except ImportError:
        return None

    try:
        with open(pdf_path, "rb") as handle:
            page = next(PDFPage.create_pages(PDFDocument(PDFParser(handle))))
            box = page.mediabox
        width = float(box[2]) - float(box[0])
        height = float(box[3]) - float(box[1])
    except Exception:
        return None

    return width / height if height else None
