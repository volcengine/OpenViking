# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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

import hashlib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from openviking.parse.base import NodeType, ParseResult, ResourceNode, create_parse_result
from openviking.parse.parsers.base_parser import BaseParser
from openviking_cli.utils.config.parser_config import ParserConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

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
    DEFAULT_MAX_SECTION_SIZE = 1024  # Maximum tokens per section
    DEFAULT_MIN_SECTION_TOKENS = 512  # Minimum tokens to create a separate section
    MAX_MERGED_FILENAME_LENGTH = 32  # Maximum length for merged section filenames

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
            **kwargs: Additional runtime options

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

            # Get document title
            doc_title = meta.get("frontmatter", {}).get(
                "title", Path(source_path).stem if source_path else "Document"
            )

            # Create root directory
            root_dir = f"{temp_uri}/{self._sanitize_for_path(doc_title)}"

            # Find all headings
            headings = self._find_headings(content)
            logger.info(f"[MarkdownParser] Found {len(headings)} headings")

            # Parse and create directory structure
            await self._parse_and_create_structure(content, headings, root_dir, source_path)

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

        Args:
            content: Content to split
            max_size: Maximum size per part (in tokens)

        Returns:
            List of content parts
        """
        paragraphs = content.split("\n\n")
        parts = []
        current = ""
        current_tokens = 0

        for para in paragraphs:
            para_tokens = self._estimate_token_count(para)

            # Single paragraph too long, force split by characters
            if para_tokens > max_size:
                if current:
                    parts.append(current.strip())
                    current = ""
                    current_tokens = 0
                # Split by character count (rough approximation: 1 token ~ 3 chars)
                char_split_size = int(max_size * 3)
                for i in range(0, len(para), char_split_size):
                    parts.append(para[i : i + char_split_size].strip())
            elif current_tokens + para_tokens > max_size and current:
                parts.append(current.strip())
                current = para
                current_tokens = para_tokens
            else:
                current = current + "\n\n" + para if current else para
                current_tokens += para_tokens

        if current.strip():
            parts.append(current.strip())

        return parts if parts else [content]

    def _sanitize_for_path(self, text: str, max_length: int = 50) -> str:
        safe = re.sub(
            r"[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u3400-\u4dbf\U00020000-\U0002a6df\s-]",
            "",
            text,
        )
        safe = re.sub(r"\s+", "_", safe)
        safe = safe.strip("_")
        if not safe:
            return "section"
        if len(safe) > max_length:
            hash_suffix = hashlib.sha256(text.encode()).hexdigest()[:8]
            return f"{safe[: max_length - 9]}_{hash_suffix}"
        return safe

    # ========== New Parsing Logic (v5.0) ==========

    async def _parse_and_create_structure(
        self,
        content: str,
        headings: List[Tuple[int, int, str, int]],
        root_dir: str,
        source_path: Optional[str] = None,
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
        min_size = self.DEFAULT_MIN_SECTION_TOKENS

        # Estimate document size
        estimated_tokens = self._estimate_token_count(content)
        logger.info(f"[MarkdownParser] Document size: {estimated_tokens} tokens")

        # Create root directory
        await viking_fs.mkdir(root_dir)

        # Get document name
        doc_name = self._sanitize_for_path(Path(source_path).stem if source_path else "content")

        # Small document: save as single file
        if estimated_tokens <= max_size:
            file_path = f"{root_dir}/{doc_name}.md"
            await viking_fs.write_file(file_path, content)
            logger.debug(f"[MarkdownParser] Small document saved as: {file_path}")
            return

        # No headings: split by paragraphs
        if not headings:
            logger.info("[MarkdownParser] No headings, splitting by paragraphs")
            parts = self._smart_split_content(content, max_size)
            for part_idx, part in enumerate(parts, 1):
                part_file = f"{root_dir}/{doc_name}_{part_idx}.md"
                await viking_fs.write_file(part_file, part)
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
        for sec in expanded:
            name, tokens, content_text = sec["name"], sec["tokens"], sec["content"]
            has_children = sec["has_children"]

            # Handle small sections
            if tokens < min_size:
                pending = await self._try_add_to_pending(
                    viking_fs, parent_dir, pending, (name, content_text, tokens), max_size
                )
                continue

            # Try merge with pending
            if pending and self._can_merge(pending, tokens, max_size, has_children):
                pending.append((name, content_text, tokens))
                await self._save_merged(viking_fs, parent_dir, pending)
                pending = []
                continue

            # Save pending and process current section
            pending = await self._flush_pending(viking_fs, parent_dir, pending)
            await self._save_section(content, headings, parent_dir, sec, max_size, min_size)

        # Save remaining pending
        await self._flush_pending(viking_fs, parent_dir, pending)

    def _can_merge(self, pending: List, tokens: int, max_size: int, has_children: bool) -> bool:
        """Check if section can merge with pending."""
        return sum(t for _, _, t in pending) + tokens <= max_size and not has_children

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

        # Fits in one file
        if tokens <= max_size:
            await viking_fs.write_file(f"{parent_dir}/{name}.md", content_text)
            logger.debug(f"[MarkdownParser] Saved: {name}.md")
            return

        # Create directory and handle children or split
        section_dir = f"{parent_dir}/{name}"
        await viking_fs.mkdir(section_dir)

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
            await viking_fs.write_file(f"{section_dir}/{name}_{i}.md", part)

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

        if count == 1:
            name = names[0]
        else:
            suffix = f"_{count}more"
            max_first_len = max_len - len(suffix)
            first_name = names[0][: max(max_first_len, 1)]
            name = f"{first_name}{suffix}"

        if len(name) > max_len:
            full_key = "_".join(names)
            hash_suffix = hashlib.sha256(full_key.encode()).hexdigest()[:8]
            name = f"{name[: max_len - 9]}_{hash_suffix}"

        name = name.strip("_")
        return name or "merged"

    async def _save_merged(
        self, viking_fs, parent_dir: str, sections: List[Tuple[str, str, int]]
    ) -> None:
        """Save merged sections as single file with smart naming."""
        name = self._generate_merged_filename(sections)
        content = "\n\n".join(c for _, c, _ in sections)
        await viking_fs.write_file(f"{parent_dir}/{name}.md", content)
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
