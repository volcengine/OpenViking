# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Markdown parser for OpenViking (v5.0).

This parser implements the new simplified architecture:
- Parse structure and create directory structure directly in VikingFS
- No LLM calls during parsing (semantic generation moved to SemanticQueue)
- Support mixed directory structure (files + subdirectories)
- Small sections (< 800 tokens) are merged with adjacent sections

The parser handles scenarios:
1. Small files (< 4000 tokens) → save as single file with original name
2. Large files with sections → split by sections with merge logic
3. Sections with subsections → section becomes directory
4. Small sections (< 800 tokens) → merged with adjacent sections
5. Oversized sections without subsections → split by paragraphs
"""

import asyncio
import hashlib
import io
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from openviking.parse.accessors.mime_types import IANA_MEDIA_TYPE_TO_EXTENSION
from openviking.parse.base import NodeType, ParseResult, ResourceNode, create_parse_result
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.constants import (
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    IGNORE_EXTENSIONS,
)
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

# All known valid extensions - only these should be stripped when getting stem
KNOWN_EXTENSIONS: set[str] = set()
for extensions in IANA_MEDIA_TYPE_TO_EXTENSION.values():
    KNOWN_EXTENSIONS.update(extensions)
KNOWN_EXTENSIONS.update(CODE_EXTENSIONS)
KNOWN_EXTENSIONS.update(DOCUMENTATION_EXTENSIONS)
KNOWN_EXTENSIONS.update(IGNORE_EXTENSIONS)

# Markdown link/image in one pass: optional leading "!" marks images.
# Scope (per spec, YAGNI): only inline [..](..)/![..](..) are handled. Reference-style
# links, HTML, and links inside fenced/indented code blocks are NOT excluded — a
# code-block link to an existing relative .md could be rewritten. Acceptable for now.
# NOTE: `[^)]+` stops at the first ")", so a target whose filename literally
# contains ")" is captured truncated; it then fails the on-disk existence check
# and is left unchanged (conservative). Such filenames are unsupported.
_MD_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)]+)\)")
# Splits a link target into its path part and optional "#fragment"/"?query" suffix.
_MD_FRAGMENT_RE = re.compile(r"^([^#?]*)([#?].*)?$")
# Extensions whose targets become directories on ingest (see _rewrite_single_link).
_MD_DIR_EXTS = {".md", ".markdown", ".mdown", ".mkd"}


def _smart_stem(path_or_name: str | Path) -> str:
    """Get the stem of a filename, but only strip known valid extensions.

    For filenames like "2601.00014" where ".00014" is not a valid extension,
    returns the full name instead of just "2601".

    Args:
        path_or_name: Path object or string filename

    Returns:
        Stem with only known extensions stripped
    """
    path = Path(path_or_name)
    suffix = path.suffix.lower()

    if suffix in KNOWN_EXTENSIONS:
        return path.stem

    # If the suffix is not a known extension, treat the whole name as the stem
    return path.name


logger = get_logger(__name__)


def _gh_slug(text: str) -> str:
    """GitHub-style heading slug: lowercase, strip punctuation, spaces→'-', keep CJK."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


class _InMemoryFS:
    """Minimal write-only VikingFS stand-in to capture a parser's split layout
    without touching the real store (used for in-memory anchor location)."""

    def __init__(self) -> None:
        self.files: Dict[str, Any] = {}
        self._n = 0

    def create_temp_uri(self) -> str:
        self._n += 1
        return f"mem://t{self._n}"

    async def mkdir(self, uri: str, exist_ok: bool = False, **kw) -> None:
        return None

    async def write_file(self, uri: str, content: Any) -> None:
        self.files[uri] = content


if TYPE_CHECKING:
    pass


