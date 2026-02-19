# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for directory pre-scan validation module (RFC #83 T1)."""

from pathlib import Path

import pytest

from openviking.parse.directory_scan import (
    CLASS_PROCESSABLE,
    ClassifiedFile,
    DirectoryScanResult,
    scan_directory,
)
from openviking.parse.registry import ParserRegistry
from openviking_cli.exceptions import UnsupportedDirectoryFilesError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_tree(tmp_path: Path) -> Path:
    """Create a directory tree with mixed file types for scan tests."""
    # rich (parser exists): .md, .pdf, .html, .txt
    (tmp_path / "readme.md").write_text("# README", encoding="utf-8")
    (tmp_path / "doc.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "note.txt").write_text("plain text", encoding="utf-8")

    # text (code/config, no dedicated parser or text parser only): .py, .yaml
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("key: value", encoding="utf-8")

    # unsupported: unknown extension
    (tmp_path / "data.xyz").write_text("unknown", encoding="utf-8")
    (tmp_path / "archive.rar").write_bytes(b"RAR\x00")

    # skipped: dot file, empty, ignored dir
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
    (tmp_path / "empty.txt").write_bytes(b"")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg").write_text("x", encoding="utf-8")

    # subdir with mixed
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "app.py").write_text("code", encoding="utf-8")
    (sub / "custom.bin").write_bytes(b"\x00\x01")

    return tmp_path


@pytest.fixture
def tmp_all_supported(tmp_path: Path) -> Path:
    """Directory where every file is rich or text (no unsupported)."""
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "b.py").write_text("pass", encoding="utf-8")
    (tmp_path / "c.yaml").write_text("k: v", encoding="utf-8")
    return tmp_path


