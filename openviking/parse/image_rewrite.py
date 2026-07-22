# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Post-commit image URI rewriting for OpenViking.

Scans markdown files in VikingFS after source commit and rewrites local
image references to viking:// URIs, driven by the ``.image_mappings.json``
sidecars that ``_ingest_local_images`` writes at each document root (the
images themselves are stored next to the markdown file referencing them).
"""

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Set

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.storage.transaction.lock_handle import LockHandle

logger = get_logger(__name__)

_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(((?:[^()]|\([^()]*\))+)\)")
_REMOTE_PREFIXES = ("http://", "https://", "viking://", "data:", "ftp://")

# Sidecar written by MarkdownParser._ingest_local_images at each document root,
# consumed (and deleted) here. Shared so merge/sync code can recognize it.
IMAGE_MAPPINGS_FILENAME = ".image_mappings.json"

# HTML <img src="..."> embeds, common in markdown for sizing control. Shared
# with the parser so ingestion and rewriting see the same references.
HTML_IMG_PATTERN = re.compile(r"""(<img\s[^>]*?src=["'])([^"']+)(["'][^>]*>)""", re.IGNORECASE)
_FENCE_PATTERN = re.compile(r"^(\s{0,3})(`{3,}|~{3,})")
_LIST_ITEM_PATTERN = re.compile(r"^(\s{0,3})([-*+]|\d{1,9}[.)])(\s+)")


def build_artifact_image_mappings(root_dir: Path) -> Dict[str, Dict[str, str]]:
    """Build the sidecar mapping for an already-materialized parser artifact.

    Understanding API artifacts already place each image next to the markdown
    file that references it. Keep this helper deliberately narrower than
    MarkdownParser's local-image ingestion: it records only existing sibling
    files and never copies or renames artifact content.
    """
    root = root_dir.resolve()
    mappings: Dict[str, Dict[str, str]] = {}

    for md_path in root.rglob("*.md"):
        if not md_path.is_file():
            continue
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            logger.warning(f"[image_rewrite] Failed to read artifact markdown: {md_path}")
            continue

        protected = _protected_ranges(content)

        refs = [(match.start(), match.group(2)) for match in _IMAGE_PATTERN.finditer(content)]
        refs.extend((match.start(), match.group(2)) for match in HTML_IMG_PATTERN.finditer(content))

        file_mappings: Dict[str, str] = {}
        md_dir = md_path.parent.resolve()
        for pos, original_ref in refs:
            if any(start <= pos < end for start, end in protected):
                continue

            ref = original_ref.strip()
            if not ref or _is_remote_uri(ref):
                continue

            # Queries/fragments are not part of the local filesystem path, but
            # the exact original reference remains the mapping key used later.
            path_part = re.split(r"[?#]", ref, maxsplit=1)[0]
            ref_path = Path(path_part)
            if not path_part or ref_path.is_absolute():
                continue

            try:
                candidate = (md_dir / ref_path).resolve()
                candidate.relative_to(root)
            except (OSError, ValueError):
                continue

            # The existing sidecar contract stores only the final filename and
            # rewrite_image_uris resolves it beside the markdown file.
            if candidate.parent != md_dir or not candidate.is_file():
                continue

            file_mappings[original_ref] = candidate.name

        if file_mappings:
            mappings[md_path.relative_to(root).as_posix()] = file_mappings

    return mappings


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
            if (
                m
                and m.group(2)[0] == fence_char
                and len(m.group(2)) >= fence_len
                and stripped == m.group(2)
            ):
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


async def _discover_mappings(
    viking_fs,
    root_prefix: str,
    md_uris: list,
    ctx: Optional[RequestContext] = None,
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Locate every ``.image_mappings.json`` under *root_prefix*.

    ``_ingest_local_images`` writes one sidecar per document root, and all of a
    document's markdown files live under that root — so probing the ancestor
    directories of the markdown files finds every sidecar regardless of how
    deep the ingest placed the document (single-file ingest leaves it at the
    resource root; directory ingest nests it per document).

    Returns ``{mapping_dir: {rel_md_path: {original_path: image_filename}}}``
    where ``rel_md_path`` is relative to ``mapping_dir``.
    """
    candidates = set()
    for md_uri in md_uris:
        d = md_uri.rsplit("/", 1)[0]
        while d == root_prefix or d.startswith(root_prefix + "/"):
            candidates.add(d)
            if d == root_prefix:
                break
            d = d.rsplit("/", 1)[0]

    found: Dict[str, Dict[str, Dict[str, str]]] = {}
    for d in candidates:
        try:
            content = await viking_fs.read_file(f"{d}/{IMAGE_MAPPINGS_FILENAME}", ctx=ctx)
            found[d] = json.loads(content)
        except Exception:
            continue
    return found


