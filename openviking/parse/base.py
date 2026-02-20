# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Base parser interface for OpenViking document processing.

Following PageIndex philosophy: preserve natural document structure
rather than arbitrary chunking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    pass

# ============================================================================
# Common utility functions
# ============================================================================


def calculate_media_strategy(image_count: int, line_count: int) -> str:
    """
    Unified media processing strategy calculation.

    Args:
        image_count: Number of images
        line_count: Number of text lines

    Returns:
        Strategy string: "full_page_vlm" | "extract" | "text_only"
    """
    if line_count > 0 and (image_count / line_count > 0.3 or image_count >= 5):
        return "full_page_vlm"
    elif image_count > 0:
        return "extract"
    else:
        return "text_only"


def format_table_to_markdown(rows: List[List[str]], has_header: bool = True) -> str:
    """
    Format table data as Markdown table.

    Args:
        rows: Table row data, each row is a list of strings
        has_header: Whether first row is header

    Returns:
        Markdown formatted table string
    """
    if not rows:
        return ""

    # Calculate maximum width for each column
    col_count = max(len(row) for row in rows)
    col_widths = [0] * col_count
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    lines = []
    for row_idx, row in enumerate(rows):
        # Pad missing columns
        padded_row = list(row) + [""] * (col_count - len(row))
        cells = [str(cell).ljust(col_widths[i]) for i, cell in enumerate(padded_row)]
        lines.append("| " + " | ".join(cells) + " |")

        # Add separator row after header
        if row_idx == 0 and has_header and len(rows) > 1:
            separator = ["-" * w for w in col_widths]
            lines.append("| " + " | ".join(separator) + " |")

    return "\n".join(lines)


def lazy_import(module_name: str, package_name: Optional[str] = None) -> Any:
    """
    Unified lazy import utility.

    Args:
        module_name: Module name
        package_name: pip package name (if different from module name)

    Returns:
        Imported module

    Raises:
        ImportError: If module is not available
    """
    import importlib

    try:
        return importlib.import_module(module_name)
    except ImportError:
        pkg = package_name or module_name
        raise ImportError(
            f"Module '{module_name}' not available. Please install: pip install {pkg}"
        )


class ResourceCategory(Enum):
    """
    Resource category classification.

    Used to categorize different types of resources at a high level.
    """

    DOCUMENT = "document"  # Text-based document types (currently supported)
    MEDIA = "media"  # Media types (future support)


class DocumentType(Enum):
    """
    Document format types.

    Specific document formats supported under the DOCUMENT category.
    """

    PDF = "pdf"
    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"
    HTML = "html"


class MediaType(Enum):
    """
    Media format types - Future expansion.

    Specific media formats to be supported under the MEDIA category.
    Currently these are placeholder types for future implementation.
    """

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class NodeType(Enum):
    """Document node types.

    Simplified structure (v2.0) - only ROOT and SECTION are used.
    All content (paragraphs, code blocks, tables, lists, etc.) remains
    in the content string as Markdown format.

    Design Principles:
    - Structural simplification: Only ROOT and SECTION types
    - Content preservation: All detailed content in Markdown format
    - Clear hierarchy: SECTION represents document chapter structure
    - Maximum flexibility: Avoid fine-grained node decomposition
    """

    ROOT = "root"
    SECTION = "section"


