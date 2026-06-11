# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Post-commit image URI rewriting for OpenViking.

Scans markdown files in VikingFS after source commit and rewrites local
image references to viking:// URIs based on images stored in the ./images/
directory.
"""

import json
import re
from typing import TYPE_CHECKING, Dict, Optional, Set

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.storage.transaction.lock_handle import LockHandle

logger = get_logger(__name__)

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_REMOTE_PREFIXES = ("http://", "https://", "viking://", "data:", "ftp://")
_FENCE_PATTERN = re.compile(r"^(\s{0,3})(`{3,}|~{3,})")
_LIST_ITEM_PATTERN = re.compile(r"^(\s{0,3})([-*+]|\d{1,9}[.)])(\s+)")


def _is_remote_uri(path: str) -> bool:
    return any(path.startswith(p) for p in _REMOTE_PREFIXES)


def _inline_code_ranges(line: str):
    """Yield (start, end) offsets of inline code spans within a single line.

    A code span is a run of N backticks closed by another run of exactly N
    backticks. Unterminated runs are not treated as code.
    """
    i = 0
    n = len(line)
    while i < n:
        if line[i] != "`":
            i += 1
            continue
        j = i
        while j < n and line[j] == "`":
            j += 1
        run = j - i
        k = j
        closed = False
        while k < n:
            if line[k] != "`":
                k += 1
                continue
            m = k
            while m < n and line[m] == "`":
                m += 1
            if m - k == run:
                yield (i, m)
                i = m
                closed = True
                break
            k = m
        if not closed:
            i = j


def _protected_ranges(content: str):
    """Compute character ranges that must not be rewritten.

    Covers fenced code blocks, indented code blocks and inline code spans so
    that Markdown image examples inside code are left untouched.
    """
    ranges = []
    offset = 0
    in_fence = False
    fence_char = ""
    fence_len = 0
    in_indent_code = False
    in_list = False
    prev_blank = True  # start of document behaves like "after a blank line"

    for line in content.splitlines(keepends=True):
        start = offset
        end = offset + len(line)
        offset = end

        line_content = line.rstrip("\n").rstrip("\r")
        stripped = line_content.strip()
        is_blank = stripped == ""

        if in_fence:
            ranges.append((start, end))
            m = _FENCE_PATTERN.match(line_content)
            if m and m.group(2)[0] == fence_char and len(m.group(2)) >= fence_len and stripped == m.group(2):
                in_fence = False
            prev_blank = is_blank
            continue

        m = _FENCE_PATTERN.match(line_content)
        if m:
            in_fence = True
            in_indent_code = False
            fence_char = m.group(2)[0]
            fence_len = len(m.group(2))
            ranges.append((start, end))
            prev_blank = is_blank
            continue

        indent_width = 0
        for ch in line_content:
            if ch == " ":
                indent_width += 1
            elif ch == "\t":
                indent_width += 4
            else:
                break

        # Track list scope: a list item opens a list; it stays open across
        # blank lines and indented continuation, and closes when a non-blank,
        # non-list line returns to the left margin.
        if _LIST_ITEM_PATTERN.match(line_content):
            in_list = True
        elif in_list and not is_blank and indent_width == 0:
            in_list = False

        if in_indent_code:
            if is_blank or indent_width >= 4:
                ranges.append((start, end))
                prev_blank = is_blank
                continue
            in_indent_code = False
        elif not in_list and not is_blank and indent_width >= 4 and prev_blank:
            in_indent_code = True
            ranges.append((start, end))
            prev_blank = is_blank
            continue

        for s, e in _inline_code_ranges(line_content):
            ranges.append((start + s, start + e))

        prev_blank = is_blank

    return ranges


async def rewrite_image_uris(
    root_uri: str,
    ctx: Optional[RequestContext] = None,
    lock_handle: Optional["LockHandle"] = None,
) -> Dict[str, int]:
    """Rewrite local image references in markdown files to viking:// URIs.

    After ``persist_temp_tree`` copies content to the final VikingFS location,
    this function scans all ``.md`` files under *root_uri* for image references
    pointing to local paths.  For each one, it looks for a corresponding file
    in ``{root_uri}/.images/`` and replaces the path with the full viking:// URI.

    Args:
        root_uri: The final VikingFS root URI (e.g. ``viking://resources/doc``)
        ctx: Optional request context for permissions
        lock_handle: Optional lock handle held by the caller. When the caller
            already owns a TREE lock over *root_uri*, forwarding it lets the
            cleanup ``rm`` reuse that lock instead of conflicting with it.

    Returns:
        Dict with ``files_processed`` and ``references_rewritten`` counts.
    """
    viking_fs = get_viking_fs()

    root_prefix = root_uri.rstrip("/")

    # Find all .md files recursively
    glob_result = await viking_fs.glob("*.md", uri=root_uri, ctx=ctx)
    md_uris = glob_result.get("matches", [])

    if not md_uris:
        return {"files_processed": 0, "references_rewritten": 0}

    # Load mapping file from _ingest_local_images:
    #   {rel_md_path -> {original_path_str -> image_filename}}
    # The mapping file lives at the root directory and images are stored next to
    # their referencing markdown file.
    file_mappings: Dict[str, Dict[str, str]] = {}
    try:
        mapping_content = await viking_fs.read_file(f"{root_prefix}/.image_mappings.json", ctx=ctx)
        file_mappings = json.loads(mapping_content)
    except Exception:
        pass

    files_processed = 0
    references_rewritten = 0

    for md_uri in md_uris:
        # Resolve this markdown file's mapping and its containing directory
        rel_md_path = md_uri[len(root_prefix) + 1 :] if md_uri.startswith(root_prefix) else md_uri
        path_to_image_name = file_mappings.get(rel_md_path, {})
        if not path_to_image_name:
            continue

        md_dir = md_uri.rsplit("/", 1)[0]

        # Build the set of available images that sit beside this markdown file
        available_images: Set[str] = set()
        try:
            entries = await viking_fs.ls(md_dir, ctx=ctx)
            available_images = {
                e["name"] for e in entries
                if not e.get("isDir") and not e["name"].startswith(".")
            }
        except Exception:
            logger.debug(f"[image_rewrite] Failed to list directory {md_dir}")

        try:
            content = await viking_fs.read_file(md_uri, ctx=ctx)
        except Exception:
            logger.warning(f"[image_rewrite] Failed to read {md_uri}, skipping")
            continue

        new_content, rewrite_count = _rewrite_content(content, md_dir, available_images, path_to_image_name)

        if rewrite_count > 0:
            try:
                await viking_fs.write_file(md_uri, new_content, ctx=ctx)
                files_processed += 1
                references_rewritten += rewrite_count
                logger.debug(
                    f"[image_rewrite] Rewrote {rewrite_count} image ref(s) in {md_uri}"
                )
            except Exception:
                logger.warning(f"[image_rewrite] Failed to write {md_uri}")

    # Clean up mapping file — no longer needed after rewrite
    if file_mappings:
        try:
            await viking_fs.rm(f"{root_prefix}/.image_mappings.json", ctx=ctx, lock_handle=lock_handle)
        except Exception as e:
            logger.warning(f"[image_rewrite] Failed to delete .image_mappings.json: {e}")

    logger.info(
        f"[image_rewrite] Processed {len(md_uris)} .md files, "
        f"rewrote {references_rewritten} image reference(s) in {files_processed} file(s)"
    )

    return {"files_processed": files_processed, "references_rewritten": references_rewritten}


def _rewrite_content(
    content: str,
    image_dir: str,
    available_images: Set[str],
    path_to_image_name: Optional[Dict[str, str]] = None,
) -> tuple[str, int]:
    """Rewrite local image references in markdown content.

    Returns (new_content, rewrite_count).
    """
    rewrite_count = 0
    mappings = path_to_image_name or {}

    protected = _protected_ranges(content)

    def _in_protected(pos: int) -> bool:
        for s, e in protected:
            if s <= pos < e:
                return True
        return False

    def replacer(match: re.Match) -> str:
        nonlocal rewrite_count
        alt_text = match.group(1)
        path = match.group(2)

        # Skip image references that live inside code blocks / inline code.
        if _in_protected(match.start()):
            return match.group(0)

        if _is_remote_uri(path):
            return match.group(0)

        # Prefer exact path mapping from .image_mappings.json
        if path in mappings:
            image_name = mappings[path]
            if image_name in available_images:
                rewrite_count += 1
                return f"![{alt_text}]({image_dir}/{image_name})"

        logger.warning(
            f"[image_rewrite] Image not found in VikingFS: path = {path}, "
            f"image_dir = {image_dir}, leaving reference unchanged"
        )
        return match.group(0)

    new_content = _IMAGE_PATTERN.sub(replacer, content)
    return new_content, rewrite_count
