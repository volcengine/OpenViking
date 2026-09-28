# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared upload utilities for directory and file uploading to VikingFS."""

import asyncio
import os
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple, Union

from openviking.parse.gitignore import GitignoreMatcher
from openviking.parse.output import (
    ARTIFACT_MANIFEST_NAME as ARTIFACT_MANIFEST_NAME,
)
from openviking.parse.output import (
    write_artifact_manifest,
)
from openviking.parse.parsers.constants import (
    ADDITIONAL_TEXT_EXTENSIONS,
    CODE_EXTENSIONS,
    DOCUMENTATION_EXTENSIONS,
    IGNORE_EXTENSIONS,
)
from openviking.parse.parsers.text_encoding import normalize_text_bytes
from openviking.utils.content_hash import content_md5
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


# Common text files that have no extension but should be treated as text.
_EXTENSIONLESS_TEXT_NAMES: Set[str] = {
    "README",
    "LICENSE",
    "LICENCE",
    "MAKEFILE",
    "DOCKERFILE",
    "VAGRANTFILE",
    "GEMFILE",
    "RAKEFILE",
    "PROCFILE",
    "CODEOWNERS",
    "AUTHORS",
    "CONTRIBUTORS",
    "CHANGELOG",
    "CHANGES",
    "NEWS",
    "NOTICE",
    "TODO",
    "BUILD",
}


def is_text_file(file_path: Union[str, Path]) -> bool:
    """Return True when the file extension is treated as text content."""
    p = Path(file_path)
    extension = p.suffix.lower()
    if extension:
        if (
            extension in CODE_EXTENSIONS
            or extension in DOCUMENTATION_EXTENSIONS
            or extension in ADDITIONAL_TEXT_EXTENSIONS
        ):
            return True

        from openviking.parse.parsers.code.ast.providers import supports_code_skeleton

        return supports_code_skeleton(str(p))
    # Extensionless files: check against known text file names (case-insensitive).
    return p.name.upper() in _EXTENSIONLESS_TEXT_NAMES


def detect_and_convert_encoding(content: bytes, file_path: Union[str, Path] = "") -> bytes:
    """Detect text encoding and normalize content to UTF-8 when needed."""
    if not is_text_file(file_path):
        return content
    return normalize_text_bytes(content, file_path)


def is_empty_file(file_path: Path) -> bool:
    """Return whether a file is empty or contains only ASCII whitespace."""
    with file_path.open("rb") as source:
        while chunk := source.read(8192):
            if chunk.strip():
                return False
    return True


def should_skip_file(
    file_path: Path,
    max_file_size: int = 10 * 1024 * 1024,
    ignore_extensions: Optional[Set[str]] = None,
) -> Tuple[bool, str]:
    """Return whether to skip a file and the reason for skipping."""
    effective_ignore_extensions = (
        ignore_extensions if ignore_extensions is not None else IGNORE_EXTENSIONS
    )

    if file_path.name.startswith("."):
        return True, "hidden file"

    if file_path.is_symlink():
        return True, "symbolic link"

    extension = file_path.suffix.lower()
    if extension in effective_ignore_extensions:
        return True, f"ignored extension: {extension}"

    try:
        file_size = file_path.stat().st_size
        if file_size > max_file_size:
            return True, f"file too large: {file_size} bytes"
        if is_empty_file(file_path):
            return True, "empty file"
    except OSError as exc:
        return True, f"os error: {exc}"

    return False, ""


_UPLOAD_CONCURRENCY = 8


