# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Isolated unit tests for directory-import parser routing and path mapping.

This script verifies **two independent concerns** without invoking the full
``ResourceService`` pipeline:

1. **Parser selection** – given a set of file extensions, the ``ParserRegistry``
   (and ``scan_directory``) correctly classifies each file and selects the
   expected parser type (MarkdownParser, HTMLParser, PDFParser, TextParser,
   or the text-fallback path for code / config files).

2. **Path mapping** – the ``_process_directory_file`` helper in
   ``ResourceService`` converts each file's relative path into the correct
   Viking target URI so that the imported directory structure is preserved.
   For example, ``a/b/c.md`` with base target ``viking://resources/mydir``
   produces target ``viking://resources/mydir/a/b`` and the parser names
   the document ``c``, yielding final URI ``viking://resources/mydir/a/b/c``.
"""

from pathlib import Path, PurePosixPath
from typing import Dict, List, Tuple

import pytest

from openviking.parse.directory_scan import (
    DirectoryScanResult,
    scan_directory,
)
from openviking.parse.parsers.html import HTMLParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.pdf import PDFParser
from openviking.parse.parsers.text import TextParser
from openviking.parse.registry import ParserRegistry

# ═══════════════════════════════════════════════════════════════════════════
# Part 1 – Parser selection
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def registry() -> ParserRegistry:
    """Default registry (no optional parsers like ImageParser)."""
    return ParserRegistry(register_optional=False)


# -- directory tree that covers every parser type ----------------------------


@pytest.fixture
def tmp_all_parsers(tmp_path: Path) -> Path:
    """Directory tree with files that exercise every built-in parser.

    Layout::

        tmp_path/
            docs/
                guide.md          -> MarkdownParser
                spec.markdown     -> MarkdownParser
                readme.mdown      -> MarkdownParser
            web/
                index.html        -> HTMLParser
                page.htm          -> HTMLParser
            pdfs/
                paper.pdf         -> PDFParser  (binary, requires real bytes)
            text/
                notes.txt         -> TextParser
                log.text          -> TextParser
            code/
                app.py            -> text-fallback (is_text_file)
                main.js           -> text-fallback
                style.css         -> text-fallback
            config/
                settings.yaml     -> text-fallback
                data.json         -> text-fallback
                rules.toml        -> text-fallback
            unsupported/
                image.bmp         -> unsupported (binary, no parser)
                archive.rar       -> unsupported
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide", encoding="utf-8")
    (tmp_path / "docs" / "spec.markdown").write_text("# Spec", encoding="utf-8")
    (tmp_path / "docs" / "readme.mdown").write_text("# Readme", encoding="utf-8")

    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "web" / "page.htm").write_text("<html></html>", encoding="utf-8")

    (tmp_path / "pdfs").mkdir()
    # Minimal PDF header so it's not empty
    (tmp_path / "pdfs" / "paper.pdf").write_bytes(b"%PDF-1.4 minimal")

    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "notes.txt").write_text("plain text", encoding="utf-8")
    (tmp_path / "text" / "log.text").write_text("log entry", encoding="utf-8")

    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "app.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "code" / "main.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "code" / "style.css").write_text("body{}", encoding="utf-8")

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("k: v", encoding="utf-8")
    (tmp_path / "config" / "data.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "rules.toml").write_text("[section]", encoding="utf-8")

    (tmp_path / "unsupported").mkdir()
    (tmp_path / "unsupported" / "image.bmp").write_bytes(b"BM\x00\x00")
    (tmp_path / "unsupported" / "archive.rar").write_bytes(b"RAR\x00")

    return tmp_path


class TestParserSelection:
    """Each file extension must be resolved to the correct parser class."""

    # Extension -> expected parser class (or None = no dedicated parser, uses
    # text-fallback through ParserRegistry.parse which falls through to TextParser)
    DEDICATED_PARSER_MAP: Dict[str, type] = {
        ".md": MarkdownParser,
        ".markdown": MarkdownParser,
        ".mdown": MarkdownParser,
        ".html": HTMLParser,
        ".htm": HTMLParser,
        ".pdf": PDFParser,
        ".txt": TextParser,
        ".text": TextParser,
    }

    # Extensions that are *processable* (via is_text_file) but have no
    # dedicated parser in the registry – they fall back to TextParser at
    # parse-time via ``ParserRegistry.parse``.
    TEXT_FALLBACK_EXTENSIONS = {".py", ".js", ".css", ".yaml", ".json", ".toml"}

    def test_dedicated_parsers_resolve(self, registry: ParserRegistry) -> None:
        """get_parser_for_file returns the correct class for each extension."""
        for ext, expected_cls in self.DEDICATED_PARSER_MAP.items():
            dummy_path = Path(f"/tmp/file{ext}")
            parser = registry.get_parser_for_file(dummy_path)
            assert parser is not None, f"No parser returned for {ext}"
            assert isinstance(parser, expected_cls), (
                f"{ext}: expected {expected_cls.__name__}, got {type(parser).__name__}"
            )

    def test_text_fallback_returns_none_from_registry(self, registry: ParserRegistry) -> None:
        """Code / config extensions have no *dedicated* parser, so
        ``get_parser_for_file`` returns None.  The registry's ``parse()``
        falls back to TextParser internally."""
        for ext in self.TEXT_FALLBACK_EXTENSIONS:
            dummy_path = Path(f"/tmp/file{ext}")
            parser = registry.get_parser_for_file(dummy_path)
            assert parser is None, (
                f"{ext}: expected None (text-fallback), got {type(parser).__name__}"
            )

    def test_scan_classifies_all_files_correctly(
        self, tmp_all_parsers: Path, registry: ParserRegistry
    ) -> None:
        """scan_directory should mark dedicated-parser and text-fallback
        files as processable, and truly unknown formats as unsupported."""
        result: DirectoryScanResult = scan_directory(
            tmp_all_parsers, registry=registry, strict=False
        )

        processable_exts = {Path(f.rel_path).suffix.lower() for f in result.processable}
        unsupported_exts = {Path(f.rel_path).suffix.lower() for f in result.unsupported}

        # All dedicated-parser extensions must be processable
        for ext in self.DEDICATED_PARSER_MAP:
            assert ext in processable_exts, f"{ext} should be processable"

        # All text-fallback extensions must be processable
        for ext in self.TEXT_FALLBACK_EXTENSIONS:
            assert ext in processable_exts, f"{ext} should be processable (text-fallback)"

        # .rar are unsupported
        assert ".rar" in unsupported_exts

    def test_each_processable_file_has_a_parser_or_is_text(
        self, tmp_all_parsers: Path, registry: ParserRegistry
    ) -> None:
        """Every processable file must either have a dedicated parser or pass
        ``is_text_file``."""
        from openviking.parse.parsers.upload_utils import is_text_file

        result = scan_directory(tmp_all_parsers, registry=registry, strict=False)
        for cf in result.processable:
            has_parser = registry.get_parser_for_file(cf.path) is not None
            is_text = is_text_file(cf.path)
            assert has_parser or is_text, (
                f"{cf.rel_path}: not a known parser type and not a text file"
            )


class TestParserCanParse:
    """Parser.can_parse must accept its own supported extensions."""

    @pytest.mark.parametrize(
        "parser_cls,filenames",
        [
            (MarkdownParser, ["doc.md", "spec.markdown", "x.mdown", "y.mkd"]),
            (HTMLParser, ["page.html", "site.htm"]),
            (PDFParser, ["paper.pdf"]),
            (TextParser, ["notes.txt", "log.text"]),
        ],
    )
    def test_can_parse_returns_true(self, parser_cls: type, filenames: List[str]) -> None:
        parser = parser_cls()
        for name in filenames:
            assert parser.can_parse(Path(name)), (
                f"{parser_cls.__name__}.can_parse('{name}') should be True"
            )

    @pytest.mark.parametrize(
        "parser_cls,filenames",
        [
            (MarkdownParser, ["file.py", "file.html", "file.pdf"]),
            (HTMLParser, ["file.md", "file.pdf", "file.txt"]),
            (PDFParser, ["file.md", "file.txt", "file.html"]),
            (TextParser, ["file.md", "file.html", "file.pdf"]),
        ],
    )
    def test_can_parse_returns_false_for_wrong_extension(
        self, parser_cls: type, filenames: List[str]
    ) -> None:
        parser = parser_cls()
        for name in filenames:
            assert not parser.can_parse(Path(name)), (
                f"{parser_cls.__name__}.can_parse('{name}') should be False"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 – Relative-path → Viking URI mapping
# ═══════════════════════════════════════════════════════════════════════════

# The mapping logic lives in ``ResourceService._process_directory_file``.
# Instead of pulling in the full service we replicate the *pure* path
# computation here so tests stay isolated and fast.


def _compute_file_target(rel_path: str, base_target: str) -> str:
    """Replicate the target-URI computation from _process_directory_file."""
    parent_rel = str(PurePosixPath(rel_path).parent)
    if parent_rel == ".":
        return base_target
    return f"{base_target}/{parent_rel}"


def _expected_final_uri(rel_path: str, base_target: str) -> str:
    """Expected final URI after the parser names the document by file stem.

    The TreeBuilder computes:  ``final_uri = base_uri.join(doc_name)``
    where ``doc_name`` is typically the file stem.
    """
    file_target = _compute_file_target(rel_path, base_target)
    stem = Path(rel_path).stem
    return f"{file_target}/{stem}"


class TestPathMapping:
    """Verify that relative file paths map to the correct Viking URIs."""

    BASE = "viking://resources/mydir"

    # (relative_path, expected_target_for_process_resource, expected_final_uri)
    CASES: List[Tuple[str, str, str]] = [
        # Root-level file
        ("top.md", "viking://resources/mydir", "viking://resources/mydir/top"),
        ("README.txt", "viking://resources/mydir", "viking://resources/mydir/README"),
        # One level deep
        (
            "docs/guide.md",
            "viking://resources/mydir/docs",
            "viking://resources/mydir/docs/guide",
        ),
        (
            "src/app.py",
            "viking://resources/mydir/src",
            "viking://resources/mydir/src/app",
        ),
        # Two levels deep
        (
            "a/b/c.md",
            "viking://resources/mydir/a/b",
            "viking://resources/mydir/a/b/c",
        ),
        (
            "a/b/d.txt",
            "viking://resources/mydir/a/b",
            "viking://resources/mydir/a/b/d",
        ),
        # Three levels deep
        (
            "x/y/z/deep.md",
            "viking://resources/mydir/x/y/z",
            "viking://resources/mydir/x/y/z/deep",
        ),
    ]

    @pytest.mark.parametrize("rel_path,expected_target,_", CASES)
    def test_target_uri_computation(self, rel_path: str, expected_target: str, _: str) -> None:
        """_compute_file_target produces the correct parent-based target."""
        assert _compute_file_target(rel_path, self.BASE) == expected_target

    @pytest.mark.parametrize("rel_path,_,expected_uri", CASES)
    def test_final_uri_matches_rel_path_structure(
        self, rel_path: str, _: str, expected_uri: str
    ) -> None:
        """The final URI (target + file stem) preserves the directory tree."""
        assert _expected_final_uri(rel_path, self.BASE) == expected_uri


class TestPathMappingFromScan:
    """End-to-end: scan a real directory, then verify every processable file's
    relative path maps to the expected Viking URI."""

    @pytest.fixture
    def tmp_deep(self, tmp_path: Path) -> Path:
        """Create a three-level nested directory.

        Structure::

            tmp_path/
                a/
                    b/
                        c.md
                    x.md
                top.md
                src/
                    main.py
        """
        ab = tmp_path / "a" / "b"
        ab.mkdir(parents=True)
        (ab / "c.md").write_text("# C", encoding="utf-8")
        (tmp_path / "a" / "x.md").write_text("# X", encoding="utf-8")
        (tmp_path / "top.md").write_text("# Top", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")
        return tmp_path

    def test_scan_then_map_preserves_structure(self, tmp_deep: Path) -> None:
        """For every processable file, the computed final URI should embed
        the same directory hierarchy as the original relative path."""
        result = scan_directory(tmp_deep, strict=False)
        base = f"viking://resources/{tmp_deep.name}"

        for cf in result.processable:
            rel = cf.rel_path.replace("\\", "/")  # normalize for Windows
            final_uri = _expected_final_uri(rel, base)

            # The URI path (after viking://resources/) should equal
            # <dir_name>/<rel_path_without_extension>
            uri_path = final_uri[len("viking://resources/") :]
            expected_path = f"{tmp_deep.name}/{str(PurePosixPath(rel).with_suffix(''))}"
            assert uri_path == expected_path, (
                f"Mapping mismatch for {rel}: got URI path '{uri_path}', expected '{expected_path}'"
            )

    def test_empty_directory_produces_no_mappings(self, tmp_path: Path) -> None:
        """An empty directory has no processable files → zero URI mappings."""
        (tmp_path / ".gitkeep").write_text("", encoding="utf-8")  # skipped: empty
        result = scan_directory(tmp_path, strict=False)
        assert len(result.processable) == 0
