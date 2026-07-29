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
from typing import TYPE_CHECKING, Dict, Iterator, NamedTuple, Optional, Set

from openviking.server.identity import RequestContext
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    from openviking.storage.transaction.lock_handle import LockHandle

logger = get_logger(__name__)

_REMOTE_PREFIXES = ("http://", "https://", "viking://", "data:", "ftp://")

# Sidecar written by MarkdownParser._ingest_local_images at each document root,
# consumed (and deleted) here. Shared so merge/sync code can recognize it.
IMAGE_MAPPINGS_FILENAME = ".image_mappings.json"

# HTML <img src="..."> embeds, common in markdown for sizing control. Shared
# with the parser so ingestion and rewriting see the same references.
HTML_IMG_PATTERN = re.compile(r"""(<img\s[^>]*?src=["'])([^"']+)(["'][^>]*>)""", re.IGNORECASE)
_FENCE_PATTERN = re.compile(r"^(\s{0,3})(`{3,}|~{3,})")
_LIST_ITEM_PATTERN = re.compile(r"^(\s{0,3})([-*+]|\d{1,9}[.)])(\s+)")


class _MarkdownImageMatch(NamedTuple):
    start: int
    end: int
    alt_start: int
    alt_end: int
    payload_start: int
    payload_end: int
    title_start: int
    title_end: int


def _iter_unprotected_html_images(
    content: str,
    protected_ranges: Optional[list[tuple[int, int]]] = None,
) -> Iterator[re.Match]:
    """Yield HTML image matches outside sorted protected ranges in linear time."""
    protected = protected_ranges or []
    protected_index = 0
    for match in HTML_IMG_PATTERN.finditer(content):
        start = match.start()
        end = match.end()
        while protected_index < len(protected) and protected[protected_index][1] <= start:
            protected_index += 1
        if protected_index < len(protected) and protected[protected_index][0] < end:
            continue
        yield match


