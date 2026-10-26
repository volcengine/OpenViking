# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Patch handler for memory updates.

Supports two modes:
1. Content patch: structured SEARCH/REPLACE and DELETE blocks
2. Field patch: Field-level updates based on merge_op

Enhanced features from RooCode:
- Support for multiple SEARCH/REPLACE and DELETE blocks
- Fuzzy matching (fuzzy matching)
- Line number handling (add, strip, detect)
- Marker escaping support
- Aggressive line number stripping fallback
- Detailed validation and error messages
- Indentation preservation
- Levenshtein distance similarity calculation
"""

import re
from functools import lru_cache
from typing import Any, Dict, List

from rapidfuzz.distance import Levenshtein

from openviking.session.memory.merge_op.base import DeleteBlock, StrPatch
from openviking.session.memory.utils.line_numbers import (
    add_line_numbers,
    every_line_has_line_numbers,
    extract_start_line_number,
    strip_line_numbers,
)


class PatchParseError(Exception):
    """Error parsing patch content."""

    pass


# ============================================================================
# Core Algorithm Functions (from RooCode)
# ============================================================================


def normalize_string(text: str) -> str:
    """Normalize string by handling smart quotes and special characters."""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\u200e": "",
        "\u200f": "",
        "\ufeff": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def get_similarity(original: str, search: str) -> float:
    """Calculate similarity ratio between two strings (0 to 1)."""
    if search == "":
        return 0.0

    normalized_original = normalize_string(original)
    normalized_search = normalize_string(search)

    if normalized_original == normalized_search:
        return 1.0

    dist = Levenshtein.distance(normalized_original, normalized_search)
    max_length = max(len(normalized_original), len(normalized_search))

    return 1.0 - (dist / max_length) if max_length > 0 else 1.0


def fuzzy_search(
    lines: List[str], search_chunk: str, start_index: int, end_index: int
) -> Dict[str, Any]:
    """
    Perform a "middle-out" search to find the slice most similar to search_chunk.

    For single-line search, also checks for substring matches within each line.

    Returns dict with bestScore, bestMatchIndex, bestMatchContent
    """
    best_score = 0.0
    best_match_index = -1
    best_match_content = ""
    search_lines = search_chunk.split("\n")
    search_len = len(search_lines)

    mid_point = (start_index + end_index) // 2
    left_index = mid_point
    right_index = mid_point + 1

    # For single-line search, enable substring matching mode
    is_single_line = search_len == 1
    search_str = search_lines[0] if is_single_line else ""
    # Bound retained candidate text and discard it when this search returns.
    substring_match = lru_cache(maxsize=128)(_find_best_substring_match)
    chunk_similarity = lru_cache(maxsize=128)(get_similarity)

    while left_index >= start_index or right_index <= end_index - search_len:
        if left_index >= start_index:
            if is_single_line:
                # Check substring match in this single line
                line = lines[left_index]
                # First check for exact substring match
                if search_str in line:
                    best_score = 1.0
                    best_match_index = left_index
                    best_match_content = line
                    left_index -= 1
                    continue
                # If no exact match, try the best similarity with substrings
                line_score, line_content = substring_match(line, search_str)
                if line_score > best_score:
                    best_score = line_score
                    best_match_index = left_index
                    best_match_content = line_content
            else:
                # Original multi-line logic
                original_chunk = "\n".join(lines[left_index : left_index + search_len])
                similarity = chunk_similarity(original_chunk, search_chunk)
                if similarity > best_score:
                    best_score = similarity
                    best_match_index = left_index
                    best_match_content = original_chunk
            left_index -= 1

        if right_index <= end_index - search_len:
            if is_single_line:
                # Check substring match in this single line
                line = lines[right_index]
                # First check for exact substring match
                if search_str in line:
                    best_score = 1.0
                    best_match_index = right_index
                    best_match_content = line
                    right_index += 1
                    continue
                # If no exact match, try the best similarity with substrings
                line_score, line_content = substring_match(line, search_str)
                if line_score > best_score:
                    best_score = line_score
                    best_match_index = right_index
                    best_match_content = line_content
            else:
                # Original multi-line logic
                original_chunk = "\n".join(lines[right_index : right_index + search_len])
                similarity = chunk_similarity(original_chunk, search_chunk)
                if similarity > best_score:
                    best_score = similarity
                    best_match_index = right_index
                    best_match_content = original_chunk
            right_index += 1

    return {
        "bestScore": best_score,
        "bestMatchIndex": best_match_index,
        "bestMatchContent": best_match_content,
    }


def _find_best_substring_match(line: str, search_str: str) -> tuple[float, str]:
    """Find the best matching substring in a line."""
    best_score = 0.0
    best_content = ""
    search_len = len(search_str)
    line_len = len(line)

    # If search string is longer than line, just compare the whole line
    if search_len >= line_len:
        return get_similarity(line, search_str), line

    # Try sliding window for best match (limit to reasonable checks for performance)
    # First check at start, end, and a few positions in between
    positions_to_check = [0, line_len - search_len]
    if line_len > search_len * 3:
        positions_to_check.append(line_len // 2 - search_len // 2)

    for i in positions_to_check:
        if 0 <= i <= line_len - search_len:
            substring = line[i : i + search_len]
            score = get_similarity(substring, search_str)
            if score > best_score:
                best_score = score
                best_content = substring

    # Also compare with the whole line as fallback
    whole_line_score = get_similarity(line, search_str)
    if whole_line_score > best_score:
        best_score = whole_line_score
        best_content = line

    return best_score, best_content


# ============================================================================
# Marker Utilities (from RooCode)
# ============================================================================


def unescape_markers(content: str) -> str:
    """Unescape escaped markers in content."""
    return (
        content.replace(r"\<<<<<<<", "<<<<<<<")
        .replace(r"\=======", "=======")
        .replace(r"\>>>>>>>", ">>>>>>>")
        .replace(r"\-------", "-------")
        .replace(r"\:end_line:", ":end_line:")
        .replace(r"\:start_line:", ":start_line:")
    )


# ============================================================================
# Structured patch application
# ============================================================================


def _delete_complete_lines(content: str, delete_content: str) -> str:
    """Delete a unique block of complete lines, including one adjacent line ending."""
    start = content.index(delete_content)
    end = start + len(delete_content)

    starts_at_line_boundary = start == 0 or content[start - 1] == "\n"
    ends_at_line_boundary = (
        end == len(content) or content.startswith("\r\n", end) or content.startswith("\n", end)
    )
    if not starts_at_line_boundary or not ends_at_line_boundary:
        raise PatchParseError("DELETE content must contain one or more complete lines")

    # Prefer consuming the following line ending. At EOF, consume the preceding
    # line ending so deleting the final line does not leave a trailing blank line.
    if content.startswith("\r\n", end):
        end += 2
    elif content.startswith("\n", end):
        end += 1
    elif start > 0 and content[start - 2 : start] == "\r\n":
        start -= 2
    elif start > 0 and content[start - 1] == "\n":
        start -= 1

    return content[:start] + content[end:]


def apply_str_patch(original_content: str, patch: StrPatch) -> str:
    """Apply a StrPatch to original content.

    Args:
        original_content: Original string content
        patch: StrPatch model to apply

    Returns:
        Updated content after applying the patch
    """
    if not patch.blocks:
        return original_content

    # First try simple substring replacement - this handles the common case
    result_content = original_content
    all_applied = True

    for block in patch.blocks:
        search_content = unescape_markers(block.search)
        replace_content = unescape_markers(block.replace)

        if search_content == replace_content:
            continue

        if not search_content:
            all_applied = False
            break

        if search_content not in result_content:
            all_applied = False
            break

        match_count = result_content.count(search_content)
        if match_count > 1:
            raise PatchParseError(
                f"SEARCH content matched {match_count} locations; "
                "The re-generated SEARCH string may contain additional lines "
                "to make sure it is unique."
            )

        if isinstance(block, DeleteBlock):
            result_content = _delete_complete_lines(result_content, search_content)
        else:
            result_content = result_content.replace(search_content, replace_content, 1)

    if all_applied:
        return result_content

    # Fall back to line-based approach for complex cases
    fuzzy_threshold = 0.8
    buffer_lines = 40

    # Convert StrPatch blocks to internal match format
    matches = []
    for block in patch.blocks:
        start_line = getattr(block, "start_line", None) or 0
        matches.append(
            {
                "startLine": start_line,
                "searchContent": block.search,
                "replaceContent": block.replace,
            }
        )

    if not matches:
        return original_content

    # Detect line ending from original content
    line_ending = "\r\n" if "\r\n" in original_content else "\n"
    result_lines = re.split(r"\r?\n", original_content)
    diff_results = []
    applied_count = 0

    # Sort replacements by start_line
    replacements = [
        {
            "startLine": int(m.get("startLine", 0)),
            "searchContent": m.get("searchContent", ""),
            "replaceContent": m.get("replaceContent", ""),
        }
        for m in matches
    ]
    replacements.sort(key=lambda x: x["startLine"])

    for replacement in replacements:
        search_content = replacement["searchContent"]
        replace_content = replacement["replaceContent"]
        start_line = replacement["startLine"]

        # Unescape markers
        search_content = unescape_markers(search_content)
        replace_content = unescape_markers(replace_content)

        # Strip line numbers if present
        has_all_line_numbers = (
            every_line_has_line_numbers(search_content)
            and every_line_has_line_numbers(replace_content)
        ) or (every_line_has_line_numbers(search_content) and replace_content.strip() == "")

        if has_all_line_numbers and start_line == 0:
            inferred_start_line = extract_start_line_number(search_content)
            if inferred_start_line is not None:
                start_line = inferred_start_line

        if has_all_line_numbers:
            search_content = strip_line_numbers(search_content)
            replace_content = strip_line_numbers(replace_content)

        # If search and replace are identical, treat as success (no changes needed)
        if search_content == replace_content:
            diff_results.append(
                {
                    "success": True,
                    "message": "Search and replace content are identical - no changes needed",
                }
            )
            continue

        # Split content into lines
        search_lines = [] if search_content == "" else search_content.split("\n")
        replace_lines = [] if replace_content == "" else replace_content.split("\n")

        # Validate search content is not empty
        if len(search_lines) == 0:
            diff_results.append(
                {
                    "success": False,
                    "error": (
                        "Empty search content is not allowed\n\n"
                        "Debug Info:\n"
                        "- Search content cannot be empty\n"
                        "- For insertions, provide a specific line using :start_line: "
                        "and include content to search for\n"
                        "- For example, match a single line to insert before/after it"
                    ),
                }
            )
            continue

        # Initialize search variables
        match_index = -1
        best_match_score = 0.0
        best_match_content = ""
        search_chunk = "\n".join(search_lines)

        # Determine search bounds
        search_start_index = 0
        search_end_index = len(result_lines)

        # Validate and handle line range if provided
        if start_line:
            exact_start_index = start_line - 1
            search_len = len(search_lines)
            exact_end_index = exact_start_index + search_len - 1

            # Try exact match first
            original_chunk = "\n".join(result_lines[exact_start_index : exact_end_index + 1])
            similarity = get_similarity(original_chunk, search_chunk)
            if similarity >= fuzzy_threshold:
                match_index = exact_start_index
                best_match_score = similarity
                best_match_content = original_chunk
            else:
                # Set bounds for buffered search
                search_start_index = max(0, start_line - (buffer_lines + 1))
                search_end_index = min(
                    len(result_lines), start_line + len(search_lines) + buffer_lines
                )

        # If no match found yet, try middle-out search within bounds
        if match_index == -1:
            fuzzy_result = fuzzy_search(
                result_lines, search_chunk, search_start_index, search_end_index
            )
            match_index = fuzzy_result["bestMatchIndex"]
            best_match_score = fuzzy_result["bestScore"]
            best_match_content = fuzzy_result["bestMatchContent"]

        # Try aggressive line number stripping as a fallback
        if match_index == -1 or best_match_score < fuzzy_threshold:
            aggressive_search_content = strip_line_numbers(search_content, aggressive=True)
            aggressive_replace_content = strip_line_numbers(replace_content, aggressive=True)

            aggressive_search_lines = (
                [] if aggressive_search_content == "" else aggressive_search_content.split("\n")
            )
            aggressive_search_chunk = "\n".join(aggressive_search_lines)

            # Try middle-out search again with aggressive stripped content
            fuzzy_result = fuzzy_search(
                result_lines, aggressive_search_chunk, search_start_index, search_end_index
            )
            if (
                fuzzy_result["bestMatchIndex"] != -1
                and fuzzy_result["bestScore"] >= fuzzy_threshold
            ):
                match_index = fuzzy_result["bestMatchIndex"]
                best_match_score = fuzzy_result["bestScore"]
                best_match_content = fuzzy_result["bestMatchContent"]
                # Replace with stripped versions
                search_content = aggressive_search_content
                replace_content = aggressive_replace_content
                search_lines = aggressive_search_lines
                replace_lines = [] if replace_content == "" else replace_content.split("\n")
            else:
                # No match found with either method
                if start_line:
                    end_line = start_line + len(search_lines) - 1
                    original_section = "\n\nOriginal Content:\n" + add_line_numbers(
                        "\n".join(
                            result_lines[
                                max(0, start_line - 1 - buffer_lines) : min(
                                    len(result_lines), end_line + buffer_lines
                                )
                            ]
                        ),
                        max(1, start_line - buffer_lines),
                    )
                else:
                    original_section = "\n\nOriginal Content:\n" + add_line_numbers(
                        "\n".join(result_lines)
                    )

                best_match_section = (
                    "\n\nBest Match Found:\n"
                    + add_line_numbers(best_match_content, match_index + 1)
                    if best_match_content
                    else "\n\nBest Match Found:\n(no match)"
                )

                line_range = f" at line: {start_line}" if start_line else ""

                diff_results.append(
                    {
                        "success": False,
                        "error": (
                            f"No sufficiently similar match found{line_range} "
                            f"({int(best_match_score * 100)}% similar, "
                            f"needs {int(fuzzy_threshold * 100)}%)\n\n"
                            "Debug Info:\n"
                            f"- Similarity Score: {int(best_match_score * 100)}%\n"
                            f"- Required Threshold: {int(fuzzy_threshold * 100)}%\n"
                            f"- Search Range: {f'starting at line {start_line}' if start_line else 'start to end'}\n"
                            "- Tried both standard and aggressive line number stripping\n"
                            "- Tip: Use read_file tool to get the latest content of the file before "
                            "attempting to use apply_diff tool again, as file content may have changed\n\n"
                            f"Search Content:\n{search_chunk}"
                            f"{best_match_section}"
                            f"{original_section}"
                        ),
                    }
                )
                continue

        # Get matched lines from original content
        matched_lines = result_lines[match_index : match_index + len(search_lines)]

        # Get exact indentation of each line
        original_indents = []
        for line in matched_lines:
            match = re.match(r"^[\t ]*", line)
            original_indents.append(match.group(0) if match else "")

        search_indents = []
        for line in search_lines:
            match = re.match(r"^[\t ]*", line)
            search_indents.append(match.group(0) if match else "")

        # Apply replacement while preserving exact indentation
        # For each replace line, use the corresponding original matched line's indent
        indented_replace_lines = []
        for i, line in enumerate(replace_lines):
            # Get indent from corresponding original matched line
            if i < len(original_indents):
                matched_indent = original_indents[i]
            else:
                matched_indent = original_indents[0] if original_indents else ""

            # Get indent from corresponding search line
            if i < len(search_indents):
                search_indent = search_indents[i]
            else:
                search_indent = search_indents[0] if search_indents else ""

            # Get indent from current replace line
            current_replace_match = re.match(r"^[\t ]*", line)
            current_replace_indent = current_replace_match.group(0) if current_replace_match else ""

            # Calculate relative indent level (how much deeper/shallower this line is compared to search)
            relative_level = len(current_replace_indent) - len(search_indent)

            # Apply same relative level to matched indent
            if relative_level >= 0:
                final_indent = matched_indent + current_replace_indent[len(search_indent) :]
            else:
                final_indent = matched_indent[: max(0, len(matched_indent) + relative_level)]

            # For empty lines, keep original indent with no content
            if line.strip() == "":
                indented_replace_lines.append(matched_indent)
            else:
                # Add line content (without its original indent, preserving internal whitespace)
                line_content = line.lstrip(" \t")
                indented_replace_lines.append(final_indent + line_content)

        # Construct final content
        before_match = result_lines[:match_index]
        after_match = result_lines[match_index + len(search_lines) :]
        result_lines = before_match + indented_replace_lines + after_match
        applied_count += 1

    final_content = line_ending.join(result_lines)

    # Check if all results are successful (including no-change cases)
    has_failures = any(not result.get("success", False) for result in diff_results)

    if applied_count == 0 and has_failures:
        raise PatchParseError(
            f"Patch application failed: search content not found in original, original_content={original_content}, patch={patch}"
        )

    return final_content