@pytest.fixture
def tmp_with_drafts(tmp_path: Path) -> Path:
    """Tree with drafts/ subdir and mixed extensions for include/exclude tests."""
    (tmp_path / "readme.md").write_text("# README", encoding="utf-8")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.0")
    (tmp_path / "main.py").write_text("pass", encoding="utf-8")
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "draft.pdf").write_bytes(b"%PDF")
    (tmp_path / "drafts" / "notes.md").write_text("notes", encoding="utf-8")
    (tmp_path / "drafts" / "skip.py").write_text("x", encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry() -> ParserRegistry:
    """Default parser registry (includes markdown, pdf, html, text, etc.)."""
    return ParserRegistry(register_optional=False)


# ---------------------------------------------------------------------------
# Traversal and classification
# ---------------------------------------------------------------------------


class TestScanDirectoryTraversal:
    """Test that scan_directory traverses the tree and respects IGNORE_DIRS."""

    def test_traverses_all_non_ignored_dirs(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        result: DirectoryScanResult = scan_directory(tmp_tree, registry=registry, strict=False)
        rel_paths = {f.rel_path for f in result.all_processable_files()}
        assert "readme.md" in rel_paths
        assert "main.py" in rel_paths
        assert "src/app.py" in rel_paths
        # Ignored dirs must not appear
        assert not any(".git" in p for p in rel_paths)
        assert not any("node_modules" in p for p in rel_paths)

    def test_skips_dot_files_and_empty(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        result: DirectoryScanResult = scan_directory(tmp_tree, registry=registry, strict=False)
        all_rel = [f.rel_path for f in result.processable + result.unsupported]
        assert ".hidden" not in all_rel
        assert "empty.txt" not in all_rel
        assert any("empty" in s or "dot" in s for s in result.skipped)


class TestScanDirectoryClassification:
    """Test processable / unsupported classification."""

    def test_processable_includes_parser_files(
        self, tmp_tree: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(tmp_tree, registry=registry, strict=False)
        processable_rel = [f.rel_path for f in result.processable]
        assert "readme.md" in processable_rel
        assert "doc.html" in processable_rel
        assert "note.txt" in processable_rel

    def test_processable_includes_code_or_config(
        self, tmp_tree: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(tmp_tree, registry=registry, strict=False)
        processable_rel = [f.rel_path for f in result.processable]
        assert "main.py" in processable_rel
        assert "config.yaml" in processable_rel
        assert "src/app.py" in processable_rel

    def test_unsupported_unknown_ext(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        result: DirectoryScanResult = scan_directory(tmp_tree, registry=registry, strict=False)
        unsup_rel = [f.rel_path for f in result.unsupported]
        assert "data.xyz" in unsup_rel
        assert "archive.rar" in unsup_rel
        assert "src/custom.bin" in unsup_rel


# ---------------------------------------------------------------------------
# Strict vs non-strict (unsupported handling)
# ---------------------------------------------------------------------------


class TestStrictParameter:
    """Test strict=True raises; strict=False adds warnings."""

    def test_strict_raises_when_unsupported(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        with pytest.raises(UnsupportedDirectoryFilesError) as exc_info:
            scan_directory(tmp_tree, registry=registry, strict=True)
        err = exc_info.value
        assert err.unsupported_files
        assert "data.xyz" in err.unsupported_files or any("xyz" in p for p in err.unsupported_files)

    def test_non_strict_returns_warnings(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        result: DirectoryScanResult = scan_directory(tmp_tree, registry=registry, strict=False)
        assert result.unsupported
        assert result.warnings
        assert any("unsupported" in w.lower() for w in result.warnings)

    def test_strict_passes_when_no_unsupported(
        self, tmp_all_supported: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_all_supported, registry=registry, strict=True
        )
        assert not result.unsupported
        assert not result.warnings


# ---------------------------------------------------------------------------
# Exception reporting
# ---------------------------------------------------------------------------


class TestExceptionReporting:
    """Test that UnsupportedDirectoryFilesError carries full unsupported list."""

    def test_error_contains_all_unsupported_paths(
        self, tmp_tree: Path, registry: ParserRegistry
    ) -> None:
        with pytest.raises(UnsupportedDirectoryFilesError) as exc_info:
            scan_directory(tmp_tree, registry=registry, strict=True)
        paths = exc_info.value.unsupported_files
        assert len(paths) >= 3  # data.xyz, archive.rar, src/custom.bin
        assert "data.xyz" in paths
        assert "archive.rar" in paths
        assert "src/custom.bin" in paths


# ---------------------------------------------------------------------------
# Edge cases and API
# ---------------------------------------------------------------------------


class TestScanDirectoryEdgeCases:
    """Edge cases: missing dir, not a dir, custom ignore_dirs."""

    def test_raises_on_nonexistent(self, registry: ParserRegistry) -> None:
        with pytest.raises(FileNotFoundError):
            scan_directory("/nonexistent/path/12345", registry=registry)

    def test_raises_on_file_not_dir(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        with pytest.raises(NotADirectoryError):
            scan_directory(tmp_tree / "readme.md", registry=registry)

    def test_custom_ignore_dirs(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        """Custom ignore_dirs supports directory names and relative paths."""

        # 1) Ignore by directory name ("src")
        result_name: DirectoryScanResult = scan_directory(
            tmp_tree,
            registry=registry,
            strict=False,
            ignore_dirs={".git", "node_modules", "src"},
        )
        all_rel_name = [f.rel_path for f in result_name.processable + result_name.unsupported]
        assert not any(p.startswith("src/") for p in all_rel_name)

        # 2) Ignore by relative path with trailing slash ("src/")
        result_rel_slash: DirectoryScanResult = scan_directory(
            tmp_tree,
            registry=registry,
            strict=False,
            ignore_dirs=["src/"],
        )
        all_rel_slash = [
            f.rel_path for f in result_rel_slash.processable + result_rel_slash.unsupported
        ]
        assert not any(p.startswith("src/") for p in all_rel_slash)

        # 3) Ignore by relative path with ./ prefix ("./src")
        result_rel_dot: DirectoryScanResult = scan_directory(
            tmp_tree,
            registry=registry,
            strict=False,
            ignore_dirs=["./src"],
        )
        all_rel_dot = [f.rel_path for f in result_rel_dot.processable + result_rel_dot.unsupported]
        assert not any(p.startswith("src/") for p in all_rel_dot)

    def test_result_all_processable(
        self, tmp_all_supported: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_all_supported, registry=registry, strict=True
        )
        all_p = result.all_processable_files()
        assert len(all_p) == len(result.processable)
        for cf in all_p:
            assert cf.classification == CLASS_PROCESSABLE


# ---------------------------------------------------------------------------
# Include / exclude filters
# ---------------------------------------------------------------------------


class TestIncludeExclude:
    """Test include and exclude parameters for user-defined file filtering."""

    def test_include_only_matching_files(
        self, tmp_with_drafts: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_with_drafts,
            registry=registry,
            strict=False,
            include="*.pdf,*.md",
        )
        rel_paths = [f.rel_path for f in result.processable + result.unsupported]
        assert "readme.md" in rel_paths
        assert "doc.pdf" in rel_paths
        assert "drafts/draft.pdf" in rel_paths
        assert "drafts/notes.md" in rel_paths
        assert "main.py" not in rel_paths
        assert "drafts/skip.py" not in rel_paths
        skipped_reasons = " ".join(result.skipped)
        assert "excluded by include" in skipped_reasons

    def test_exclude_path_prefix(self, tmp_with_drafts: Path, registry: ParserRegistry) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_with_drafts,
            registry=registry,
            strict=False,
            exclude="drafts/",
        )
        rel_paths = [f.rel_path for f in result.processable + result.unsupported]
        assert "readme.md" in rel_paths
        assert "doc.pdf" in rel_paths
        assert "main.py" in rel_paths
        assert "drafts/draft.pdf" not in rel_paths
        assert "drafts/notes.md" not in rel_paths
        assert "drafts/skip.py" not in rel_paths
        skipped_reasons = " ".join(result.skipped)
        assert "excluded by exclude" in skipped_reasons

    def test_include_and_exclude_combined(
        self, tmp_with_drafts: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_with_drafts,
            registry=registry,
            strict=False,
            include="*.pdf,*.md",
            exclude="drafts/",
        )
        rel_paths = [f.rel_path for f in result.processable + result.unsupported]
        assert "readme.md" in rel_paths
        assert "doc.pdf" in rel_paths
        assert "drafts/draft.pdf" not in rel_paths
        assert "drafts/notes.md" not in rel_paths
        assert "main.py" not in rel_paths

    def test_exclude_name_glob(self, tmp_tree: Path, registry: ParserRegistry) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_tree,
            registry=registry,
            strict=False,
            exclude="*.rar,*.xyz",
        )
        unsup_rel = [f.rel_path for f in result.unsupported]
        assert "data.xyz" not in unsup_rel
        assert "archive.rar" not in unsup_rel
        assert "src/custom.bin" in unsup_rel

    def test_no_include_means_all_files(
        self, tmp_with_drafts: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_with_drafts, registry=registry, strict=False
        )
        rel_paths = [f.rel_path for f in result.processable + result.unsupported]
        assert "readme.md" in rel_paths
        assert "doc.pdf" in rel_paths
        assert "main.py" in rel_paths
        assert "drafts/draft.pdf" in rel_paths
        assert "drafts/notes.md" in rel_paths
        assert "drafts/skip.py" in rel_paths

    def test_empty_include_exclude_unchanged(
        self, tmp_tree: Path, registry: ParserRegistry
    ) -> None:
        r1 = scan_directory(tmp_tree, registry=registry, strict=False)
        r2 = scan_directory(tmp_tree, registry=registry, strict=False, include="", exclude="")
        paths1 = {f.rel_path for f in r1.processable + r1.unsupported}
        paths2 = {f.rel_path for f in r2.processable + r2.unsupported}
        assert paths1 == paths2

    def test_ignore_dirs_with_include_and_exclude(
        self, tmp_with_drafts: Path, registry: ParserRegistry
    ) -> None:
        """Combined ignore_dirs + include + exclude should work together."""

        result: DirectoryScanResult = scan_directory(
            tmp_with_drafts,
            registry=registry,
            strict=False,
            ignore_dirs={"drafts"},
            include="*.md,*.py",
            exclude="main.py",
        )

        rel_paths = [f.rel_path for f in result.processable + result.unsupported]

        # ignore_dirs: drafts/ 整个目录被跳过
        assert not any(p.startswith("drafts/") for p in rel_paths)
        # include: .md 仍然被保留
        assert "readme.md" in rel_paths
        # exclude: main.py 被排除
        assert "main.py" not in rel_paths

        skipped_reasons = " ".join(result.skipped)
        assert "ignore_dirs" in skipped_reasons
        assert "excluded by include" in skipped_reasons or "excluded by exclude" in skipped_reasons


class TestClassifiedFileAndResult:
    """Test ClassifiedFile and DirectoryScanResult types."""

    def test_classified_file_has_rel_path_and_classification(self) -> None:
        p = Path("/tmp/foo/bar.txt")
        cf = ClassifiedFile(path=p, rel_path="bar.txt", classification=CLASS_PROCESSABLE)
        assert cf.rel_path == "bar.txt"
        assert cf.classification == CLASS_PROCESSABLE

    def test_scan_result_root_and_lists(
        self, tmp_all_supported: Path, registry: ParserRegistry
    ) -> None:
        result: DirectoryScanResult = scan_directory(
            tmp_all_supported, registry=registry, strict=True
        )
        assert result.root == tmp_all_supported.resolve()
        assert isinstance(result.processable, list)
        assert isinstance(result.unsupported, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.skipped, list)