def _merge_sorted_ranges(
    first: list[tuple[int, int]],
    second: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Merge two source-ordered range streams in linear time."""
    merged: list[tuple[int, int]] = []
    first_index = 0
    second_index = 0
    while first_index < len(first) or second_index < len(second):
        if second_index >= len(second) or (
            first_index < len(first) and first[first_index][0] <= second[second_index][0]
        ):
            start, end = first[first_index]
            first_index += 1
        else:
            start, end = second[second_index]
            second_index += 1
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _next_position_by_level(
    starts: list[int],
    levels: Dict[int, int],
    positions_by_level: Dict[int, list[int]],
) -> Dict[int, int]:
    """Map each sorted start to the next position at its parenthesis level."""
    offsets: Dict[int, int] = {}
    result: Dict[int, int] = {}
    for start in starts:
        level = levels[start]
        positions = positions_by_level.get(level, [])
        offset = offsets.get(level, 0)
        while offset < len(positions) and positions[offset] < start:
            offset += 1
        offsets[level] = offset
        result[start] = positions[offset] if offset < len(positions) else -1
    return result


def _iter_markdown_images(
    content: str,
    protected_ranges: Optional[list[tuple[int, int]]] = None,
) -> Iterator[_MarkdownImageMatch]:
    """Yield Markdown image spans with linear preprocessing and bounded recovery."""
    length = len(content)
    protected = protected_ranges or []
    protected_index = 0
    candidate_starts: list[int] = []
    bracket_stack: list[int] = []
    bracket_matches: Dict[int, int] = {}
    backslashes = 0

    # Pair every unescaped bracket once. An unmatched outer image therefore
    # cannot consume a later independently balanced image candidate.
    for index, char in enumerate(content):
        while protected_index < len(protected) and protected[protected_index][1] <= index:
            protected_index += 1
        if (
            protected_index < len(protected)
            and protected[protected_index][0] <= index < protected[protected_index][1]
        ):
            backslashes = 0
            continue
        if char == "\\":
            backslashes += 1
            continue
        escaped = backslashes % 2 == 1
        backslashes = 0
        if not escaped and char == "!" and index + 1 < length and content[index + 1] == "[":
            candidate_starts.append(index)
        if escaped:
            continue
        if char == "[":
            bracket_stack.append(index)
        elif char == "]" and bracket_stack:
            bracket_matches[bracket_stack.pop()] = index

    candidate_bounds: Dict[int, tuple[int, int]] = {}
    payload_start_set: Set[int] = set()
    for start in candidate_starts:
        alt_end = bracket_matches.get(start + 1)
        if alt_end is None or alt_end + 1 >= length or content[alt_end + 1] != "(":
            continue
        payload_start = alt_end + 2
        candidate_bounds[start] = (alt_end, payload_start)
        payload_start_set.add(payload_start)

    # Images and titles cannot borrow delimiters across blank lines or protected
    # block-code regions. Inline code remains part of the surrounding block.
    block_boundaries: Set[int] = set()
    protected_index = 0
    offset = 0
    for line in content.splitlines(keepends=True):
        line_start = offset
        line_end = line_start + len(line)
        offset = line_end
        while protected_index < len(protected) and protected[protected_index][1] <= line_start:
            protected_index += 1
        if not line.strip():
            block_boundaries.add(line_end)
        if (
            protected_index < len(protected)
            and protected[protected_index][0] <= line_start
            and protected[protected_index][1] >= line_end
        ):
            block_boundaries.add(line_start)
            block_boundaries.add(line_end)
    block_ids = [0] * (length + 1)
    block_id = 0
    for position in range(length + 1):
        block_id += position in block_boundaries
        block_ids[position] = block_id

    payload_starts: list[int] = []
    payload_levels: Dict[int, int] = {}
    closes_by_level: Dict[int, list[int]] = {}
    title_openers: list[tuple[int, int, str]] = []
    next_same_quote: Dict[int, int] = {}
    quote_positions: list[int] = []
    last_quote: Dict[str, int] = {}
    parenthesis_stack: list[int] = []
    matching_parenthesis: Dict[int, int] = {}
    parenthesis_level = 0
    backslashes = 0
    protected_index = 0

    for index, char in enumerate(content):
        if index in payload_start_set:
            payload_starts.append(index)
            payload_levels[index] = parenthesis_level
        while protected_index < len(protected) and protected[protected_index][1] <= index:
            protected_index += 1
        if (
            protected_index < len(protected)
            and protected[protected_index][0] <= index < protected[protected_index][1]
        ):
            backslashes = 0
            continue
        if char == "\\":
            backslashes += 1
            continue
        escaped = backslashes % 2 == 1
        backslashes = 0
        if escaped:
            continue

        if char == "(":
            if index > 0 and content[index - 1].isspace():
                title_openers.append((index, parenthesis_level, char))
            parenthesis_stack.append(index)
            parenthesis_level += 1
        elif char == ")":
            closes_by_level.setdefault(parenthesis_level, []).append(index)
            if parenthesis_stack:
                matching_parenthesis[parenthesis_stack.pop()] = index
            parenthesis_level -= 1

        if char in {'"', "'"}:
            previous = last_quote.get(char)
            if previous is not None:
                next_same_quote[previous] = index
            last_quote[char] = index
            quote_positions.append(index)
            if index > 0 and content[index - 1].isspace():
                title_openers.append((index, parenthesis_level, char))

    if length in payload_start_set:
        payload_starts.append(length)
        payload_levels[length] = parenthesis_level

    next_close = _next_position_by_level(payload_starts, payload_levels, closes_by_level)
    terminal_marker_positions = set(quote_positions)
    terminal_marker_positions.update(matching_parenthesis.values())
    next_nonspace_after_marker: Dict[int, int] = {}
    following_nonspace = length
    for index in range(length - 1, -1, -1):
        if index in terminal_marker_positions:
            next_nonspace_after_marker[index] = following_nonspace
        if not content[index].isspace():
            following_nonspace = index

    terminal_titles_by_level: Dict[int, list[int]] = {}
    title_bounds: Dict[int, tuple[int, int]] = {}
    for position, level, marker in title_openers:
        title_end = (
            matching_parenthesis.get(position, -1)
            if marker == "("
            else next_same_quote.get(position, -1)
        )
        if title_end < 0:
            continue
        close = next_nonspace_after_marker[title_end]
        if close < length and content[close] == ")":
            terminal_titles_by_level.setdefault(level, []).append(position)
            title_bounds[position] = (position + 1, title_end)
    next_title_opener = _next_position_by_level(
        payload_starts,
        payload_levels,
        terminal_titles_by_level,
    )

    raw_matches: Dict[int, tuple[int, int, int, int]] = {}
    for start in candidate_starts:
        if start not in candidate_bounds:
            continue
        alt_end, payload_start = candidate_bounds[start]
        close = next_close[payload_start]
        title_opener = next_title_opener[payload_start]
        title_start = -1
        title_end = -1

        if title_opener >= 0 and (close < 0 or title_opener < close):
            title_start, title_end = title_bounds[title_opener]
            close = next_nonspace_after_marker[title_end]
            if close >= length or content[close] != ")":
                continue
        elif close <= payload_start:
            continue

        if block_ids[start] != block_ids[close]:
            continue
        raw_matches[start] = (alt_end, close, title_start, title_end)

    suppressed: Set[int] = set()

    # A syntactically confirmed title owns all text that starts in its interior.
    # Never let mapping or filesystem success reinterpret title text as an image.
    title_events_at: Dict[int, list[tuple[int, int]]] = {}
    for owner, (_alt_end, _close, title_start, title_end) in raw_matches.items():
        if title_start >= 0:
            title_events_at.setdefault(title_start, []).append((title_end, owner))
    title_events = [
        (position, title_end, owner)
        for position in range(length)
        for title_end, owner in title_events_at.get(position, ())
    ]
    title_event_index = 0
    furthest_title_end = -1
    for start in candidate_starts:
        raw_match = raw_matches.get(start)
        if raw_match is None:
            continue
        while title_event_index < len(title_events) and title_events[title_event_index][0] <= start:
            title_start, title_end, owner = title_events[title_event_index]
            title_event_index += 1
            if owner not in suppressed:
                furthest_title_end = max(furthest_title_end, title_end)
        if start < furthest_title_end:
            suppressed.add(start)

    selected_end = -1
    for start in candidate_starts:
        if start not in raw_matches or start in suppressed:
            continue
        alt_end, close, title_start, title_end = raw_matches[start]
        if start < selected_end:
            continue
        selected_end = close + 1
        payload_start = candidate_bounds[start][1]
        yield _MarkdownImageMatch(
            start,
            close + 1,
            start + 2,
            alt_end,
            payload_start,
            close,
            title_start,
            title_end,
        )


def _owned_image_matches(
    content: str,
    protected: list[tuple[int, int]],
) -> tuple[list[_MarkdownImageMatch], list[re.Match]]:
    """Return non-overlapping Markdown and HTML images with outer syntax owning."""
    html_candidates = list(_iter_unprotected_html_images(content, protected))
    markdown_protected = _merge_sorted_ranges(
        protected,
        [(match.start(), match.end()) for match in html_candidates],
    )
    markdown_matches = list(_iter_markdown_images(content, markdown_protected))
    html_excluded = _merge_sorted_ranges(
        protected,
        [(match.start, match.end) for match in markdown_matches],
    )
    html_matches = list(_iter_unprotected_html_images(content, html_excluded))
    return markdown_matches, html_matches


def _split_markdown_image_target(payload: str) -> tuple[str, str]:
    """Return ``(destination, exact_title_suffix)`` for one image payload.

    This scans once from the end instead of making the image regex choose an
    ambiguous whitespace boundary. Callers can still prefer the full payload
    when it names an existing file or sidecar key.
    """
    content_end = len(payload)
    while content_end > 0 and payload[content_end - 1].isspace():
        content_end -= 1
    if content_end == 0:
        return payload, ""

    closing = payload[content_end - 1]
    if closing not in {'"', "'", ")"}:
        return payload, ""

    slash = content_end - 2
    while slash >= 0 and payload[slash] == "\\":
        slash -= 1
    if (content_end - slash - 2) % 2:
        return payload, ""

    opening = -1
    if closing in {'"', "'"}:
        index = content_end - 2
        while index >= 0:
            if payload[index] != closing:
                index -= 1
                continue
            slash = index - 1
            while slash >= 0 and payload[slash] == "\\":
                slash -= 1
            if (index - slash - 1) % 2 == 0:
                opening = index
                break
            index = slash
    else:
        depth = 1
        index = content_end - 2
        while index >= 0:
            char = payload[index]
            if char not in "()":
                index -= 1
                continue
            slash = index - 1
            while slash >= 0 and payload[slash] == "\\":
                slash -= 1
            if (index - slash - 1) % 2:
                index = slash
                continue
            if char == ")":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    opening = index
                    break
            index = slash

    if opening <= 0:
        return payload, ""

    separator_start = opening
    while separator_start > 0 and payload[separator_start - 1].isspace():
        separator_start -= 1
    if separator_start == opening or not payload[:separator_start].strip():
        return payload, ""

    return payload[:separator_start], payload[separator_start:]


def _artifact_image_candidate(
    image_ref: str,
    md_dir: Path,
    root: Path,
    *,
    strip_suffix: bool = True,
) -> Optional[Path]:
    # Query/fragment suffixes are reference syntax, not literal filenames. The
    # title-ambiguity caller opts out for its compatibility-first exact probe.
    ref = image_ref.strip()
    if not ref or _is_remote_uri(ref):
        return None

    path_part = re.split(r"[?#]", ref, maxsplit=1)[0] if strip_suffix else ref
    ref_path = Path(path_part)
    if not path_part or ref_path.is_absolute():
        return None

    try:
        candidate = (md_dir / ref_path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if candidate.parent != md_dir or not candidate.is_file():
        return None
    return candidate


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

        markdown_matches, html_matches = _owned_image_matches(content, protected)
        refs = [(match.start, match.end, match, True) for match in markdown_matches]
        refs.extend((match.start(), match.end(), match.group(2), False) for match in html_matches)

        file_mappings: Dict[str, str] = {}
        md_dir = md_path.parent.resolve()

        for _start, _end, reference, is_markdown in refs:
            original_ref = (
                content[reference.payload_start : reference.payload_end]
                if is_markdown
                else reference
            )
            destination, title = (
                _split_markdown_image_target(original_ref) if is_markdown else (original_ref, "")
            )
            mapping_ref = original_ref
            if title:
                # Compatibility first: an existing literal filename such as
                # ``photo.png (copy)`` remains a path, not a title-bearing ref.
                candidate = _artifact_image_candidate(
                    original_ref, md_dir, root, strip_suffix=False
                )
                if candidate is None:
                    candidate = _artifact_image_candidate(destination, md_dir, root)
                    if candidate is not None:
                        mapping_ref = destination
            else:
                candidate = _artifact_image_candidate(original_ref, md_dir, root)
            if candidate is None:
                continue

            # The existing sidecar contract stores only the final filename and
            # rewrite_image_uris resolves it beside the markdown file.
            file_mappings[mapping_ref] = candidate.name

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

    if not ranges:
        return []

    merged = []
    # Fenced/indented lines and inline spans are appended in source order.
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _span_intersects_protected_ranges(
    start: int,
    end: int,
    ranges: list[tuple[int, int]],
) -> bool:
    """Return whether ``[start, end)`` overlaps sorted, disjoint ``ranges``."""
    low = 0
    high = len(ranges)
    while low < high:
        middle = (low + high) // 2
        if ranges[middle][1] <= start:
            low = middle + 1
        else:
            high = middle
    return low < len(ranges) and ranges[low][0] < end


def _position_in_protected_ranges(
    position: int,
    ranges: list[tuple[int, int]],
) -> bool:
    """Return whether one source position is inside a protected Markdown span."""
    return _span_intersects_protected_ranges(position, position + 1, ranges)


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

    def _mapped_uri(path: str) -> Optional[str]:
        """viking:// URI for *path* if the mapping covers it, else None."""
        image_name = mappings.get(path)
        if image_name is None:
            return None
        if image_name in available_images:
            return f"{image_dir}/{image_name}"
        logger.warning(
            f"[image_rewrite] Image not found in VikingFS: path = {path}, "
            f"image_dir = {image_dir}, leaving reference unchanged"
        )
        return None

    def replace_markdown_image(match: _MarkdownImageMatch) -> Optional[str]:
        nonlocal rewrite_count
        payload = content[match.payload_start : match.payload_end]
        path = payload
        title = ""
        # The sidecar is the provenance boundary. ``available_images`` only
        # validates mapped targets; it cannot safely infer an original ref.
        if payload not in mappings:
            destination, title_suffix = _split_markdown_image_target(payload)
            if title_suffix:
                path = destination
                title = title_suffix

        if _is_remote_uri(path):
            return None

        uri = _mapped_uri(path)
        if uri is None:
            return None
        alt_text = content[match.alt_start : match.alt_end]
        rewrite_count += 1
        return f"![{alt_text}]({uri}{title})"

    def replace_html_image(match: re.Match) -> Optional[str]:
        nonlocal rewrite_count
        path = match.group(2)

        if _is_remote_uri(path):
            return None

        uri = _mapped_uri(path)
        if uri is None:
            return None
        rewrite_count += 1
        return f"{match.group(1)}{uri}{match.group(3)}"

    parts = []
    cursor = 0
    markdown_matches, _ = _owned_image_matches(content, protected)
    for match in markdown_matches:
        replacement = replace_markdown_image(match)
        if replacement is None:
            continue
        parts.append(content[cursor : match.start])
        parts.append(replacement)
        cursor = match.end
    parts.append(content[cursor:])
    new_content = "".join(parts)
    # The markdown pass may have shifted offsets; recompute protected ranges
    # against the updated text before rewriting <img> tags.
    protected = _protected_ranges(new_content)
    _, html_matches = _owned_image_matches(new_content, protected)
    parts = []
    cursor = 0
    for match in html_matches:
        replacement = replace_html_image(match)
        if replacement is None:
            continue
        parts.append(new_content[cursor : match.start()])
        parts.append(replacement)
        cursor = match.end()
    parts.append(new_content[cursor:])
    return "".join(parts), rewrite_count