async def upload_directory(
    local_dir: Path,
    base_rel: str,
    *,
    store: Any,
    artifact_ref: Any,
    ignore_dirs: Optional[Union[Set[str], List[str], str]] = None,
    ignore_extensions: Optional[Set[str]] = None,
    max_file_size: int = 10 * 1024 * 1024,
    include: Optional[str] = None,
    exclude: Optional[str] = None,
) -> Tuple[int, List[str]]:
    """Upload a directory into a parse output store, returning (count, warnings).

    Artifacts are written through the backend-agnostic :class:`ParseOutputStore`,
    so AGFS and local backends share one path with no branching. Files land under
    ``base_rel`` (e.g. ``repository``) as artifact-relative paths the store
    resolves to a local dir or an AGFS temp URI. An md5 manifest of the final
    (encoding-normalized) bytes is written at the artifact root so the incremental
    diff can compare fingerprints without re-reading file contents.
    """
    effective_ignore_extensions = (
        ignore_extensions if ignore_extensions is not None else IGNORE_EXTENSIONS
    )
    gitignore_matcher = GitignoreMatcher(local_dir)
    # Keep repository uploads aligned with DirectoryParser's public filtering
    # contract without duplicating its path/glob semantics.
    from openviking.parse.directory_scan import (
        _matches_exclude,
        _matches_include,
        _parse_patterns,
        _should_skip_directory,
    )

    if isinstance(ignore_dirs, str):
        parsed_ignore_dirs: Optional[Set[str]] = set(_parse_patterns(ignore_dirs)) or None
    elif ignore_dirs:
        parsed_ignore_dirs = {str(item) for item in ignore_dirs}
    else:
        parsed_ignore_dirs = None
    include_patterns = _parse_patterns(include)
    exclude_patterns = _parse_patterns(exclude)

    warnings: List[str] = []

    # --- Phase 1: Collect files and their artifact-relative parent dirs ---
    base = base_rel.strip("/")
    files_to_upload: List[Tuple[Path, str]] = []
    parent_dirs: Set[str] = set()

    for root, dirs, files in os.walk(local_dir):
        dir_path = Path(root)
        dir_spec = gitignore_matcher.spec_for_dir(dir_path)

        # Prune subdirectories in-place so os.walk won't descend into them
        kept = []
        for d in dirs:
            sub_dir_path = dir_path / d
            should_skip, _ = _should_skip_directory(
                sub_dir_path,
                local_dir,
                parsed_ignore_dirs,
            )
            if should_skip:
                continue

            if gitignore_matcher.is_ignored_dir(sub_dir_path, dir_spec):
                continue

            kept.append(d)

        dirs[:] = kept

        for file_name in files:
            file_path = dir_path / file_name
            should_skip, _ = should_skip_file(
                file_path,
                max_file_size=max_file_size,
                ignore_extensions=effective_ignore_extensions,
            )
            if should_skip:
                continue

            if gitignore_matcher.is_ignored_file(file_path, dir_spec):
                continue

            rel_path_str = str(file_path.relative_to(local_dir)).replace(os.sep, "/")
            if include_patterns and not _matches_include(file_name, include_patterns):
                continue
            if exclude_patterns and _matches_exclude(
                rel_path_str,
                file_name,
                exclude_patterns,
            ):
                continue
            # Artifact-relative path under the resource root; the store resolves it
            # to a local/agfs location.
            target = f"{base}/{rel_path_str}" if base else rel_path_str
            files_to_upload.append((file_path, target))
            if "/" in target:
                parent_dirs.add(target.rsplit("/", 1)[0])

    # --- Phase 2: Pre-create unique parent dirs (shallowest first) ---
    # store.write_bytes also creates parents implicitly; this batches the unique
    # directories once to avoid redundant per-file creation on remote backends.
    for rel_dir in sorted(parent_dirs, key=lambda value: (value.count("/"), value)):
        try:
            await store.mkdir(artifact_ref, rel_dir)
        except Exception as e:
            logger.warning(f"Failed to create artifact directory {rel_dir}: {e}")

    # --- Phase 3: Upload files concurrently ---
    sem = asyncio.Semaphore(_UPLOAD_CONCURRENCY)
    errors: List[Optional[str]] = [None] * len(files_to_upload)
    # rel_path -> md5 of final bytes, collected for the manifest.
    md5_by_target: dict[str, str] = {}

    async def _upload_one(idx: int, file_path: Path, target: str) -> None:
        async with sem:

            def _read_and_encode() -> bytes:
                content = file_path.read_bytes()
                return detect_and_convert_encoding(content, file_path)

            try:
                encoded = await asyncio.to_thread(_read_and_encode)
                await store.write_bytes(artifact_ref, target, encoded)
                # Fingerprint the final (normalized) bytes at the write point; this
                # is a local read, no extra remote IO.
                md5_by_target[target] = content_md5(encoded)
            except Exception as exc:
                errors[idx] = f"Failed to upload {file_path}: {exc}"

    await asyncio.gather(*[_upload_one(i, fp, tgt) for i, (fp, tgt) in enumerate(files_to_upload)])

    for err in errors:
        if err:
            warnings.append(err)
            logger.warning(err)

    # Persist the md5 manifest at the artifact root so the incremental diff can
    # compare fingerprints without re-reading files. Only write it when every file
    # uploaded cleanly, so a partial manifest never masquerades as complete.
    if not any(errors):
        await write_artifact_manifest(store, artifact_ref, md5_by_target)

    uploaded_count = sum(1 for e in errors if e is None)
    return uploaded_count, warnings
