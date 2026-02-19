# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Directory pre-scan validation module for OpenViking.

Implements phase-one of directory import (RFC #83): traverse directory tree,
classify files as processable / unsupported, validate format,
and report errors or warnings with optional strict mode.
"""

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Union

from openviking.parse.parsers.constants import IGNORE_DIRS
from openviking.parse.parsers.upload_utils import is_text_file
from openviking.parse.registry import ParserRegistry, get_registry
from openviking_cli.exceptions import UnsupportedDirectoryFilesError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# File classification labels
CLASS_PROCESSABLE = "processable"
CLASS_UNSUPPORTED = "unsupported"


@dataclass
class ClassifiedFile:
    """A single file with its classification and relative path."""

    path: Path
    rel_path: str
    classification: str  # CLASS_PROCESSABLE | CLASS_UNSUPPORTED


@dataclass
class DirectoryScanResult:
    """Result of directory pre-scan: classified files and optional warnings."""

    root: Path
    processable: List[ClassifiedFile] = field(default_factory=list)
    unsupported: List[ClassifiedFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # reason -> count or paths for debugging
    warnings: List[str] = field(default_factory=list)

    def all_processable_files(self) -> List[ClassifiedFile]:
        """Return processable files in order (for phase-two routing)."""
        return self.processable


def _should_skip_file(file_path: Path) -> tuple[bool, str]:
    """
    Return (True, reason) if the file should be skipped (not counted as supported/unsupported).

    Skip: dot files, symlinks, empty files (per RFC phase-one).
    """
    if file_path.name.startswith("."):
        return True, "dot file"
    if file_path.is_symlink():
        return True, "symlink"
    try:
        if file_path.stat().st_size == 0:
            return True, "empty file"
    except OSError:
        return True, "os error"
    return False, ""


def _should_skip_directory(
    dir_path: Path,
    root: Path,
    ignore_dirs: Optional[Set[str]] = None,
) -> tuple[bool, str]:
    """
    Return (True, reason) if the directory should be skipped (not counted as supported/unsupported).

    Skip: dot directories, symlinks, IGNORE_DIRS, and any dir in ignore_dirs.
    ignore_dirs: directory names, or relative paths (relative to root).
                 - Name only (no path sep): skip any dir with that name.
                 - Relative path (e.g. "parse/", "./storage/vectordb", "openviking/parse"): skip when dir's path matches.
    """
    if dir_path.name.startswith("."):
        return True, "dot directory"
    if dir_path.is_symlink():
        return True, "symlink"
    if dir_path.name in IGNORE_DIRS:
        return True, "IGNORE_DIRS"
    if not ignore_dirs:
        return False, ""

    try:
        dir_rel = _normalize_rel_path(str(dir_path.relative_to(root)))
    except ValueError:
        dir_rel = _normalize_rel_path(str(dir_path))

    for entry in ignore_dirs:
        if not entry or not str(entry).strip():
            continue
        raw = str(entry).strip().replace("\\", "/")
        if not raw:
            continue

        # Relative path (contains '/'): match by path relative to root
        if "/" in raw:
            prefix = raw.rstrip("/").lstrip("./")
            if prefix and (dir_rel == prefix or dir_rel.startswith(prefix + "/")):
                return True, "ignore_dirs"
            continue

        # Single segment: match by directory name
        if dir_path.name == raw:
            return True, "ignore_dirs"

    return False, ""


def _parse_patterns(value: Optional[str]) -> List[str]:
    """Parse comma-separated include/exclude string into list of stripped patterns."""
    if not value or not value.strip():
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def _normalize_rel_path(rel_path: str) -> str:
    """Use forward slashes for consistent matching across platforms."""
    return rel_path.replace("\\", "/")


def _matches_include(path_name: str, patterns: List[str]) -> bool:
    """True if file is included: no patterns means include all; else match path name against any pattern."""
    if not patterns:
        return True
    return any(fnmatch.fnmatch(path_name, p) for p in patterns)


def _matches_exclude(rel_path_norm: str, path_name: str, patterns: List[str]) -> bool:
    """
    True if file is excluded.
    - Pattern ending with '/' is a path prefix (e.g. 'drafts/' excludes paths under drafts/).
    - Otherwise match path name as glob (e.g. '*.tmp').
    """
    if not patterns:
        return False
    for p in patterns:
        if p.endswith("/"):
            prefix = p.rstrip("/").replace("\\", "/")
            if rel_path_norm == prefix or rel_path_norm.startswith(prefix + "/"):
                return True
        else:
            if fnmatch.fnmatch(path_name, p):
                return True
    return False


def _classify_file(
    file_path: Path,
    registry: ParserRegistry,
) -> str:
    """
    Classify a single file as CLASS_PROCESSABLE or CLASS_UNSUPPORTED.

    Processable: ParserRegistry has a parser, or is_text_file (code/config/docs).
    """
    # Normal classification logic
    if registry.get_parser_for_file(file_path) is not None:
        return CLASS_PROCESSABLE
    if is_text_file(file_path):
        return CLASS_PROCESSABLE
    return CLASS_UNSUPPORTED


def scan_directory(
    root: Union[str, Path],
    registry: Optional[ParserRegistry] = None,
    strict: bool = True,
    ignore_dirs: Optional[Set[str]] = None,
    include: Optional[str] = None,
    exclude: Optional[str] = None,
) -> DirectoryScanResult:
    """
    Traverse directory tree and classify every file (phase-one validation).

    - Skips directories in IGNORE_DIRS (or ignore_dirs), and skips dot files,
      symlinks, and empty files (they are not included in any list).
    - If include is set, only files whose name matches one of the glob patterns are considered
      (e.g. include="*.pdf,*.md"). If exclude is set, files matching any exclude pattern are
      skipped (e.g. exclude="drafts/" for path prefix, or "*.tmp" for name glob).
    - Classifies remaining files:
      - processable: ParserRegistry has a parser, or is_text_file (code/docs/config)
      - unsupported: everything else

    Args:
        root: Directory path to scan.
        registry: Parser registry for processable detection. Defaults to get_registry().
        strict: If True, raise UnsupportedDirectoryFilesError when any unsupported file exists.
                If False, append warnings and continue (unsupported list still populated).
        ignore_dirs: Directory names or relative paths (to root) to skip. E.g. "parse", "parse/", "./storage/".
        include: Comma-separated glob patterns for file names; only matching files are included
                 (e.g. "*.pdf,*.md"). If not set, all files (subject to exclude) are considered.
        exclude: Comma-separated patterns: trailing '/' = path prefix (e.g. "drafts/"),
                 else glob on file name (e.g. "*.tmp").

    Returns:
        DirectoryScanResult with processable, unsupported, warnings.

    Raises:
        UnsupportedDirectoryFilesError: When strict=True and there is at least one unsupported file.
        FileNotFoundError: When root does not exist.
        NotADirectoryError: When root is not a directory.
    """
    root = Path(root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    effective_registry = registry if registry is not None else get_registry()
    include_patterns = _parse_patterns(include)
    exclude_patterns = _parse_patterns(exclude)

    result = DirectoryScanResult(root=root)
    for dir_path_str, dir_names, file_names in os.walk(root, topdown=True):
        dir_path = Path(dir_path_str)

        # Prune subdirectories in-place so os.walk won't descend into them
        kept = []
        for d in dir_names:
            sub = dir_path / d
            skip, reason = _should_skip_directory(sub, root, ignore_dirs)
            if skip:
                result.skipped.append(f"{sub.relative_to(root)} ({reason})")
            else:
                kept.append(d)
        dir_names[:] = kept

        for name in file_names:
            file_path = dir_path / name
            try:
                rel_path = str(file_path.relative_to(root))
            except ValueError:
                rel_path = str(file_path)
            rel_path_norm = _normalize_rel_path(rel_path)

            skip, reason = _should_skip_file(file_path)
            if skip:
                result.skipped.append(f"{rel_path} ({reason})")
                continue

            if include_patterns and not _matches_include(name, include_patterns):
                result.skipped.append(f"{rel_path} (excluded by include filter)")
                continue
            if exclude_patterns and _matches_exclude(rel_path_norm, name, exclude_patterns):
                result.skipped.append(f"{rel_path} (excluded by exclude filter)")
                continue

            classification = _classify_file(file_path, effective_registry)
            classified = ClassifiedFile(
                path=file_path, rel_path=rel_path, classification=classification
            )
            if classification == CLASS_PROCESSABLE:
                result.processable.append(classified)
            else:
                result.unsupported.append(classified)

    if result.unsupported:
        unsupported_paths = [f.rel_path for f in result.unsupported]
        msg = (
            f"Directory contains {len(result.unsupported)} unsupported file(s). "
            f"Unsupported: {unsupported_paths[:10]}{'...' if len(unsupported_paths) > 10 else ''}"
        )
        if strict:
            raise UnsupportedDirectoryFilesError(msg, unsupported_paths)
        result.warnings.append(msg)
        for rel in unsupported_paths:
            result.warnings.append(f"  - {rel}")

    result.processable.sort(key=lambda x: x.rel_path)
    result.unsupported.sort(key=lambda x: x.rel_path)
    result.skipped.sort()
    result.warnings.sort()
    return result
