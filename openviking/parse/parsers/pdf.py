# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
PDF parser for OpenViking.

Unified parser that converts PDF to Markdown then parses the result.
Supports dual strategy:
- Local: pdfplumber for direct conversion
- Remote: MinerU API for advanced conversion

This design simplifies PDF handling by delegating structure analysis
to the MarkdownParser after conversion.
"""

import asyncio
import base64
import hashlib
import io
import re
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.parse.base import (
    NodeType,
    ParseResult,
    ResourceNode,
    create_parse_result,
    lazy_import,
)
from openviking.parse.parsers.base_parser import BaseParser
from openviking.utils.zip_safe import normalize_zip_filenames
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.parser_config import PDFConfig

logger = get_logger(__name__)


class PDFParser(BaseParser):
    """
    PDF parser with dual conversion strategy.

    Converts PDF → Markdown → ParseResult using MarkdownParser.
    When available, extracts PDF bookmarks/outlines and injects them as
    markdown headings so MarkdownParser can build a hierarchical directory
    structure instead of flat numbered files.

    Strategies:
    - "local": Use pdfplumber for text and table extraction
    - "mineru": Use MinerU API for advanced PDF processing
    - "auto": Try local first, fallback to MinerU if configured

    Examples:
        >>> # Local parsing
        >>> parser = PDFParser(PDFConfig(strategy="local"))
        >>> result = await parser.parse("document.pdf")

        >>> # Remote API parsing
        >>> config = PDFConfig(
        ...     strategy="mineru",
        ...     mineru_endpoint="http://127.0.0.1:8000"
        ... )
        >>> parser = PDFParser(config)
        >>> result = await parser.parse("document.pdf")
    """

    def __init__(self, config: Optional[PDFConfig] = None):
        """
        Initialize PDF parser.

        Args:
            config: PDFConfig instance (defaults to auto strategy)
        """
        self.config = config or PDFConfig()
        self.config.validate()

        # Lazy import MarkdownParser to avoid circular imports
        self._markdown_parser = None

    def _get_markdown_parser(self):
        """Lazy import and create MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser(config=self.config)
        return self._markdown_parser

    @property
    def supported_extensions(self) -> List[str]:
        """List of supported file extensions."""
        return [".pdf"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse PDF file.

        Args:
            source: Path to PDF file
            **kwargs: Additional options (resource_name/source_name for original filename)

        Returns:
            ParseResult with document tree

        Raises:
            FileNotFoundError: If PDF file doesn't exist
            ValueError: If conversion fails with all strategies
        """
        start_time = time.time()
        pdf_path = Path(source)

        # Get resource name from kwargs, prefer original filename from upload
        resource_name = kwargs.get("resource_name") or kwargs.get("source_name")

        if not pdf_path.exists():
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(pdf_path),
                source_format="pdf",
                parser_name="PDFParser",
                parse_time=time.time() - start_time,
                warnings=[f"File not found: {pdf_path}"],
            )

        try:
            # Step 1: Convert PDF to Markdown
            markdown_content, conversion_meta = await self._convert_to_markdown(
                pdf_path,
                resource_name=resource_name,
            )

            # Step 2: Parse Markdown using MarkdownParser, pass through resource name
            md_parser = self._get_markdown_parser()
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()
            result = await md_parser.parse_content(
                markdown_content,
                source_path=str(pdf_path),
                resource_name=resource_name,
                source_name=resource_name,
                base_dir=pdf_path.parent,
                allowed_media_dirs=[storage.media_dir],
                split_content=kwargs.get("split_content", True),
            )

            # Step 3: Update metadata for PDF origin
            result.source_format = "pdf"  # Override markdown format
            result.parser_name = "PDFParser"
            result.parser_version = "2.0"
            result.parse_time = time.time() - start_time
            result.meta.update(conversion_meta)
            result.meta["pdf_strategy"] = self.config.strategy
            result.meta["intermediate_markdown_length"] = len(markdown_content)
            result.meta["intermediate_markdown_preview"] = markdown_content[:500]

            logger.info(
                f"PDF parsed successfully: {pdf_path.name} "
                f"({len(markdown_content)} chars markdown, "
                f"{result.parse_time:.2f}s)"
            )

            return result

        except Exception as e:
            logger.error(f"Failed to parse PDF {pdf_path}: {e}")
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=str(pdf_path),
                source_format="pdf",
                parser_name="PDFParser",
                parse_time=time.time() - start_time,
                warnings=[f"Failed to parse PDF: {e}"],
            )

    async def _convert_to_markdown(
        self,
        pdf_path: Path,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown using configured strategy.

        Args:
            pdf_path: Path to PDF file
            resource_name: Optional resource name for organizing saved images

        Returns:
            Tuple of (markdown_content, metadata_dict)

        Raises:
            ValueError: If all conversion strategies fail
        """
        if self.config.strategy == "local":
            return await self._convert_local(pdf_path, resource_name=resource_name)

        elif self.config.strategy == "mineru":
            return await self._convert_mineru(pdf_path, resource_name=resource_name)

        elif self.config.strategy == "auto":
            # Try local first
            try:
                return await self._convert_local(pdf_path, resource_name=resource_name)
            except Exception as e:
                logger.warning(f"Local conversion failed: {e}")

                # Fallback to MinerU if configured
                if self.config.mineru_endpoint:
                    logger.info("Falling back to MinerU API")
                    return await self._convert_mineru(pdf_path, resource_name=resource_name)
                else:
                    raise ValueError(
                        f"Local conversion failed and no MinerU endpoint configured: {e}"
                    )

        else:
            raise ValueError(f"Unknown strategy: {self.config.strategy}")

    async def _convert_local(
        self, pdf_path: Path, storage=None, resource_name: Optional[str] = None
    ) -> tuple[str, Dict[str, Any]]:
        # pdfplumber / pdfminer 的解析与图片/表格提取通常是 CPU/IO 密集且为同步实现，
        # 放到线程池中执行，避免阻塞事件循环。
        return await asyncio.to_thread(self._convert_local_sync, pdf_path, storage, resource_name)

    def _convert_local_sync(
        self, pdf_path: Path, storage=None, resource_name: Optional[str] = None
    ) -> tuple[str, Dict[str, Any]]:
        """同步版：用 pdfplumber 将 PDF 转 Markdown。

        该方法会在 :meth:`_convert_local` 中通过 asyncio.to_thread 调用。
        """
        pdfplumber = lazy_import("pdfplumber")

        # Import storage utilities
        if storage is None:
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()

        if resource_name is None:
            resource_name = pdf_path.stem

        parts = []
        meta = {
            "strategy": "local",
            "library": "pdfplumber",
            "pages_processed": 0,
            "images_extracted": 0,
            "images_deduplicated": 0,
            "tables_extracted": 0,
            "bookmarks_found": 0,
            "bookmarks_resolved": 0,
            "bookmarks_unresolved": 0,
            "headings_found": 0,
            "heading_source": "none",
        }

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                meta["total_pages"] = len(pdf.pages)

                # Extract structure (bookmarks → font fallback)
                detection_mode = self.config.heading_detection
                bookmarks = []
                raw_bookmarks = []
                heading_source = "none"

                if detection_mode in ("bookmarks", "auto"):
                    raw_bookmarks = self._extract_bookmarks(pdf)
                    meta["bookmarks_found"] = len(raw_bookmarks)
                    bookmarks = [bm for bm in raw_bookmarks if bm["page_num"] is not None]
                    meta["bookmarks_resolved"] = len(bookmarks)
                    meta["bookmarks_unresolved"] = len(raw_bookmarks) - len(bookmarks)

                    if bookmarks:
                        heading_source = "bookmarks"
                    elif raw_bookmarks:
                        logger.info(
                            "Bookmark detection found %d entries but none resolved to pages; "
                            "ignoring bookmark headings",
                            len(raw_bookmarks),
                        )

                if not bookmarks and detection_mode in ("font", "auto"):
                    bookmarks = self._detect_headings_by_font(pdf)
                    if bookmarks:
                        heading_source = "font_analysis"

                meta["headings_found"] = len(bookmarks)
                meta["heading_source"] = heading_source
                logger.info(
                    "Heading detection: source=%s, headings=%d, bookmarks=%d, resolved=%d, "
                    "unresolved=%d",
                    heading_source,
                    len(bookmarks),
                    meta["bookmarks_found"],
                    meta["bookmarks_resolved"],
                    meta["bookmarks_unresolved"],
                )

                # Group bookmarks by page_num
                bookmarks_by_page = defaultdict(list)
                for bm in bookmarks:
                    page = bm["page_num"]
                    if page is None:
                        continue
                    bookmarks_by_page[page].append(bm)

                for page_num, page in enumerate(pdf.pages, 1):
                    try:
                        # Inject headings before page text
                        page_bookmarks = bookmarks_by_page.get(page_num, [])
                        for bm in page_bookmarks:
                            heading_prefix = "#" * bm["level"]
                            parts.append(f"\n{heading_prefix} {bm['title']}\n")

                        # Extract text
                        text = page.extract_text()
                        if text and text.strip():
                            # Add page marker as HTML comment
                            parts.append(f"<!-- Page {page_num} -->\n{text.strip()}")
                            meta["pages_processed"] += 1

                        # Extract tables
                        tables = page.extract_tables()
                        for table_idx, table in enumerate(tables or []):
                            if table and len(table) > 0:
                                md_table = self._format_table_markdown(table)
                                if md_table:
                                    parts.append(
                                        f"<!-- Page {page_num} Table {table_idx + 1} -->\n{md_table}"
                                    )
                                    meta["tables_extracted"] += 1

                        # Extract images.
                        #
                        # A page can stack several image XObjects on the exact same
                        # spot — print-to-PDF producers routinely emit a background
                        # layer plus a content layer. Since extraction rasterises the
                        # page *region* rather than the XObject itself, every one of
                        # them renders to identical bytes. Skip the repeats: bbox
                        # first, which avoids the (expensive) render entirely, then a
                        # content hash as a backstop. Both sets are per-page, so a
                        # header logo repeated across pages is still kept once per
                        # page.
                        images = page.images
                        seen_boxes = set()
                        seen_digests = set()
                        for img_idx, img in enumerate(images or []):
                            try:
                                bbox_key = (
                                    round(img["x0"], 1),
                                    round(img["top"], 1),
                                    round(img["x1"], 1),
                                    round(img["bottom"], 1),
                                )
                                if bbox_key in seen_boxes:
                                    meta["images_deduplicated"] += 1
                                    continue
                                seen_boxes.add(bbox_key)

                                # Extract image using underlying PDF object
                                image_obj = self._extract_image_from_page(page, img)
                                if image_obj:
                                    # Dedup only — md5 keeps this cheap, and the
                                    # flag keeps it working on FIPS-locked hosts.
                                    digest = hashlib.md5(image_obj, usedforsecurity=False).digest()
                                    if digest in seen_digests:
                                        meta["images_deduplicated"] += 1
                                        continue
                                    seen_digests.add(digest)

                                    # Save image
                                    filename = f"page{page_num}_img{img_idx + 1}"
                                    image_path = storage.save_image(
                                        resource_name, image_obj, filename=filename
                                    )

                                    # Generate path relative to the media root.
                                    rel_path = image_path.relative_to(storage.media_dir)
                                    parts.append(
                                        f"<!-- Page {page_num} Image {img_idx + 1} -->\n"
                                        f"![Page {page_num} Image {img_idx + 1}]({rel_path})"
                                    )
                                    meta["images_extracted"] += 1
                            except Exception as img_err:
                                logger.warning(
                                    f"Failed to extract image {img_idx + 1} on page {page_num}: {img_err}"
                                )
                    finally:
                        self._release_page_cache(page)

            if not parts:
                logger.warning(f"No content extracted from {pdf_path}")
                return "", meta

            markdown_content = "\n\n".join(parts)
            logger.info(
                f"Local conversion: {meta['pages_processed']}/{meta['total_pages']} pages, "
                f"{meta['headings_found']} headings ({meta['heading_source']}, "
                f"bookmarks={meta['bookmarks_found']}, "
                f"resolved={meta['bookmarks_resolved']}), "
                f"{meta['images_extracted']} images "
                f"({meta['images_deduplicated']} duplicates skipped), "
                f"{meta['tables_extracted']} tables → "
                f"{len(markdown_content)} chars"
            )

            return markdown_content, meta

        except Exception as e:
            logger.error(f"pdfplumber conversion failed: {e}")
            raise

    @staticmethod
    def _release_page_cache(page: Any) -> None:
        """Release pdfplumber/pdfminer per-page caches when available."""
        close = getattr(page, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:
                pass

        flush_cache = getattr(page, "flush_cache", None)
        if callable(flush_cache):
            try:
                flush_cache()
            except Exception:
                pass

    def _extract_bookmarks(self, pdf) -> List[Dict[str, Any]]:
        """Extract bookmark structure from PDF outlines.

        Returns: [{level: int, title: str, page_num: int(1-based)}]
        """
        try:
            if not hasattr(pdf, "doc") or not hasattr(pdf.doc, "get_outlines"):
                return []

            outlines = list(pdf.doc.get_outlines())
            if not outlines:
                return []

            page_ref_to_num = self._build_page_number_map(pdf)

            bookmarks = []
            for level, title, dest, _action, _se in outlines:
                if not title or not title.strip():
                    continue

                page_num = None
                try:
                    if dest and len(dest) > 0:
                        page_num = self._resolve_bookmark_page(
                            dest[0], page_ref_to_num, len(pdf.pages)
                        )
                except Exception:
                    pass

                bookmarks.append(
                    {
                        "level": min(max(level, 1), 6),
                        "title": title.strip(),
                        "page_num": page_num,
                    }
                )

            return bookmarks

        except Exception as e:
            logger.warning(f"Failed to extract bookmarks: {e}")
            return []

    def _build_page_number_map(self, pdf) -> Dict[int, int]:
        """Build a lookup from PDF page object ids to 1-based page numbers.

        pdfminer outlines and link annotations reference page objects by object id.
        In pdfplumber these ids are exposed as ``page.page_obj.pageid``; some mocks
        or alternate inputs may still expose ``objid``, so we keep both.
        """
        page_ref_to_num: Dict[int, int] = {}
        for page_num, page in enumerate(pdf.pages, 1):
            page_obj = getattr(page, "page_obj", None)
            if page_obj is None:
                continue

            for attr_name in ("pageid", "objid"):
                ref_id = getattr(page_obj, attr_name, None)
                if isinstance(ref_id, int):
                    page_ref_to_num.setdefault(ref_id, page_num)

        return page_ref_to_num

    def _resolve_bookmark_page(
        self, page_ref: Any, page_ref_to_num: Dict[int, int], total_pages: int
    ) -> Optional[int]:
        """Resolve a bookmark destination to a 1-based page number."""
        ref_id = getattr(page_ref, "objid", None)
        if isinstance(ref_id, int):
            return page_ref_to_num.get(ref_id)

        if isinstance(page_ref, int):
            # 0-based integer page index (common in many PDF producers)
            candidate = page_ref + 1
            if 1 <= candidate <= total_pages:
                return candidate
            return None

        if hasattr(page_ref, "resolve"):
            resolved = page_ref.resolve()
            for attr_name in ("pageid", "objid"):
                resolved_id = getattr(resolved, attr_name, None)
                if isinstance(resolved_id, int):
                    return page_ref_to_num.get(resolved_id)

        return None

    def _detect_headings_by_font(self, pdf) -> List[Dict[str, Any]]:
        """Detect headings by font size analysis.

        Returns: [{level: int, title: str, page_num: int(1-based)}]
        """
        try:
            # Step 1: Sample font size distribution (every 5th page)
            size_counter: Counter = Counter()
            sample_pages = pdf.pages[::5]
            for page in sample_pages:
                try:
                    for char in page.chars:
                        if char["text"].strip():
                            rounded = round(char["size"] * 2) / 2
                            size_counter[rounded] += 1
                finally:
                    self._release_page_cache(page)

            if not size_counter:
                return []

            # Step 2: Determine body font size and heading font sizes
            body_size = size_counter.most_common(1)[0][0]
            min_delta = self.config.font_heading_min_delta

            heading_sizes = sorted(
                [
                    s
                    for s, count in size_counter.items()
                    if s >= body_size + min_delta and count < size_counter[body_size] * 0.5
                ],
                reverse=True,
            )

            max_levels = self.config.max_heading_levels
            heading_sizes = heading_sizes[:max_levels]

            if not heading_sizes:
                logger.debug(f"Font analysis: body_size={body_size}pt, no heading sizes found")
                return []

            size_to_level = {s: i + 1 for i, s in enumerate(heading_sizes)}
            logger.debug(
                f"Font analysis: body_size={body_size}pt, "
                f"heading_sizes={heading_sizes}, size_to_level={size_to_level}"
            )

            # Step 3: Extract heading text page by page
            headings: List[Dict[str, Any]] = []

            def flush_line(chars_to_flush: list, page_num: int) -> None:
                if not chars_to_flush:
                    return
                title = "".join(c["text"] for c in chars_to_flush).strip()
                size = round(chars_to_flush[0]["size"] * 2) / 2

                if len(title) < 2:
                    return
                if len(title) > 100:
                    return
                if title.isdigit():
                    return
                if re.match(r"^[\d\s.·…]+$", title):
                    return

                headings.append(
                    {
                        "level": size_to_level[size],
                        "title": title,
                        "page_num": page_num,
                    }
                )

            for page in pdf.pages:
                try:
                    page_num = page.page_number + 1
                    chars = sorted(page.chars, key=lambda c: (c["top"], c["x0"]))

                    current_line_chars: list = []
                    current_top = None

                    for char in chars:
                        # Performance: headings won't appear in bottom 70% of page
                        if char["top"] > page.height * 0.3:
                            flush_line(current_line_chars, page_num)
                            current_line_chars = []
                            break

                        rounded_size = round(char["size"] * 2) / 2
                        if rounded_size not in size_to_level:
                            flush_line(current_line_chars, page_num)
                            current_line_chars = []
                            current_top = None
                            continue

                        # Same line check (top offset < 2pt)
                        if current_top is not None and abs(char["top"] - current_top) > 2:
                            flush_line(current_line_chars, page_num)
                            current_line_chars = []

                        current_line_chars.append(char)
                        current_top = char["top"]

                    flush_line(current_line_chars, page_num)
                finally:
                    self._release_page_cache(page)

            # Step 4: Deduplicate - filter headers appearing on >30% of pages
            title_page_count: Counter = Counter(h["title"] for h in headings)
            total_pages = len(pdf.pages)
            header_titles = {t for t, c in title_page_count.items() if c > total_pages * 0.3}
            headings = [h for h in headings if h["title"] not in header_titles]

            logger.debug(
                f"Font heading detection: {len(headings)} headings found "
                f"(filtered {len(header_titles)} header titles)"
            )
            return headings

        except Exception as e:
            logger.warning(f"Failed to detect headings by font: {e}")
            return []

    def _extract_image_from_page(self, page, img_info: dict) -> Optional[bytes]:
        """
        Extract a PDF image as valid PNG bytes.

        Renders the image's bounding box on the page to a raster PNG via
        pdfplumber's ``crop().to_image()`` instead of returning the raw decoded
        XObject stream (which is not a valid image file and cannot be opened).

        Args:
            page: pdfplumber page object
            img_info: Image metadata from page.images

        Returns:
            PNG-encoded image bytes or None if extraction fails
        """
        try:
            # pdfplumber coordinates: ``top`` is measured from the top of the page.
            bbox = (
                max(0, img_info["x0"]),
                max(0, img_info["top"]),
                min(page.width, img_info["x1"]),
                min(page.height, img_info["bottom"]),
            )

            # Skip degenerate / zero-area boxes that cannot be cropped.
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                return None

            cropped = page.crop(bbox)
            page_image = cropped.to_image(resolution=self.config.image_resolution)

            buffer = io.BytesIO()
            page_image.save(buffer, format="PNG")
            return buffer.getvalue()

        except Exception as e:
            logger.debug(f"Image extraction error: {e}")
            return None

    def _mineru_auth_headers(self) -> Dict[str, str]:
        """Authorization headers for the online MinerU API (empty when unset)."""
        if self.config.mineru_token:
            return {"Authorization": f"Bearer {self.config.mineru_token}"}
        return {}

    def _mineru_base_url(self) -> str:
        return self.config.mineru_endpoint.rstrip("/")

    def _mineru_file_parse_url(self) -> str:
        """Upload URL: ``<endpoint>/file_parse`` unless the endpoint already ends with it."""
        base = self._mineru_base_url()
        return base if base.endswith("/file_parse") else f"{base}/file_parse"

    async def _mineru_post(
        self, client, url: str, pdf_path: Path, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """POST the PDF as multipart and return the parsed JSON body."""
        with open(pdf_path, "rb") as f:
            files = {"files": (pdf_path.name, f, "application/pdf")}
            logger.info(f"Calling MinerU API: {url}")
            response = await client.post(
                url,
                files=files,
                data=data,
                headers=self._mineru_auth_headers(),
            )
            response.raise_for_status()
        return response.json()

    @staticmethod
    def _is_http_404(exc: Exception) -> bool:
        status = getattr(exc, "response", None)
        return getattr(status, "status_code", None) == 404

    @staticmethod
    def _mineru_payload_data(result: Dict[str, Any]) -> Dict[str, Any]:
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _mineru_response_flavor(result: Dict[str, Any]) -> Optional[str]:
        """Classify a create/submit response as one of the three protocols.

        Returns ``"v1-sync"``, ``"v2-tasks"``, ``"online-batch"`` or None when
        the shape is unrecognized.
        """
        data = PDFParser._mineru_payload_data(result)
        if isinstance(result.get("task_id"), str) and isinstance(
            result.get("status_url") or result.get("result_url"), str
        ):
            return "v2-tasks"
        if isinstance(data.get("batch_id"), str):
            return "online-batch"
        if result.get("status") == "completed" and isinstance(
            result.get("results"), dict
        ):
            return "v1-sync"
        return None

    async def _convert_mineru(
        self,
        pdf_path: Path,
        storage=None,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """
        Convert PDF to Markdown via the MinerU API.

        Three protocol flavors are supported, selected by
        ``PDFConfig.mineru_api_mode``:

        - ``"sync"``: the legacy self-hosted single-shot contract — one
          ``POST <endpoint>/file_parse`` answering inline with
          ``{"status": "completed", "results": ...}``.
        - ``"async"``: the task-based contracts — the current self-hosted
          ``POST <endpoint>/tasks`` API (202 + ``status_url``/``result_url``)
          and the online batch API (``/extract/task/batch`` + polling
          ``/extract-results/batch/{id}``, Bearer-token auth). Both deliver
          the markdown inside a zip archive. The self-hosted endpoint is
          probed first; a 404 falls through to the online endpoint.
        - ``"auto"`` (default): the flavor is detected — the inline POST is
          attempted first and a 404 (or a task-shaped response) switches to
          the task flow, so existing v1 deployments keep working unchanged.

        Args:
            pdf_path: Path to PDF file
            storage: Media storage used to persist extracted images; defaults to
                the configured storage if None
            resource_name: Resource name under which extracted images are saved;
                defaults to the PDF stem

        Returns:
            Tuple of (markdown_content, metadata) where metadata includes
            strategy, endpoint, api_mode, api_version, backend, task_id,
            processing_time (seconds) and images_saved

        Raises:
            ValueError: If MinerU endpoint is not configured, or the task does
                not complete
            Exception: If the API call fails
        """
        httpx = lazy_import("httpx")

        if not self.config.mineru_endpoint:
            raise ValueError("MinerU endpoint not configured")

        meta: Dict[str, Any] = {
            "strategy": "mineru",
            "endpoint": self.config.mineru_endpoint,
            "api_version": None,
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.mineru_timeout) as client:
                mode = self.config.mineru_api_mode
                if mode != "async":
                    data: Dict[str, Any] = dict(self.config.mineru_bodys or {})
                    # MinerU must return extracted images for the markdown refs.
                    data["return_images"] = True
                    try:
                        result = await self._mineru_post(
                            client, self._mineru_file_parse_url(), pdf_path, data
                        )
                    except Exception as exc:
                        if mode == "sync" or not self._is_http_404(exc):
                            raise
                        # auto: /file_parse is gone -> task-based protocol
                        result = None
                    if result is not None:
                        flavor = self._mineru_response_flavor(result)
                        if mode == "sync" or flavor in (None, "v1-sync"):
                            meta["api_mode"] = "v1-sync"
                            return await self._convert_mineru_v1(
                                result, pdf_path, meta,
                                storage=storage, resource_name=resource_name,
                            )
                        return await self._mineru_continue_task(
                            client, result, meta, pdf_path,
                            storage=storage, resource_name=resource_name,
                        )
                return await self._mineru_run_task_flow(
                    client, meta, pdf_path,
                    storage=storage, resource_name=resource_name,
                )
        except Exception as e:
            logger.error(f"MinerU API call failed: {e}")
            raise

    async def _mineru_run_task_flow(
        self, client, meta: Dict[str, Any], pdf_path: Path, **kwargs
    ) -> tuple[str, Dict[str, Any]]:
        """Explicit async mode: probe the task endpoints until one accepts."""
        errors = []
        base = self._mineru_base_url()
        for url, kind, data in (
            (f"{base}/tasks", "v2-tasks", self._mineru_v2_form()),
            (f"{base}/extract/task/batch", "online-batch", self._mineru_online_form()),
        ):
            try:
                result = await self._mineru_post(client, url, pdf_path, data)
            except Exception as exc:
                if self._is_http_404(exc):
                    errors.append(f"{url}: 404")
                    continue
                raise
            flavor = self._mineru_response_flavor(result)
            if flavor in (None, kind):
                return await self._mineru_continue_task(
                    client, result, meta, pdf_path, **kwargs
                )
            errors.append(f"{url}: unexpected response shape")
        raise ValueError(
            "No MinerU task endpoint responded on "
            f"{base} (tried /tasks, /extract/task/batch; {errors}). "
            "Check pdf.mineru_endpoint and, for the online API, pdf.mineru_token."
        )

    def _mineru_v2_form(self) -> Dict[str, Any]:
        """Form fields for the current self-hosted /tasks API."""
        data: Dict[str, Any] = {
            "return_md": "true",
            "return_images": "true",
            "response_format_zip": "true",
        }
        data.update(self.config.mineru_bodys or {})
        return data

    def _mineru_online_form(self) -> Dict[str, Any]:
        """Form fields for the online batch API (pass-through of mineru_bodys)."""
        return dict(self.config.mineru_bodys or {})

    async def _mineru_continue_task(
        self,
        client,
        result: Dict[str, Any],
        meta: Dict[str, Any],
        pdf_path: Path,
        storage=None,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Drive an already-submitted task to completion and unpack the zip."""
        flavor = self._mineru_response_flavor(result) or "v2-tasks"
        meta["api_mode"] = flavor
        base = self._mineru_base_url()

        if flavor == "v2-tasks":
            task_id = result["task_id"]
            meta["task_id"] = task_id
            status_url = self._mineru_absolute(result.get("status_url"), base)
            result_url = self._mineru_absolute(result.get("result_url"), base)
            logger.info(f"MinerU task {task_id} submitted; polling {status_url}")
            deadline = time.monotonic() + self.config.mineru_timeout
            await self._mineru_poll(
                client, status_url, deadline,
                done_when=lambda body: body.get("status") == "completed",
                busy_when=lambda body: body.get("status") in ("pending", "processing"),
                failure_detail=self._mineru_failure_detail,
            )
            zip_url = result_url
        else:  # online-batch
            batch_id = self._mineru_payload_data(result)["batch_id"]
            meta["task_id"] = batch_id
            poll_url = f"{base}/extract-results/batch/{batch_id}"
            logger.info(f"MinerU online batch {batch_id} submitted; polling {poll_url}")
            deadline = time.monotonic() + self.config.mineru_timeout
            await self._mineru_poll(
                client, poll_url, deadline,
                done_when=self._mineru_online_done,
                busy_when=self._mineru_online_busy,
                failure_detail=self._mineru_online_failure_detail,
            )
            zip_url = self._mineru_online_zip_url(
                await self._mineru_get_json(client, poll_url)
            )
            if not zip_url:
                raise ValueError(
                    "MinerU online task completed but no zip url found in "
                    f"{poll_url} response"
                )

        zip_bytes = await self._mineru_download_zip(client, zip_url)
        markdown_content, meta = self._mineru_unpack_zip(
            zip_bytes, pdf_path, meta,
            storage=storage, resource_name=resource_name,
        )
        logger.info(f"MinerU {flavor} conversion: {len(markdown_content)} chars")
        return markdown_content, meta

    @staticmethod
    def _mineru_absolute(url: Optional[str], base: str) -> str:
        if url and url.startswith(("http://", "https://")):
            return url
        return base + (url or "")

    async def _mineru_poll(
        self, client, url: str, deadline: float,
        done_when, busy_when, failure_detail,
    ) -> None:
        """Poll until ``done_when`` holds; raise on failure or timeout."""
        last_state = None
        while time.monotonic() < deadline:
            body = await self._mineru_get_json(client, url)
            if done_when(body):
                return
            if busy_when(body):
                state = repr(body)[:120]
                if state != last_state:
                    logger.info(f"MinerU task still busy: {state}")
                    last_state = state
                await asyncio.sleep(3.0)
                continue
            raise ValueError(
                f"MinerU task failed: {failure_detail(body)}"
            )
        raise TimeoutError(
            f"MinerU task did not finish within {self.config.mineru_timeout}s: {url}"
        )

    async def _mineru_get_json(self, client, url: str) -> Dict[str, Any]:
        response = await client.get(url, headers=self._mineru_auth_headers())
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _mineru_online_result_entry(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = PDFParser._mineru_payload_data(body)
        entries = data.get("extract_result")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            return entries[0]
        return None

    @classmethod
    def _mineru_online_done(cls, body: Dict[str, Any]) -> bool:
        entry = cls._mineru_online_result_entry(body)
        return bool(entry) and entry.get("state") == "done"

    @classmethod
    def _mineru_online_busy(cls, body: Dict[str, Any]) -> bool:
        entry = cls._mineru_online_result_entry(body)
        return bool(entry) and entry.get("state") in ("waiting", "pending", "running")

    @classmethod
    def _mineru_online_failure_detail(cls, body: Dict[str, Any]) -> str:
        entry = cls._mineru_online_result_entry(body) or {}
        for key in ("err_msg", "err_no", "state", "message", "msg"):
            if entry.get(key):
                return f"{key}={entry[key]}"
        return cls._mineru_failure_detail(body)

    @staticmethod
    def _mineru_failure_detail(body: Dict[str, Any]) -> str:
        for key in ("msg", "message", "error", "err_msg"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
        return repr(body)[:200]

    @classmethod
    def _mineru_online_zip_url(cls, body: Dict[str, Any]) -> Optional[str]:
        entry = cls._mineru_online_result_entry(body) or {}
        for key in ("full_zip_url", "zip_url", "result_url"):
            value = entry.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    async def _mineru_download_zip(self, client, zip_url: str) -> bytes:
        """Download the result archive (signed urls may already carry auth)."""
        response = await client.get(
            zip_url,
            headers=self._mineru_auth_headers(),
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.content

    def _mineru_unpack_zip(
        self,
        zip_bytes: bytes,
        pdf_path: Path,
        meta: Dict[str, Any],
        storage=None,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Unpack the zip result: locate the markdown and persist images.

        The archive layout follows the standard MinerU export: a markdown file
        (``full.md`` preferred) plus an ``images/`` directory it references.
        """
        from openviking.utils.zip_safe import safe_extract_zip

        import zipfile

        with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
            tmp_dir = Path(tmp)
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            normalize_zip_filenames(zf)
            safe_extract_zip(zf, tmp_dir)
            zf.close()

            md_path = self._mineru_pick_markdown(tmp_dir)
            if md_path is None:
                raise ValueError(
                    f"No markdown file found in MinerU zip result extracted to {tmp_dir}"
                )
            markdown_content = md_path.read_text(encoding="utf-8", errors="replace")
            root_dir = md_path.parent

            meta["api_version"] = "task-zip"

            if not markdown_content:
                logger.warning(f"MinerU returned empty content for {pdf_path}")
                return markdown_content, meta

            if storage is None:
                from openviking_cli.utils.storage import get_storage

                storage = get_storage()

            if resource_name is None:
                resource_name = pdf_path.stem

            # Persist every shipped image and rewrite the markdown references
            # (``images/<name>``) to the stored relative paths.
            repl: Dict[str, str] = {}
            media_dir = storage.media_dir
            images_dir = root_dir / "images"
            if images_dir.is_dir():
                for img in sorted(images_dir.iterdir()):
                    if not img.is_file():
                        continue
                    try:
                        image_path = storage.save_image(
                            resource_name,
                            img.read_bytes(),
                            filename=img.stem,
                            extension=img.suffix or ".png",
                        )
                        repl[f"images/{img.name}"] = (
                            image_path.relative_to(media_dir).as_posix()
                        )
                    except Exception as img_err:
                        logger.warning(f"Failed to save MinerU image {img.name}: {img_err}")

            if repl:
                ref_pattern = re.compile(
                    "|".join(
                        re.escape(ref) for ref in sorted(repl, key=len, reverse=True)
                    )
                )
                markdown_content = ref_pattern.sub(
                    lambda m: repl[m.group(0)], markdown_content
                )
                meta["images_saved"] = len(repl)

        return markdown_content, meta

    @staticmethod
    def _mineru_pick_markdown(root_dir: Path) -> Optional[Path]:
        """Pick the document markdown from an extracted MinerU zip.

        Preference order: ``full.md`` next to the images dir, any single
        ``*.md`` in a flat layout, else the shallowest ``*.md``.
        """
        candidates = [p for p in root_dir.rglob("*.md") if p.is_file()]
        if not candidates:
            return None
        for p in candidates:
            if p.name == "full.md":
                return p
        if len(candidates) == 1:
            return candidates[0]
        return min(candidates, key=lambda p: (len(p.parts), -p.stat().st_size))

    async def _convert_mineru_v1(
        self,
        result: Dict[str, Any],
        pdf_path: Path,
        meta: Dict[str, Any],
        storage=None,
        resource_name: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Handle the legacy inline response (``md_content`` + base64 images)."""
        if result.get("status") != "completed":
            raise ValueError(f"MinerU task not completed: {result.get('status')}")

        results = result.get("results") or {}
        file_result = results.get(pdf_path.name) or next(iter(results.values()), {})
        markdown_content = file_result.get("md_content") or ""

        # Extract metadata from response
        meta["api_version"] = result.get("version")
        meta["backend"] = result.get("backend")
        meta["task_id"] = result.get("task_id")
        started_at, completed_at = result.get("started_at"), result.get("completed_at")
        if started_at and completed_at:
            meta["processing_time"] = (
                parse_iso_datetime(completed_at) - parse_iso_datetime(started_at)
            ).total_seconds()

        if not markdown_content:
            logger.warning(f"MinerU returned empty content for {pdf_path}")
            return markdown_content, meta

        if storage is None:
            from openviking_cli.utils.storage import get_storage

            storage = get_storage()

        if resource_name is None:
            resource_name = pdf_path.stem

        # MinerU embeds images as base64 data-URLs, referenced from markdown
        # as `images/<filename>`; save them into the media store and rewrite
        # the references to the stored relative paths.
        repl: Dict[str, str] = {}
        media_dir = storage.media_dir
        for img_name, data_url in (file_result.get("images") or {}).items():
            try:
                # data URL form: "data:image/jpeg;base64,<b64>"
                image_bytes = base64.b64decode(data_url.split(",", 1)[-1])
                img_path = Path(img_name)
                image_path = storage.save_image(
                    resource_name,
                    image_bytes,
                    filename=img_path.stem,
                    extension=img_path.suffix or ".png",
                )
                repl[f"images/{img_name}"] = image_path.relative_to(media_dir).as_posix()
            except Exception as img_err:
                logger.warning(f"Failed to save MinerU image {img_name}: {img_err}")

        if repl:
            # Single pass over the markdown replaces every known reference;
            # unknown ``images/...`` text is left untouched.
            ref_pattern = re.compile(
                "|".join(re.escape(ref) for ref in sorted(repl, key=len, reverse=True))
            )
            markdown_content = ref_pattern.sub(lambda m: repl[m.group(0)], markdown_content)
            meta["images_saved"] = len(repl)

        logger.info(f"MinerU conversion: {len(markdown_content)} chars")

        return markdown_content, meta

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse PDF content string.

        Note: This method is not recommended for PDFParser as it requires
        file path for conversion tools. Use parse() with file path instead.

        Args:
            content: PDF content (not supported)
            source_path: Optional source path
            **kwargs: Additional options

        Raises:
            NotImplementedError: PDFParser requires file path
        """
        raise NotImplementedError(
            "PDFParser does not support parsing content strings. "
            "Use parse() with a file path instead."
        )