async def rewrite_image_uris(
    root_uri: str,
    ctx: Optional[RequestContext] = None,
    lock_handle: Optional["LockHandle"] = None,
) -> Dict[str, int]:
    """Rewrite local image references in markdown files to viking:// URIs.

    After ``persist_temp_tree`` copies content to the final VikingFS location,
    this function scans all ``.md`` files under *root_uri* for image references
    recorded in the ``.image_mappings.json`` sidecars written by
    ``_ingest_local_images`` (one per document root, holding
    ``{rel_md_path -> {original_path -> image_filename}}``), and replaces each
    recorded path with the full viking:// URI of the image stored next to the
    referencing markdown file. Each sidecar is interpreted in the coordinate
    system of the directory holding it, so both single-file ingest (sidecar at
    the resource root) and directory ingest (sidecar per document subdirectory)
    are covered.

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
    glob_result = await viking_fs.glob("**/*.md", uri=root_uri, ctx=ctx)
    md_uris = glob_result.get("matches", [])

    if not md_uris:
        return {"files_processed": 0, "references_rewritten": 0}

    mappings_by_dir = await _discover_mappings(viking_fs, root_prefix, md_uris, ctx)

    files_processed = 0
    references_rewritten = 0

    for md_uri in md_uris:
        # Resolve this markdown file's mapping from the sidecar of the document
        # root containing it (the key is relative to that root).
        path_to_image_name: Dict[str, str] = {}
        for map_dir, file_mappings in mappings_by_dir.items():
            if md_uri.startswith(map_dir + "/"):
                entry = file_mappings.get(md_uri[len(map_dir) + 1 :])
                if entry:
                    path_to_image_name = entry
                    break
        if not path_to_image_name:
            continue

        md_dir = md_uri.rsplit("/", 1)[0]

        # Build the set of available images that sit beside this markdown file
        available_images: Set[str] = set()
        try:
            entries = await viking_fs.ls(md_dir, ctx=ctx)
            available_images = {
                e["name"] for e in entries if not e.get("isDir") and not e["name"].startswith(".")
            }
        except Exception:
            logger.debug(f"[image_rewrite] Failed to list directory {md_dir}")

        try:
            content = await viking_fs.read_file(md_uri, ctx=ctx)
        except Exception:
            logger.warning(f"[image_rewrite] Failed to read {md_uri}, skipping")
            continue

        new_content, rewrite_count = _rewrite_content(
            content, md_dir, available_images, path_to_image_name
        )

        if rewrite_count > 0:
            try:
                # TODO: This must be optimized once pathlock is pushed down into ragfs.
                await viking_fs.write_file(
                    md_uri,
                    new_content,
                    ctx=ctx,
                    lock_handle=lock_handle,
                )
                files_processed += 1
                references_rewritten += rewrite_count
                logger.debug(f"[image_rewrite] Rewrote {rewrite_count} image ref(s) in {md_uri}")
            except Exception:
                logger.warning(f"[image_rewrite] Failed to write {md_uri}")

    # Clean up mapping sidecars — no longer needed after rewrite
    for map_dir in mappings_by_dir:
        try:
            await viking_fs.rm(
                f"{map_dir}/{IMAGE_MAPPINGS_FILENAME}", ctx=ctx, lock_handle=lock_handle
            )
        except Exception as e:
            logger.warning(
                f"[image_rewrite] Failed to delete {map_dir}/{IMAGE_MAPPINGS_FILENAME}: {e}"
            )

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

    def _mapped_uri(path: str) -> Optional[str]:
        """viking:// URI for *path* if the mapping covers it, else None."""
        image_name = mappings.get(path)
        if image_name and image_name in available_images:
            return f"{image_dir}/{image_name}"
        logger.warning(
            f"[image_rewrite] Image not found in VikingFS: path = {path}, "
            f"image_dir = {image_dir}, leaving reference unchanged"
        )
        return None

    def replacer(match: re.Match) -> str:
        nonlocal rewrite_count
        alt_text = match.group(1)
        path = match.group(2)

        # Skip image references that live inside code blocks / inline code.
        if _in_protected(match.start()):
            return match.group(0)

        if _is_remote_uri(path):
            return match.group(0)

        uri = _mapped_uri(path)
        if uri is None:
            return match.group(0)
        rewrite_count += 1
        return f"![{alt_text}]({uri})"

    def img_tag_replacer(match: re.Match) -> str:
        nonlocal rewrite_count
        path = match.group(2)

        if _in_protected(match.start()):
            return match.group(0)

        if _is_remote_uri(path):
            return match.group(0)

        uri = _mapped_uri(path)
        if uri is None:
            return match.group(0)
        rewrite_count += 1
        return f"{match.group(1)}{uri}{match.group(3)}"

    new_content = _IMAGE_PATTERN.sub(replacer, content)
    # The markdown pass may have shifted offsets; recompute protected ranges
    # against the updated text before rewriting <img> tags.
    protected = _protected_ranges(new_content)
    new_content = HTML_IMG_PATTERN.sub(img_tag_replacer, new_content)
    return new_content, rewrite_count