@dataclass
class ResourceNode:
    """
    A node in the document tree structure.

    Three-phase architecture:
    - Phase 1: detail_file stores flat UUID.md filename
    - Phase 2: meta stores semantic_title, abstract, overview
    - Phase 3: content_path points to content.md in final directory

    Multimodal extensions:
    - content_type: Resource content type (text/image/video/audio)
    - auxiliary_files: Auxiliary file mapping {filename: uuid.ext}
    """

    type: NodeType
    detail_file: Optional[str] = None  # Phase 1: UUID.md filename (e.g., "a1b2c3d4.md")
    content_path: Optional[Path] = None  # Phase 3: Final content file path
    title: Optional[str] = None  # Original title (from heading), empty means split plain text
    level: int = 0  # Hierarchy level (0 = root, 1 = top section, etc.)
    children: List["ResourceNode"] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    # Multimodal extension fields
    content_type: str = "text"  # text/image/video/audio
    auxiliary_files: Dict[str, str] = field(default_factory=dict)  # {filename: uuid.ext}

    def add_child(self, child: "ResourceNode") -> None:
        """Add a child node."""
        self.children.append(child)

    # Text file extensions
    TEXT_EXTENSIONS = {".md", ".txt", ".text", ".markdown", ".json", ".yaml", ".yml"}

    def get_detail_content(self, temp_dir: Path) -> str:
        """Read detail file content from local temp directory (compatibility mode)."""
        if not self.detail_file:
            return ""
        file_path = temp_dir / self.detail_file
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return ""

    async def get_detail_content_async(self, temp_uri: str) -> str:
        """Read detail file content from VikingFS temp directory."""
        from openviking.storage.viking_fs import get_viking_fs

        if not self.detail_file:
            return ""
        file_uri = f"{temp_uri}/{self.detail_file}"
        try:
            return await get_viking_fs().read_file(file_uri)
        except Exception:
            return ""

    def get_content(self) -> str:
        """Read final content file (used after Phase 3)."""
        if not self.content_path or not self.content_path.exists():
            return ""
        if self.content_path.suffix.lower() not in self.TEXT_EXTENSIONS:
            return ""  # Binary files don't return text
        return self.content_path.read_text(encoding="utf-8")

    def get_content_bytes(self) -> bytes:
        """Read binary content (for images/audio/video)."""
        if self.content_path and self.content_path.exists():
            return self.content_path.read_bytes()
        return b""

    def is_binary(self) -> bool:
        """Check if content is binary (images/audio/video)."""
        if not self.content_path:
            return False
        return self.content_path.suffix.lower() not in self.TEXT_EXTENSIONS

    def get_content_size(self) -> int:
        """Get content file size in bytes."""
        if self.content_path and self.content_path.exists():
            return self.content_path.stat().st_size
        return 0

    def get_text(self, include_children: bool = True) -> str:
        """
        Get text content of this node.

        Args:
            include_children: Include text from child nodes

        Returns:
            Combined text content
        """
        content = self.get_content()
        texts = [content] if content else []
        if include_children:
            for child in self.children:
                texts.append(child.get_text(include_children=True))
        return "\n".join(texts)

    def get_abstract(self, max_length: int = 256) -> str:
        """
        Generate L0 abstract for this node.

        Args:
            max_length: Maximum character length

        Returns:
            Abstract text
        """
        if "abstract" in self.meta:
            return self.meta["abstract"]
        if self.title:
            abstract = self.title
        else:
            content = self.get_content()
            abstract = content[:max_length] if content else ""

        if len(abstract) > max_length:
            abstract = abstract[: max_length - 3] + "..."

        return abstract

    def get_overview(self, max_length: int = 4000) -> str:
        """
        Generate L1 overview for this node.

        Args:
            max_length: Maximum character length

        Returns:
            Overview text including structure summary
        """
        if "overview" in self.meta:
            return self.meta["overview"]
        # Default overview generation
        parts = []
        if self.title:
            parts.append(f"**{self.title}**")

        # Add content preview
        content = self.get_content()
        if content:
            content_preview = content[:1000]
            if len(content) > 1000:
                content_preview += "..."
            parts.append(content_preview)

        # Add children summary
        if self.children:
            parts.append(f"\n[Contains {len(self.children)} sub-sections]")
            for child in self.children[:5]:  # First 5 children
                child_abstract = child.get_abstract(max_length=100)
                parts.append(f"  - {child_abstract}")
            if len(self.children) > 5:
                parts.append(f"  ... and {len(self.children) - 5} more")

        overview = "\n".join(parts)
        if len(overview) > max_length:
            overview = overview[: max_length - 3] + "..."

        return overview

    def to_dict(self) -> Dict[str, Any]:
        """Convert node to dictionary."""
        return {
            "type": self.type.value,
            "title": self.title,
            "content_path": str(self.content_path) if self.content_path else None,
            "level": self.level,
            "meta": self.meta,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResourceNode":
        """Create node from dictionary."""
        content_path = data.get("content_path")
        node = cls(
            type=NodeType(data["type"]),
            content_path=Path(content_path) if content_path else None,
            title=data.get("title"),
            level=data.get("level", 0),
            meta=data.get("meta", {}),
        )
        for child_data in data.get("children", []):
            node.add_child(cls.from_dict(child_data))
        return node


@dataclass
class ParseResult:
    """Result of parsing a document."""

    root: ResourceNode
    source_path: Optional[str] = None

    # Temporary directory path (for v4.0 architecture)
    temp_dir_path: Optional[str] = None  # e.g., "/tmp/openviking_parse_a1b2c3d4"

    # Core metadata fields
    source_format: Optional[str] = None  # File format (e.g., "pdf", "markdown")
    parser_name: Optional[str] = None  # Parser name (e.g., "PDFParser")
    parser_version: Optional[str] = None  # Parser version
    parse_time: Optional[float] = None  # Parse duration in seconds
    parse_timestamp: Optional[datetime] = None  # Parse timestamp

    meta: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Check if parsing was successful."""
        return len(self.warnings) == 0

    def get_all_nodes(self) -> List[ResourceNode]:
        """Get all nodes in the tree (flattened)."""
        nodes = []

        def collect(node: ResourceNode):
            nodes.append(node)
            for child in node.children:
                collect(child)

        collect(self.root)
        return nodes

    def get_sections(self, min_level: int = 0, max_level: int = 10) -> List[ResourceNode]:
        """
        Get section nodes within level range.

        Args:
            min_level: Minimum hierarchy level
            max_level: Maximum hierarchy level

        Returns:
            List of section nodes
        """
        sections = []
        for node in self.get_all_nodes():
            if node.type == NodeType.SECTION and min_level <= node.level <= max_level:
                sections.append(node)
        return sections


def create_parse_result(
    root: ResourceNode,
    source_path: Optional[str] = None,
    source_format: Optional[str] = None,
    parser_name: Optional[str] = None,
    parser_version: str = "2.0",
    parse_time: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> ParseResult:
    """
    Helper function to create ParseResult with all new fields populated.

    Args:
        root: Document tree root node
        source_path: Source file path
        source_format: File format (e.g., "pdf", "markdown")
        parser_name: Parser name (e.g., "PDFParser")
        parser_version: Parser version (default: "2.0")
        parse_time: Parse duration in seconds
        meta: Metadata dict
        warnings: Warning messages

    Returns:
        ParseResult with all fields populated
    """
    return ParseResult(
        root=root,
        source_path=source_path,
        source_format=source_format,
        parser_name=parser_name,
        parser_version=parser_version,
        parse_time=parse_time,
        parse_timestamp=datetime.now() if parse_time is not None else None,
        meta=meta or {},
        warnings=warnings or [],
    )