class MarkdownParser(BaseParser):
    """
    Markdown parser for OpenViking v5.0.

    Supports: .md, .markdown, .mdown, .mkd

    Features:
    - Direct directory structure creation in VikingFS
    - No LLM calls during parsing (moved to SemanticQueue)
    - Mixed directory structure support (files + subdirectories)
    - Smart content splitting for oversized sections
    - Size-based parsing decisions
    """

    # Configuration constants
    DEFAULT_MAX_SECTION_SIZE = 2048  # Maximum tokens per section
    DEFAULT_MIN_SECTION_TOKENS = 512  # Minimum tokens to create a separate section
    MAX_MERGED_FILENAME_LENGTH = 32  # Maximum length for merged section filenames

    # Image validation constants
    IMAGE_MIN_SIDE = 14  # Minimum width/height in pixels (exclusive)
    IMAGE_MIN_PIXELS = 196  # Minimum width * height
    IMAGE_MAX_PIXELS = 36000000  # Maximum width * height
    IMAGE_MIN_ASPECT_RATIO = 1 / 150  # Minimum width/height ratio
    IMAGE_MAX_ASPECT_RATIO = 150  # Maximum width/height ratio
    IMAGE_MAX_FILE_BYTES = 10 * 1024 * 1024  # Local file path limit: 10 MB

    def __init__(
        self,
        extract_frontmatter: bool = True,
        config: Optional[ParserConfig] = None,
    ):
        """
        Initialize the enhanced markdown parser.

        Args:
            extract_frontmatter: Whether to extract YAML frontmatter
            config: Parser configuration (uses default if None)
        """
        self.extract_frontmatter = extract_frontmatter
        self.config = config or ParserConfig()

        # Compile regex patterns for better performance
        self._heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        self._code_block_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        self._inline_code_pattern = re.compile(r"`([^`]+)`")
        self._link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        self._image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
        self._list_pattern = re.compile(r"^(\s*)[-*+]\s+(.+)$", re.MULTILINE)
        self._numbered_list_pattern = re.compile(r"^(\s*)\d+\.\s+(.+)$", re.MULTILINE)
        self._frontmatter_pattern = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
        self._html_comment_pattern = re.compile(r"<!--.*?-->", re.DOTALL)
        self._indented_code_pattern = re.compile(r"^(?:    |\t).+$", re.MULTILINE)

        # Cache for VikingFS instance
        self._viking_fs = None

        # Relative-link rewrite context, set per parse_content() call.
        self._rewrite_ctx = None

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".md", ".markdown", ".mdown", ".mkd"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse from file path or content string.

        Args:
            source: File path or content string
            instruction: Processing instruction, guides LLM how to understand the resource
            **kwargs: Runtime options (e.g., base_dir for resolving relative paths)

        Returns:
            ParseResult with document tree (including temp_dir_path)
        """
        path = Path(source)

        if path.exists():
            content = self._read_file(path)
            # Pass base_dir for resolving relative image paths
            return await self.parse_content(
                content,
                source_path=str(path),
                instruction=instruction,
                base_dir=path.parent,
                **kwargs,
            )
        else:
            # Treat as raw content string
            return await self.parse_content(str(source), instruction=instruction, **kwargs)

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        base_dir: Optional[Path] = None,
        allowed_media_dirs: Optional[List[Path]] = None,
        **kwargs,
    ) -> ParseResult:
        """
        Parse markdown content and create directory structure in VikingFS.

        New architecture (v5.0):
        - Directly create files and directories in temp VikingFS
        - No LLM calls during parsing (semantic generation moved to SemanticQueue)
        - Support mixed directory structure (files + subdirectories)

        Args:
            content: Markdown content string
            source_path: Optional source file path
            instruction: Processing instruction (unused in v5.0)
            base_dir: Base directory for relative paths
            allowed_media_dirs: Additional directories from which derived media
                (e.g. images extracted by the PDF/DOC parser) may be read. The
                caller is responsible for ensuring these belong to the current
                input resource's lifecycle.
            **kwargs: Additional runtime options, incl. resource_name/source_name and
                enable_link_rewrite/link_rewrite_root (the latter set by DirectoryParser)

        Returns:
            ParseResult with temp_dir_path (Viking URI)
        """
        start_time = time.time()
        warnings: List[str] = []
        meta: Dict[str, Any] = {}

        try:
            logger.debug(f"[MarkdownParser] Starting parse for: {source_path or 'content string'}")

            # Extract frontmatter if present
            if self.extract_frontmatter:
                content, frontmatter = self._extract_frontmatter(content)
                if frontmatter:
                    meta["frontmatter"] = frontmatter
                    logger.debug(
                        f"[MarkdownParser] Extracted frontmatter: {list(frontmatter.keys())}"
                    )

            # Collect metadata
            # images = list(self._image_pattern.finditer(content))
            # image_count = len(images)
            # lines = content.split("\n")
            # text_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
            # line_count = len(text_lines)

            # meta["image_count"] = image_count
            # meta["line_count"] = line_count

            # Create temporary directory
            viking_fs = self._get_viking_fs()
            temp_uri = self._create_temp_uri()
            await viking_fs.mkdir(temp_uri)
            logger.debug(f"[MarkdownParser] Created temp directory: {temp_uri}")

            explicit_name = kwargs.get("resource_name")
            if not explicit_name and kwargs.get("source_name"):
                explicit_name = _smart_stem(kwargs["source_name"])

            # Preserve the original uploaded filename when available instead of
            # the temp upload name (e.g. upload_<uuid>.txt).
            doc_title = meta.get("frontmatter", {}).get(
                "title",
                explicit_name
                if explicit_name
                else _smart_stem(source_path)
                if source_path
                else "Document",
            )

            # Create root directory
            root_dir = f"{temp_uri}/{self._sanitize_for_path(doc_title)}"

            # Set up relative-link rewrite context consumed by _write_section.
            # Link rewriting is opt-in via kwargs (DirectoryParser sets these);
            # parse_content keeps the base **kwargs signature rather than adding
            # dedicated parameters, so read them from kwargs here.
            self._rewrite_ctx = {
                "enabled": bool(kwargs.get("enable_link_rewrite", False)),
                "source_path": source_path,
                "doc_name": self._sanitize_for_path(doc_title),
                "root_dir": root_dir,
                # Rewrite is driven only by DirectoryParser, which passes the ingest
                # root. Single-file ingestion never sets this, so it never rewrites.
                "import_root": kwargs.get("link_rewrite_root"),
            }

            # Find all headings
            headings = self._find_headings(content)
            logger.info(f"[MarkdownParser] Found {len(headings)} headings")

            # Parse and create directory structure
            await self._parse_and_create_structure(
                content,
                headings,
                root_dir,
                source_path,
                doc_name=self._sanitize_for_path(doc_title),
            )

            # Ingest local image files, placing each image next to the
            # markdown file that references it.
            await self._ingest_local_images(root_dir, base_dir, allowed_media_dirs)

            parse_time = time.time() - start_time
            logger.info(f"[MarkdownParser] Parse completed in {parse_time:.2f}s")

            # Create dummy root node for compatibility
            root = ResourceNode(
                type=NodeType.ROOT,
                title=doc_title,
                level=0,
                meta=meta.get("frontmatter", {}),
            )

            result = create_parse_result(
                root=root,
                source_path=source_path,
                source_format="markdown",
                parser_name="MarkdownParser",
                parse_time=parse_time,
                meta=meta,
                warnings=warnings,
            )

            result.temp_dir_path = temp_uri

            return result

        except Exception as e:
            logger.error(f"[MarkdownParser] Parse failed: {e}", exc_info=True)
            raise
        finally:
            # Rewrite context lives exactly one parse_content call.
            self._rewrite_ctx = None

    # ========== Helper Methods ==========

    def _extract_frontmatter(self, content: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Extract YAML frontmatter from content.

        Args:
            content: Markdown content

        Returns:
            Tuple of (content without frontmatter, frontmatter dict or None)
        """
        match = self._frontmatter_pattern.match(content)
        if not match:
            return content, None

        frontmatter_text = match.group(1)
        content_without_frontmatter = content[match.end() :]

        # Parse YAML (simple key: value parsing)
        frontmatter = {}
        for line in frontmatter_text.split("\n"):
            line = line.strip()
            if ":" in line:
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()

        return content_without_frontmatter, frontmatter

    def _find_headings(self, content: str) -> List[Tuple[int, int, str, int]]:
        """
        Find all headings, excluding code blocks, HTML comments, and escaped characters.

        Args:
            content: Markdown content

        Returns:
            List of tuples (start_pos, end_pos, title, level)
        """
        # Collect all excluded ranges
        excluded_ranges = []

        # Triple backtick code blocks
        for match in self._code_block_pattern.finditer(content):
            excluded_ranges.append((match.start(), match.end()))

        # HTML comments <!-- ... -->
        for match in self._html_comment_pattern.finditer(content):
            excluded_ranges.append((match.start(), match.end()))

        # Four-space or tab indented code blocks
        for match in self._indented_code_pattern.finditer(content):
            excluded_ranges.append((match.start(), match.end()))

        # Find headings, skipping excluded ranges and escaped #
        headings = []
        for match in self._heading_pattern.finditer(content):
            pos = match.start()

            # Check if in excluded range
            in_excluded = any(start <= pos < end for start, end in excluded_ranges)
            if in_excluded:
                continue

            # Check if escaped \#
            if pos > 0 and content[pos - 1] == "\\":
                continue

            level = len(match.group(1))
            title = match.group(2).strip()
            headings.append((match.start(), match.end(), title, level))

        return headings

    def _smart_split_content(self, content: str, max_size: int) -> List[str]:
        """
        Split oversized content by paragraphs, force split single oversized paragraphs.

        Enforces both a token estimate limit (max_size) and a hard character limit
        (self.config.max_section_chars) to guard against token estimation errors.

        Args:
            content: Content to split
            max_size: Maximum size per part (in tokens)

        Returns:
            List of content parts
        """
        max_chars = self.config.max_section_chars
        if max_chars <= 0:
            # Char limit disabled (misconfigured); fall back to token-only splitting
            max_chars = len(content) + 1
        paragraphs = content.split("\n\n")
        parts = []
        current = ""
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self._estimate_token_count(para)
            para_len = len(para)

            # Single paragraph too long (by tokens or chars): force split by characters.
            # If the already accumulated prefix is very short, merge it into this
            # oversized paragraph first so we do not create a low-value tiny chunk
            # like `section_1.md` that only contains the heading/introduction.
            if para_tokens > max_size or para_len > max_chars:
                if current:
                    if current_tokens < self.DEFAULT_MIN_SECTION_TOKENS:
                        para = current + "\n\n" + para
                    else:
                        parts.append(current.strip())
                    current = ""
                    current_tokens = 0
                for i in range(0, len(para), max_chars):
                    parts.append(para[i : i + max_chars].strip())
            elif (
                current_tokens + para_tokens > max_size or len(current) + len(para) + 2 > max_chars
            ) and current:
                parts.append(current.strip())
                current = para
                current_tokens = para_tokens
            else:
                current = current + "\n\n" + para if current else para
                current_tokens += para_tokens

        if current.strip():
            # Avoid emitting a tiny trailing chunk when all earlier content has
            # already been split out (for example, a huge paragraph followed by
            # a short "no data" tail). Fold the tail back into the previous part.
            if parts and current_tokens < self.DEFAULT_MIN_SECTION_TOKENS:
                parts[-1] = f"{parts[-1]}\n\n{current.strip()}".strip()
            else:
                parts.append(current.strip())

        return parts if parts else [content]

    async def _ingest_local_images(
        self,
        root_dir: str,
        base_dir: Optional[Path] = None,
        allowed_media_dirs: Optional[List[Path]] = None,
    ) -> None:
        """
        Scan every processed markdown file under ``root_dir`` and copy the local
        images they reference into VikingFS, next to the markdown file itself.

        Images are processed file by file: for each markdown file, the local
        image references it contains are resolved and copied into the same
        directory as that markdown file. A single ``.image_mappings.json`` file,
        keyed by the markdown file's path relative to ``root_dir``, is written at
        ``root_dir`` for ``rewrite_image_uris`` to consume.

        Args:
            root_dir: Root directory URI in VikingFS containing the markdown files
            base_dir: Base directory for resolving relative paths
            allowed_media_dirs: Additional directories from which derived media
                may be read (passed through to ``_resolve_image_path``)
        """
        viking_fs = self._get_viking_fs()

        # Find all processed markdown files under the root directory
        glob_result = await viking_fs.glob("*.md", uri=root_dir)
        md_uris = glob_result.get("matches", [])
        if not md_uris:
            return

        root_prefix = root_dir.rstrip("/")

        # mapping: rel_md_path -> {original_path_str -> unique_filename}
        mappings: Dict[str, Dict[str, str]] = {}

        for md_uri in md_uris:
            try:
                content = await viking_fs.read_file(md_uri)
            except Exception:
                logger.warning(f"[MarkdownParser] Failed to read markdown file: {md_uri}")
                continue

            # Collect all image references in this markdown file
            images = list(self._image_pattern.finditer(content))
            if not images:
                continue

            # Resolve local image paths, skipping remote URIs and duplicates
            local_images = []
            origin_images_links = []
            seen_paths = set()

            for match in images:
                path_str = match.group(2)

                # Skip remote URIs
                if self._is_remote_uri(path_str):
                    continue

                resolved_path = self._resolve_image_path(path_str, base_dir, allowed_media_dirs)
                if resolved_path is None:
                    logger.warning(f"[MarkdownParser] Image file not found: {path_str}")
                    continue

                # Skip duplicates within the same markdown file
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)
                origin_images_links.append(path_str)
                local_images.append(resolved_path)

            if not local_images:
                continue

            # Target directory is the directory containing the markdown file
            md_dir = md_uri.rsplit("/", 1)[0]

            # Copy each local image next to the markdown file, deduplicating names
            used_names: set[str] = set()
            file_mappings: Dict[str, str] = {}  # original_path_str -> unique_filename
            for origin_link, resolved_path in zip(origin_images_links, local_images):
                try:
                    if not await asyncio.to_thread(resolved_path.exists):
                        logger.warning(f"[MarkdownParser] Image file not found: {resolved_path}")
                        continue

                    # Read image bytes
                    image_bytes = await asyncio.to_thread(resolved_path.read_bytes)

                    # Validate pixel size and file size; skip non-compliant images
                    if not await asyncio.to_thread(self._is_valid_image, image_bytes, resolved_path):
                        continue

                    # Get filename and deduplicate
                    filename = resolved_path.name
                    unique_filename = self._deduplicate_filename(filename, used_names)
                    used_names.add(unique_filename)

                    # Write next to the markdown file
                    viking_path = f"{md_dir}/{unique_filename}"
                    await viking_fs.write_file_bytes(viking_path, image_bytes)
                    logger.debug(f"[MarkdownParser] Copied image to VikingFS: {viking_path}")

                    # Record mapping for post-commit rewrite
                    file_mappings[origin_link] = unique_filename

                except Exception as e:
                    logger.warning(f"[MarkdownParser] Failed to ingest image {resolved_path}: {e}")

            if file_mappings:
                rel_md_path = md_uri[len(root_prefix) + 1 :] if md_uri.startswith(root_prefix) else md_uri
                mappings[rel_md_path] = file_mappings

        # Write a single mapping file at the root directory for rewrite_image_uris
        if mappings:
            import json
            await viking_fs.write_file(
                f"{root_prefix}/.image_mappings.json",
                json.dumps(mappings, ensure_ascii=False),
            )

    def _resolve_image_path(
        self,
        path_str: str,
        base_dir: Optional[Path],
        allowed_media_dirs: Optional[List[Path]] = None,
    ) -> Optional[Path]:
        """
        Resolve a local image reference to an existing filesystem path.

        Args:
            path_str: Raw image path from the markdown reference
            base_dir: Base directory for resolving relative paths
            allowed_media_dirs: Additional directories that belong to the current
                input resource's lifecycle (e.g. media extracted by the PDF/DOC
                parser).

        Returns:
            Resolved absolute Path if the file exists and stays within an
            allowed root, otherwise None
        """
        try:
            path = Path(path_str)

            # Reject absolute paths: they can point anywhere on the host
            if path.is_absolute():
                logger.warning(
                    f"[MarkdownParser] Rejected absolute image path: {path_str}"
                )
                return None

            # Build the list of allowed roots to confine resolution to.
            allowed_roots: list[Path] = []
            if base_dir:
                allowed_roots.append(base_dir)
            if allowed_media_dirs:
                allowed_roots.extend(allowed_media_dirs)

            if not allowed_roots:
                return None

            for root in allowed_roots:
                candidate = (root / path).resolve()

                # Verify the resolved candidate stays under the allowed root,
                # rejecting traversal attempts such as ../../private.png.
                try:
                    candidate.relative_to(root.resolve())
                except ValueError:
                    logger.warning(
                        f"[MarkdownParser] Rejected image path outside base dir: {path_str}"
                    )
                    continue

                if candidate.exists():
                    return candidate

            return None
        except Exception:
            logger.warning(f"[MarkdownParser] Cannot resolve image path: {path_str}")
            return None

    def _is_valid_image(self, image_bytes: bytes, source_path: Path) -> bool:
        """
        Validate an image's pixel dimensions and file size.

        Requirements:
        - Width > 14px and height > 14px
        - Width * height within [196, 36000000]
        - Aspect ratio (width/height) within [1/150, 150]
        - File size (local path) <= 10 MB

        Args:
            image_bytes: Raw image bytes
            source_path: Original image path (for logging)

        Returns:
            True if the image satisfies all requirements, otherwise False
        """
        # File size check (local file path limit: 10 MB)
        if len(image_bytes) > self.IMAGE_MAX_FILE_BYTES:
            logger.warning(
                f"[MarkdownParser] Image exceeds 10MB, skipping: {source_path}"
            )
            return False

        # Pixel size check
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
        except Exception as e:
            logger.warning(
                f"[MarkdownParser] Cannot read image dimensions, skipping {source_path}: {e}"
            )
            return False

        if width <= self.IMAGE_MIN_SIDE or height <= self.IMAGE_MIN_SIDE:
            logger.warning(
                f"[MarkdownParser] Image side too small ({width}x{height}), skipping: {source_path}"
            )
            return False

        pixels = width * height
        if pixels < self.IMAGE_MIN_PIXELS or pixels > self.IMAGE_MAX_PIXELS:
            logger.warning(
                f"[MarkdownParser] Image pixel count out of range ({pixels}), skipping: {source_path}"
            )
            return False

        aspect_ratio = width / height
        if aspect_ratio < self.IMAGE_MIN_ASPECT_RATIO or aspect_ratio > self.IMAGE_MAX_ASPECT_RATIO:
            logger.warning(
                f"[MarkdownParser] Image aspect ratio out of range ({aspect_ratio:.4f}), "
                f"skipping: {source_path}"
            )
            return False

        return True

    @staticmethod
    def _is_remote_uri(path: str) -> bool:
        """
        Check if a path is a remote URI.

        Args:
            path: Path to check

        Returns:
            True if path starts with http://, https://, viking://, data:, or ftp://
        """
        remote_prefixes = ("http://", "https://", "viking://", "data:", "ftp://")
        return path.startswith(remote_prefixes)

    @staticmethod
    def _deduplicate_filename(filename: str, used_names: set[str]) -> str:
        """
        Generate a unique filename by appending _1, _2, etc. when collision occurs.

        Args:
            filename: Original filename
            used_names: Set of already used filenames

        Returns:
            Unique filename
        """
        if filename not in used_names:
            return filename

        path_obj = Path(filename)
        stem = path_obj.stem
        suffix = path_obj.suffix
        counter = 1

        while True:
            new_filename = f"{stem}_{counter}{suffix}"
            if new_filename not in used_names:
                return new_filename
            counter += 1

    def _sanitize_for_path(self, text: str, max_length: int = 50) -> str:
        safe = re.sub(
            r"[^\w\u0080-\u02af\u0400-\u052f\u0600-\u077f\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u3400-\u4dbf\U00020000-\U0002a6df\s.-]",
            "",
            text,
        )
        safe = re.sub(r"\s+", "_", safe)
        safe = safe.strip("._")
        if not safe:
            return "section"
        if len(safe) > max_length:
            hash_suffix = hashlib.sha256(text.encode()).hexdigest()[:8]
            return f"{safe[: max_length - 9]}_{hash_suffix}"
        return safe

    @staticmethod
    def _doc_landing(layout: Dict[str, str]) -> Tuple[str, bool]:
        """目标 .md 入库后的落点（相对其磁盘父目录），完全由真实 layout 推断，不对
        parser 是否目录化做任何假设：

        - 所有 section 收拢在同一公共目录下 → (该目录, True)，链接指向目录；
        - 否则即单个裸文件（如未来小 .md 不再拆成目录）→ (该文件, False)，指向文件。

        落点是文件还是目录、叫什么，全部由 in-memory parse 出来的 layout 结构决定；
        parse_content 怎么改，这里自动跟随。
        """
        keys = list(layout)
        first_segs = {k.split("/", 1)[0] for k in keys}
        if len(first_segs) == 1 and all("/" in k for k in keys):
            return first_segs.pop(), True
        return keys[0], False

    async def _rewrite_single_link(
        self, link: str, src_dir: str, import_root_abs: str, source_new_dir: str
    ) -> str:
        """Rewrite one link target; return original `link` if it must not change."""
        raw = link.strip()
        if (
            not raw
            or raw.startswith("#")
            or raw.startswith("/")
            or raw.startswith("mailto:")
            or "://" in raw
        ):
            return link

        m = _MD_FRAGMENT_RE.match(raw)
        path_part = m.group(1)
        suffix = m.group(2) or ""
        if not path_part:
            return link  # pure fragment/query

        target_disk = os.path.normpath(os.path.join(src_dir, path_part))
        if not os.path.exists(target_disk):
            return link
        try:
            if os.path.commonpath([import_root_abs, target_disk]) != import_root_abs:
                return link
        except ValueError:
            return link  # different drives, etc.

        def _to_rel(new_disk: str, keep_suffix: bool) -> str:
            r = os.path.relpath(new_disk, source_new_dir).replace(os.sep, "/")
            return (r + suffix) if keep_suffix else (r if r.endswith("/") else r + "/")

        ext = os.path.splitext(target_disk)[1].lower()
        if ext not in _MD_DIR_EXTS:
            # Non-.md target (image, sibling directory, ...): keep its path; only the
            # source's added depth shifts the relative prefix.
            return _to_rel(target_disk, keep_suffix=True)

        # .md target → on ingest MarkdownParser turns it into some layout (a directory
        # of section files, or — in future variants — a single file). Resolve against
        # that REAL layout from an in-memory parse with the SAME parser: the landing
        # (file vs directory), its name and the section files all come straight from
        # the parser, so NOTHING about ingest is assumed here. Whatever parse_content
        # does, running it in-memory tells us the final result.
        layout = await self._target_split_files(target_disk)
        if not layout:
            return link  # target unparsable → leave the link untouched

        target_parent = os.path.dirname(target_disk)
        landing, landing_is_dir = self._doc_landing(layout)

        if suffix:
            # A) #anchor uniquely located in a real section → point at that section file.
            if suffix.startswith("#"):
                anchor = suffix[1:]
                hits = [
                    rel
                    for rel, text in layout.items()
                    if any(
                        _gh_slug(h) == anchor
                        for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)
                    )
                ]
                if len(hits) == 1:
                    return _to_rel(
                        os.path.join(target_parent, hits[0]), keep_suffix=True
                    )
            # B) Single-file document (a bare file, or a directory holding exactly one
            #    file) → the anchor/query lives in that one file; keep the suffix.
            if len(layout) == 1:
                return _to_rel(
                    os.path.join(target_parent, next(iter(layout))), keep_suffix=True
                )

        # C) Single bare file with no suffix to place (e.g. a future small .md kept as
        #    a file) → point at the file itself (empty suffix ⇒ no trailing slash).
        if not landing_is_dir:
            return _to_rel(os.path.join(target_parent, landing), keep_suffix=True)
        # D) Directory landing → point at the directory and drop any suffix.
        return _to_rel(os.path.join(target_parent, landing), keep_suffix=False)

    async def _rewrite_relative_links(
        self,
        content: str,
        *,
        source_path: str,
        doc_name: str,
        section_subpath: str,
        import_root: str,
    ) -> str:
        """Rewrite all relative markdown links/images in `content` (disk-coordinate)."""
        src_dir = os.path.dirname(os.path.abspath(source_path))
        import_root_abs = os.path.abspath(import_root)
        source_new_dir = os.path.join(src_dir, doc_name, section_subpath)

        out: List[str] = []
        last = 0
        for match in _MD_LINK_RE.finditer(content):
            out.append(content[last : match.start()])
            bang, text, link = match.group(1), match.group(2), match.group(3)
            new_link = await self._rewrite_single_link(
                link, src_dir, import_root_abs, source_new_dir
            )
            out.append(f"{bang}[{text}]({new_link})")
            last = match.end()
        out.append(content[last:])
        return "".join(out)

    async def _target_split_files(self, target_path: str) -> Optional[Dict[str, str]]:
        """The target's real ingest layout {"<doc_dir>/<section...>": text} via an
        in-memory parse, cached per parse_content() call. None on parse failure."""
        ctx = self._rewrite_ctx or {}
        cache = ctx.setdefault("_split_cache", {})
        key = os.path.abspath(target_path)
        if key not in cache:
            cache[key] = await self._inmemory_split_files(target_path)
        return cache[key]

    async def _inmemory_split_files(self, target_path: str) -> Optional[Dict[str, str]]:
        """Parse target into an in-memory FS; return {"<doc_dir>/<section...>": text}
        keyed by path relative to the temp root, i.e. INCLUDING the doc-root dir
        segment so callers can map each key straight onto the target's parent dir."""
        try:
            fs = _InMemoryFS()
            sub_parser = MarkdownParser(self.extract_frontmatter, self.config)
            # _get_viking_fs() ignores self._viking_fs and returns the global
            # singleton, so override it on the instance to use our in-memory FS.
            sub_parser._get_viking_fs = lambda: fs  # type: ignore[method-assign]
            result = await sub_parser.parse(target_path)
            root = (result.temp_dir_path or "").rstrip("/")
            out: Dict[str, str] = {}
            for uri, content in fs.files.items():
                if not isinstance(content, str):
                    continue
                # rel = "<doc_dir>/<section...>" kept whole (temp_dir_path is the temp
                # root, WITHOUT the doc-root dir), so it maps directly onto the
                # target's parent directory on disk.
                rel = uri[len(root) :].lstrip("/") if uri.startswith(root) else uri
                out[rel] = content
            return out
        except Exception as exc:
            logger.debug(f"[_inmemory_split_files] failed for {target_path}: {exc}")
            return None

    def _section_subpath(self, uri: str, root_dir: str) -> str:
        """Return the section file's directory path relative to root_dir (POSIX)."""
        section_dir = uri.rsplit("/", 1)[0]
        root = root_dir.rstrip("/")
        if section_dir == root:
            return ""
        if section_dir.startswith(root + "/"):
            return section_dir[len(root) + 1 :]
        return ""

    async def _write_section(self, uri: str, content: str) -> None:
        """Write a markdown section file, rewriting relative links when enabled."""
        ctx = self._rewrite_ctx
        if ctx and ctx.get("enabled") and ctx.get("source_path") and ctx.get("import_root"):
            content = await self._rewrite_relative_links(
                content,
                source_path=ctx["source_path"],
                doc_name=ctx["doc_name"],
                section_subpath=self._section_subpath(uri, ctx["root_dir"]),
                import_root=ctx["import_root"],
            )
        await self._get_viking_fs().write_file(uri, content)

    # ========== New Parsing Logic (v5.0) ==========

    async def _parse_and_create_structure(
        self,
        content: str,
        headings: List[Tuple[int, int, str, int]],
        root_dir: str,
        source_path: Optional[str] = None,
        doc_name: Optional[str] = None,
    ) -> None:
        """
        Parse markdown and create directory structure directly in VikingFS.

        Logic:
        - Small files (< MAX_SECTION_SIZE): single file with original name
        - Large files: split by sections with merge logic for small sections
        - Sections with subsections: become directories
        - Direct content: treated as virtual section, participates in merge
        - Oversized sections without subsections: split by paragraphs

        Args:
            content: Markdown content
            headings: List of (start, end, title, level) tuples
            root_dir: Root directory URI
            source_path: Source file path for naming
        """
        viking_fs = self._get_viking_fs()
        max_size = self.config.max_section_size or self.DEFAULT_MAX_SECTION_SIZE
        max_chars = self.config.max_section_chars
        min_size = self.DEFAULT_MIN_SECTION_TOKENS

        # Estimate document size
        estimated_tokens = self._estimate_token_count(content)
        logger.info(f"[MarkdownParser] Document size: {estimated_tokens} tokens")

        # Create root directory
        await viking_fs.mkdir(root_dir)

        # Get document name
        doc_name = doc_name or self._sanitize_for_path(
            _smart_stem(source_path) if source_path else "content"
        )

        # Small document: save as single file (check both token and char limits)
        if estimated_tokens <= max_size and len(content) <= max_chars:
            file_path = f"{root_dir}/{doc_name}.md"
            await self._write_section(file_path, content)
            logger.debug(f"[MarkdownParser] Small document saved as: {file_path}")
            return

        # No headings: split by paragraphs
        if not headings:
            logger.info("[MarkdownParser] No headings, splitting by paragraphs")
            parts = self._smart_split_content(content, max_size)
            for part_idx, part in enumerate(parts, 1):
                await self._write_section(f"{root_dir}/{doc_name}_{part_idx}.md", part)
            logger.debug(f"[MarkdownParser] Split into {len(parts)} parts")
            return

        # Build virtual section list (pre-heading content as first virtual section)
        sections = []
        first_heading_start = headings[0][0]
        if first_heading_start > 0:
            pre_content = content[:first_heading_start].strip()
            if pre_content:
                pre_tokens = self._estimate_token_count(pre_content)
                sections.append(
                    {
                        "name": doc_name,
                        "content": pre_content,
                        "tokens": pre_tokens,
                        "has_children": False,
                        "heading_idx": None,
                    }
                )

        # Add real sections (top-level only for this pass)
        min_level = min(h[3] for h in headings)
        i = 0
        while i < len(headings):
            if headings[i][3] == min_level:
                sections.append(
                    {
                        "heading_idx": i,
                    }
                )
            i += 1

        # Process sections with merge logic
        await self._process_sections_with_merge(
            content, headings, root_dir, sections, doc_name, max_size, min_size
        )

    async def _process_sections_with_merge(
        self,
        content: str,
        headings: List[Tuple[int, int, str, int]],
        parent_dir: str,
        sections: List[Dict[str, Any]],
        parent_name: str,
        max_size: int,
        min_size: int,
    ) -> None:
        """Process sections with small section merge logic."""
        viking_fs = self._get_viking_fs()

        # Expand section info
        expanded = [
            section
            if section.get("heading_idx") is None
            else self._get_section_info(content, headings, section["heading_idx"])
            for section in sections
        ]

        pending = []
        buffered_section = None

        async def flush_buffered() -> None:
            nonlocal buffered_section
            if buffered_section is not None:
                await self._save_section(
                    content,
                    headings,
                    parent_dir,
                    buffered_section,
                    max_size,
                    min_size,
                )
                buffered_section = None

        for sec in expanded:
            name, tokens, content_text = sec["name"], sec["tokens"], sec["content"]
            has_children = sec["has_children"]

            # Handle small sections
            if tokens < min_size:
                if pending and sum(t for _, _, t in pending) + tokens > max_size:
                    await flush_buffered()
                    await self._save_merged(viking_fs, parent_dir, pending)
                    pending = []
                pending.append((name, content_text, tokens))
                continue

            if pending:
                await flush_buffered()

                # Try merge with pending
                if self._can_merge(pending, tokens, max_size, has_children):
                    pending.append((name, content_text, tokens))
                    await self._save_merged(viking_fs, parent_dir, pending)
                    pending = []
                    continue

                # Avoid flushing a single tiny section as a standalone low-value file.
                if self._should_merge_pending_into_next(pending):
                    sec = self._merge_pending_into_next_section(pending, sec)
                    pending = []
                else:
                    await self._save_merged(viking_fs, parent_dir, pending)
                    pending = []
            else:
                await flush_buffered()

            buffered_section = sec

        if pending:
            # No next section exists. Fold a single tiny pending section back into
            # the previous saved candidate instead of emitting a standalone file.
            if buffered_section is not None and self._should_merge_pending_into_next(pending):
                buffered_section = self._merge_pending_into_previous_section(
                    buffered_section, pending
                )
                pending = []
            else:
                await flush_buffered()
                await self._save_merged(viking_fs, parent_dir, pending)
                pending = []

        await flush_buffered()

    def _can_merge(self, pending: List, tokens: int, max_size: int, has_children: bool) -> bool:
        """Check if section can merge with pending."""
        return sum(t for _, _, t in pending) + tokens <= max_size and not has_children

    def _should_merge_pending_into_next(self, pending: List[Tuple[str, str, int]]) -> bool:
        """Prefer folding a single tiny pending section into the next section."""
        return len(pending) == 1 and pending[0][2] <= self.DEFAULT_MIN_SECTION_TOKENS

    def _merge_pending_into_next_section(
        self, pending: List[Tuple[str, str, int]], section: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Attach a tiny pending section to the following section."""
        _, pending_content, _ = pending[0]
        merged = dict(section)
        merged["content"] = f"{pending_content}\n\n{section['content']}".strip()
        merged["tokens"] = self._estimate_token_count(merged["content"])

        if merged.get("has_children"):
            direct_content = section.get("direct_content", "").strip()
            merged["direct_content"] = (
                f"{pending_content}\n\n{direct_content}".strip()
                if direct_content
                else pending_content
            )

        return merged

    def _merge_pending_into_previous_section(
        self, section: Dict[str, Any], pending: List[Tuple[str, str, int]]
    ) -> Dict[str, Any]:
        """Attach a tiny trailing pending section back into the previous section."""
        _, pending_content, _ = pending[0]
        merged = dict(section)
        merged["content"] = f"{section['content']}\n\n{pending_content}".strip()
        merged["tokens"] = self._estimate_token_count(merged["content"])

        if merged.get("has_children"):
            direct_content = section.get("direct_content", "").strip()
            merged["direct_content"] = (
                f"{direct_content}\n\n{pending_content}".strip()
                if direct_content
                else pending_content
            )

        return merged

    async def _try_add_to_pending(
        self, viking_fs, parent_dir: str, pending: List, item: Tuple, max_size: int
    ) -> List:
        """Try add item to pending, flush if would exceed max_size."""
        name, content, tokens = item
        if pending and sum(t for _, _, t in pending) + tokens > max_size:
            await self._save_merged(viking_fs, parent_dir, pending)
            pending = []
        pending.append(item)
        return pending

    async def _flush_pending(self, viking_fs, parent_dir: str, pending: List) -> List:
        """Flush pending sections and return empty list."""
        if pending:
            await self._save_merged(viking_fs, parent_dir, pending)
        return []

    async def _save_section(
        self,
        content: str,
        headings: List[Tuple[int, int, str, int]],
        parent_dir: str,
        section: Dict[str, Any],
        max_size: int,
        min_size: int,
    ) -> None:
        """Save a single section (file or directory)."""
        viking_fs = self._get_viking_fs()
        name, tokens, content_text = section["name"], section["tokens"], section["content"]
        has_children = section["has_children"]

        # Fits in one file (check both token and char limits)
        if tokens <= max_size and len(content_text) <= self.config.max_section_chars:
            await self._write_section(f"{parent_dir}/{name}.md", content_text)
            logger.debug(f"[MarkdownParser] Saved: {name}.md")
            return

        # Create directory and handle children or split
        section_dir = f"{parent_dir}/{name}"
        await viking_fs.mkdir(section_dir, exist_ok=True)

        if has_children:
            await self._process_children(
                content, headings, section_dir, section, name, max_size, min_size
            )
        else:
            await self._split_content(viking_fs, section_dir, name, content_text, max_size)

    async def _process_children(
        self,
        content: str,
        headings: List[Tuple[int, int, str, int]],
        section_dir: str,
        section: Dict[str, Any],
        name: str,
        max_size: int,
        min_size: int,
    ) -> None:
        """Build and process child sections."""
        children = []
        if section.get("direct_content"):
            children.append(
                {
                    "name": name,
                    "content": section["direct_content"],
                    "tokens": self._estimate_token_count(section["direct_content"]),
                    "has_children": False,
                    "heading_idx": None,
                }
            )
        for child_idx in section.get("child_indices", []):
            children.append({"heading_idx": child_idx})

        await self._process_sections_with_merge(
            content, headings, section_dir, children, name, max_size, min_size
        )

    async def _split_content(
        self, viking_fs, section_dir: str, name: str, content: str, max_size: int
    ) -> None:
        """Split content by paragraphs."""
        logger.info(f"[MarkdownParser] Splitting: {name}")
        parts = self._smart_split_content(content, max_size)
        for i, part in enumerate(parts, 1):
            await self._write_section(f"{section_dir}/{name}_{i}.md", part)

    def _generate_merged_filename(self, sections: List[Tuple[str, str, int]]) -> str:
        """
        Smart merged filename generation, limited to MAX_MERGED_FILENAME_LENGTH characters.

        Strategy:
        - Single section: Use directly (truncated with hash if needed)
        - Multiple sections: {first_section}_{count}more (e.g., Intro_3more)
        - Total length strictly limited: MAX_MERGED_FILENAME_LENGTH characters
        - Hash suffix ensures uniqueness when truncation occurs
        """
        if not sections:
            return "merged"

        names = [n for n, _, _ in sections]
        count = len(names)
        max_len = self.MAX_MERGED_FILENAME_LENGTH

        # Build a content-aware hash from ALL section names AND indices to guarantee
        # uniqueness even when different merge groups share the same heading names.
        full_key = "_".join(f"{n}:{i}" for n, _, i in sections)
        hash_suffix = hashlib.sha256(full_key.encode()).hexdigest()[:8]

        if count == 1:
            base = names[0]
        else:
            suffix = f"_{count}more"
            max_first_len = max_len - len(suffix) - 9  # reserve space for _hash
            first_name = names[0][: max(max_first_len, 1)]
            base = f"{first_name}{suffix}"

        name = f"{base}_{hash_suffix}"

        if len(name) > max_len:
            name = f"{name[: max_len - 9]}_{hash_suffix}"

        name = name.strip("_")
        return name or "merged"

    async def _save_merged(
        self, viking_fs, parent_dir: str, sections: List[Tuple[str, str, int]]
    ) -> None:
        """Save merged sections as single file with smart naming.

        If the joined content exceeds max_section_chars it is split further
        by _smart_split_content before writing, so no single file ever exceeds
        the hard character limit.
        """
        name = self._generate_merged_filename(sections)
        content = "\n\n".join(c for _, c, _ in sections)
        max_chars = self.config.max_section_chars
        if len(content) > max_chars:
            max_size = self.config.max_section_size or self.DEFAULT_MAX_SECTION_SIZE
            parts = self._smart_split_content(content, max_size)
            for i, part in enumerate(parts, 1):
                await self._write_section(f"{parent_dir}/{name}_{i}.md", part)
            logger.debug(
                f"[MarkdownParser] Merged then split: {name} ({len(sections)} sections → {len(parts)} parts)"
            )
        else:
            await self._write_section(f"{parent_dir}/{name}.md", content)
            logger.debug(f"[MarkdownParser] Merged: {name}.md ({len(sections)} sections)")

    def _get_section_info(
        self,
        content: str,
        headings: List[Tuple[int, int, str, int]],
        idx: int,
    ) -> Dict[str, Any]:
        """
        Get section info including content, tokens, children info.

        Args:
            content: Full markdown content
            headings: All headings list
            idx: Index of heading in list

        Returns:
            Dict with section info
        """
        start_pos, end_pos, title, level = headings[idx]
        section_name = self._sanitize_for_path(title)

        # Find section end (next same or higher level heading)
        section_end = len(content)
        next_same_level_idx = len(headings)
        for j in range(idx + 1, len(headings)):
            if headings[j][3] <= level:
                section_end = headings[j][0]
                next_same_level_idx = j
                break

        # Find direct content end (first child heading)
        direct_content_end = section_end
        first_child_idx = None
        child_indices = []
        for j in range(idx + 1, next_same_level_idx):
            if headings[j][3] == level + 1:
                if first_child_idx is None:
                    first_child_idx = j
                    direct_content_end = headings[j][0]
                child_indices.append(j)

        has_children = first_child_idx is not None

        # Build content
        heading_prefix = "#" * level
        section_start = end_pos  # After heading line
        full_content = f"{heading_prefix} {title}\n\n{content[section_start:section_end].strip()}"
        full_tokens = self._estimate_token_count(full_content)

        direct_content = ""
        if has_children:
            direct_text = content[section_start:direct_content_end].strip()
            if direct_text:
                direct_content = f"{heading_prefix} {title}\n\n{direct_text}"

        return {
            "name": section_name,
            "content": full_content,
            "tokens": full_tokens,
            "has_children": has_children,
            "heading_idx": idx,
            "direct_content": direct_content,
            "child_indices": child_indices,
        }

    def _estimate_token_count(self, content: str) -> int:
        # CJK characters (Chinese, Japanese, Korean): ~0.7 token per char
        # Other characters (including Latin, Arabic, Cyrillic, etc.): ~0.3 token per char
        # This provides better coverage for multilingual documents
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", content))
        other_chars = len(re.findall(r"[^\s]", content)) - cjk_chars
        return int(cjk_chars * 0.7 + other_chars * 0.3)
