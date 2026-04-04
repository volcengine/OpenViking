# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Patch handler for memory updates.

Supports two modes:
1. Content patch: SEARCH/REPLACE format (enhanced with RooCode's multi-search-replace strategy)
2. Field patch: Field-level updates based on merge_op

Enhanced features from RooCode:
- Support for multiple SEARCH/REPLACE blocks
- Fuzzy matching (fuzzy matching)
- Line number handling (add, strip, detect)
- Marker escaping support
- Aggressive line number stripping fallback
- Detailed validation and error messages
- Indentation preservation
- Levenshtein distance similarity calculation
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.session.memory.merge_op.base import StrPatch
from openviking_cli.utils import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class PatchParseError(Exception):
    """Error parsing patch content."""

    pass


# ============================================================================
# Core Algorithm Functions (from RooCode)
# ============================================================================


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


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

    dist = levenshtein_distance(normalized_original, normalized_search)
    max_length = max(len(normalized_original), len(normalized_search))

    return 1.0 - (dist / max_length) if max_length > 0 else 1.0


def fuzzy_search(
    lines: List[str], search_chunk: str, start_index: int, end_index: int
) -> Dict[str, Any]:
    """
    Perform a "middle-out" search to find the slice most similar to search_chunk.

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

    while left_index >= start_index or right_index <= end_index - search_len:
        if left_index >= start_index:
            original_chunk = "\n".join(lines[left_index : left_index + search_len])
            similarity = get_similarity(original_chunk, search_chunk)
            if similarity > best_score:
                best_score = similarity
                best_match_index = left_index
                best_match_content = original_chunk
            left_index -= 1

        if right_index <= end_index - search_len:
            original_chunk = "\n".join(lines[right_index : right_index + search_len])
            similarity = get_similarity(original_chunk, search_chunk)
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


# ============================================================================
# Line Number Utilities (from RooCode)
# ============================================================================


def add_line_numbers(content: str, start_line: int = 1) -> str:
    """Add line numbers to content."""
    lines = content.split("\n")
    numbered_lines = [f"{start_line + i} | {line}" for i, line in enumerate(lines)]
    return "\n".join(numbered_lines)


def strip_line_numbers(content: str, aggressive: bool = False) -> str:
    """
    Strip line numbers from content.

    Args:
        content: Content with line numbers
        aggressive: If True, strip all line numbers regardless of format
    """
    if aggressive:
        # Aggressive: remove anything that looks like a line number at the start
        return re.sub(r"^\s*\d+\s*[|:]\s*", "", content, flags=re.MULTILINE)
    else:
        # Standard: only strip "N | " format
        return re.sub(r"^\d+\s*\|\s*", "", content, flags=re.MULTILINE)


def every_line_has_line_numbers(content: str) -> bool:
    """Check if every line in content has a line number."""
    lines = content.split("\n")
    if not lines:
        return False
    # Check if all lines match the pattern "N | " at the start
    return all(re.match(r"^\d+\s*\|\s*", line) for line in lines)


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
# Validation (from RooCode)
# ============================================================================


class State(Enum):
    START = 1
    AFTER_SEARCH = 2
    AFTER_SEPARATOR = 3


def validate_marker_sequencing(diff_content: str) -> Dict[str, Any]:
    """Validate the marker sequencing in diff content."""
    state = {"current": State.START, "line": 0}

    SEARCH_PATTERN = r"^<<<<<<< SEARCH>?$"
    SEP = "======="
    REPLACE = ">>>>>>> REPLACE"
    SEARCH_PREFIX = "<<<<<<<"
    REPLACE_PREFIX = ">>>>>>>"

    def report_merge_conflict_error(found: str, _expected: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                f"ERROR: Special marker '{found}' found in your diff content at line {state['line']}:\n"
                "\n"
                f"When removing merge conflict markers like '{found}' from files, you MUST escape them\n"
                "in your SEARCH section by prepending a backslash (\\) at the beginning of the line:\n"
                "\n"
                "CORRECT FORMAT:\n\n"
                "<<<<<<< SEARCH\n"
                "content before\n"
                f"\\{found}    <-- Note the backslash here in this example\n"
                "content after\n"
                "=======\n"
                "replacement content\n"
                ">>>>>>> REPLACE\n"
                "\n"
                "Without escaping, the system confuses your content with diff syntax markers.\n"
                "You may use multiple diff blocks in a single diff request, but ANY of ONLY the following "
                "separators that occur within SEARCH or REPLACE content must be escaped, as follows:\n"
                f"\\{SEARCH_PREFIX}\n"
                f"\\{SEP}\n"
                f"\\{REPLACE}\n"
            ),
        }

    def report_invalid_diff_error(found: str, expected: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                f"ERROR: Diff block is malformed: marker '{found}' found in your diff content at line {state['line']}. "
                f"Expected: {expected}\n"
                "\n"
                "CORRECT FORMAT:\n\n"
                "<<<<<<< SEARCH\n"
                ":start_line: (required) The line number of original content where the search block starts.\n"
                "-------\n"
                "[exact content to find including whitespace]\n"
                "=======\n"
                "[new content to replace with]\n"
                ">>>>>>> REPLACE\n"
            ),
        }

    def report_line_marker_in_replace_error(marker: str) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                f"ERROR: Invalid line marker '{marker}' found in REPLACE section at line {state['line']}\n"
                "\n"
                "Line markers (:start_line: and :end_line:) are only allowed in SEARCH sections.\n"
                "\n"
                "CORRECT FORMAT:\n"
                "<<<<<<< SEARCH\n"
                ":start_line:5\n"
                "content to find\n"
                "=======\n"
                "replacement content\n"
                ">>>>>>> REPLACE\n"
                "\n"
                "INCORRECT FORMAT:\n"
                "<<<<<<< SEARCH\n"
                "content to find\n"
                "=======\n"
                ":start_line:5    <-- Invalid location\n"
                "replacement content\n"
                ">>>>>>> REPLACE\n"
            ),
        }

    lines = diff_content.split("\n")
    search_count = sum(1 for l in lines if re.match(SEARCH_PATTERN, l.strip()))
    sep_count = sum(1 for l in lines if l.strip() == SEP)
    replace_count = sum(1 for l in lines if l.strip() == REPLACE)

    likely_bad_structure = search_count != replace_count or sep_count < search_count

    for line in diff_content.split("\n"):
        state["line"] += 1
        marker = line.strip()

        # Check for line markers in REPLACE sections (but allow escaped ones)
        if state["current"] == State.AFTER_SEPARATOR:
            if marker.startswith(":start_line:") and not line.strip().startswith(r"\:start_line:"):
                return report_line_marker_in_replace_error(":start_line:")
            if marker.startswith(":end_line:") and not line.strip().startswith(r"\:end_line:"):
                return report_line_marker_in_replace_error(":end_line:")

        if state["current"] == State.START:
            if marker == SEP:
                return (
                    report_invalid_diff_error(SEP, "SEARCH")
                    if likely_bad_structure
                    else report_merge_conflict_error(SEP, "SEARCH")
                )
            if marker == REPLACE:
                return report_invalid_diff_error(REPLACE, "SEARCH")
            if marker.startswith(REPLACE_PREFIX):
                return report_merge_conflict_error(marker, "SEARCH")
            if re.match(SEARCH_PATTERN, marker):
                state["current"] = State.AFTER_SEARCH
            elif marker.startswith(SEARCH_PREFIX):
                return report_merge_conflict_error(marker, "SEARCH")

        elif state["current"] == State.AFTER_SEARCH:
            if re.match(SEARCH_PATTERN, marker):
                return report_invalid_diff_error("SEARCH", SEP)
            if marker.startswith(SEARCH_PREFIX):
                return report_merge_conflict_error(marker, "SEARCH")
            if marker == REPLACE:
                return report_invalid_diff_error(REPLACE, SEP)
            if marker.startswith(REPLACE_PREFIX):
                return report_merge_conflict_error(marker, "SEARCH")
            if marker == SEP:
                state["current"] = State.AFTER_SEPARATOR

        elif state["current"] == State.AFTER_SEPARATOR:
            if re.match(SEARCH_PATTERN, marker):
                return report_invalid_diff_error("SEARCH", REPLACE)
            if marker.startswith(SEARCH_PREFIX):
                return report_merge_conflict_error(marker, REPLACE)
            if marker == SEP:
                return (
                    report_invalid_diff_error(SEP, REPLACE)
                    if likely_bad_structure
                    else report_merge_conflict_error(SEP, REPLACE)
                )
            if marker == REPLACE:
                state["current"] = State.START
            elif marker.startswith(REPLACE_PREFIX):
                return report_merge_conflict_error(marker, REPLACE)

    if state["current"] == State.START:
        return {"success": True}
    else:
        expected = "=======" if state["current"] == State.AFTER_SEARCH else ">>>>>>> REPLACE"
        return {
            "success": False,
            "error": f"ERROR: Unexpected end of sequence: Expected '{expected}' was not found.",
        }


# ============================================================================
# Result Classes (from RooCode)
# ============================================================================


@dataclass
class DiffResult:
    """Result of applying a diff."""

    success: bool
    content: Optional[str] = None
    error: Optional[str] = None
    fail_parts: Optional[List[Dict]] = None


# ============================================================================
# Main Strategy Class (from RooCode)
# ============================================================================


class MultiSearchReplaceDiffStrategy:
    """Multi-Search-Replace diff strategy implementation."""

    def __init__(self, fuzzy_threshold: float = 1.0, buffer_lines: int = 40):
        """
        Initialize the strategy.

        Args:
            fuzzy_threshold: Similarity threshold for fuzzy matching (0.0 to 1.0)
            buffer_lines: Number of extra context lines to search around target
        """
        self.fuzzy_threshold = fuzzy_threshold
        self.buffer_lines = buffer_lines

    def apply_diff(
        self,
        original_content: str,
        diff_content: str,
        _param_start_line: Optional[int] = None,
        _param_end_line: Optional[int] = None,
    ) -> DiffResult:
        """
        Apply a multi-search-replace diff to the original content.

        Args:
            original_content: The original file content
            diff_content: The multi-search-replace diff content
            _param_start_line: (unused) Reserved for future use
            _param_end_line: (unused) Reserved for future use

        Returns:
            DiffResult with success status and modified content
        """
        # Validate marker sequencing
        valid_seq = validate_marker_sequencing(diff_content)
        if not valid_seq["success"]:
            return DiffResult(success=False, error=valid_seq["error"])

        # Parse diff blocks
        matches = self._parse_diff_blocks(diff_content)
        if not matches:
            return DiffResult(success=True, content=original_content)

        # Detect line ending from original content
        line_ending = "\r\n" if "\r\n" in original_content else "\n"
        result_lines = re.split(r"\r?\n", original_content)
        delta = 0
        diff_results = []
        applied_count = 0

        # Sort replacements by start_line
        replacements = [
            {
                "startLine": int(match.get("startLine", 0)),
                "searchContent": match.get("searchContent", ""),
                "replaceContent": match.get("replaceContent", ""),
            }
            for match in matches
        ]
        replacements.sort(key=lambda x: x["startLine"])

        for replacement in replacements:
            search_content = replacement["searchContent"]
            replace_content = replacement["replaceContent"]
            start_line = replacement["startLine"] + (
                replacement["startLine"] if replacement["startLine"] != 0 else 0
            )

            # Unescape markers
            search_content = unescape_markers(search_content)
            replace_content = unescape_markers(replace_content)

            # Strip line numbers if present
            has_all_line_numbers = (
                every_line_has_line_numbers(search_content)
                and every_line_has_line_numbers(replace_content)
            ) or (every_line_has_line_numbers(search_content) and replace_content.strip() == "")

            if has_all_line_numbers and start_line == 0:
                # Extract start line from first line
                first_line = search_content.split("\n")[0]
                if "|" in first_line:
                    start_line = int(first_line.split("|")[0].strip())

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

            end_line = replacement["startLine"] + len(search_lines) - 1

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
                if similarity >= self.fuzzy_threshold:
                    match_index = exact_start_index
                    best_match_score = similarity
                    best_match_content = original_chunk
                else:
                    # Set bounds for buffered search
                    search_start_index = max(0, start_line - (self.buffer_lines + 1))
                    search_end_index = min(
                        len(result_lines), start_line + len(search_lines) + self.buffer_lines
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
            if match_index == -1 or best_match_score < self.fuzzy_threshold:
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
                    and fuzzy_result["bestScore"] >= self.fuzzy_threshold
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
                    if start_line and end_line:
                        original_section = "\n\nOriginal Content:\n" + add_line_numbers(
                            "\n".join(
                                result_lines[
                                    max(0, start_line - 1 - self.buffer_lines) : min(
                                        len(result_lines), end_line + self.buffer_lines
                                    )
                                ]
                            ),
                            max(1, start_line - self.buffer_lines),
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
                                f"needs {int(self.fuzzy_threshold * 100)}%)\n\n"
                                "Debug Info:\n"
                                f"- Similarity Score: {int(best_match_score * 100)}%\n"
                                f"- Required Threshold: {int(self.fuzzy_threshold * 100)}%\n"
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
                current_replace_indent = (
                    current_replace_match.group(0) if current_replace_match else ""
                )

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
            delta = delta - len(matched_lines) + len(replace_lines)
            applied_count += 1

        final_content = line_ending.join(result_lines)

        # Check if all results are successful (including no-change cases)
        all_successful = all(result.get("success", False) for result in diff_results)
        has_failures = any(not result.get("success", False) for result in diff_results)

        if applied_count == 0 and has_failures:
            return DiffResult(success=False, fail_parts=diff_results)

        # If no changes were applied but all results are successful (e.g., search==replace),
        # return success with original content
        if applied_count == 0 and all_successful:
            return DiffResult(success=True, content=original_content, fail_parts=diff_results)

        return DiffResult(
            success=True, content=final_content, fail_parts=diff_results if diff_results else None
        )

    def _parse_diff_blocks(self, diff_content: str) -> List[Dict[str, Any]]:
        """
        Parse diff blocks from diff content.

        Supports two formats:
        1. v1 format (with :start_line: and)-------
        2. v2 format (without :start_line: and)-------

        Returns list of dicts with keys: startLine, searchContent, replaceContent
        """
        matches = []
        blocks = diff_content.split("<<<<<<< SEARCH")

        for block in blocks[1:]:
            if "=======" not in block or ">>>>>>> REPLACE" not in block:
                continue

            # Split by =======
            sep_parts = block.split("=======")
            if len(sep_parts) < 2:
                continue

            before_sep = sep_parts[0]
            after_sep = sep_parts[1]

            # Split before_sep by -------
            dash_parts = before_sep.split("-------")

            if len(dash_parts) >= 2:
                # v1 format: has -------
                header = dash_parts[0]
                search_content = (
                    dash_parts[1].lstrip("\n").rstrip("\n") if len(dash_parts) > 1 else ""
                )
            else:
                # v2 format: no -------, entire before_sep is header + search content
                # Extract :start_line: if present
                header = ""
                search_content = before_sep.lstrip("\n").rstrip("\n")

                # Check if first line is :start_line:
                lines = search_content.split("\n")
                if lines and lines[0].startswith(":start_line:"):
                    header = lines[0]
                    search_content = "\n".join(lines[1:])

            replace_content = after_sep.split(">>>>>>> REPLACE")[0].lstrip("\n").rstrip("\n")

            # Extract start_line from header
            start_line = 0
            for line in header.split("\n"):
                if line.startswith(":start_line:"):
                    try:
                        start_line = int(line.split(":")[1].strip())
                    except:
                        pass

            matches.append(
                {
                    "startLine": start_line,
                    "searchContent": search_content,
                    "replaceContent": replace_content,
                }
            )

        return matches


# ============================================================================
# MemoryPatchHandler
# ============================================================================


class MemoryPatchHandler:
    """Handler for applying patches to memory content."""

    # Patch format markers
    SEARCH_MARKER = "<<<<<<< SEARCH"
    SPLIT_MARKER = "======="
    REPLACE_MARKER = ">>>>>>> REPLACE"
    LINE_NUMBER_PREFIX = ":start_line:"

    def __init__(self, fuzzy_threshold: float = 1.0, buffer_lines: int = 40):
        """
        Initialize the patch handler.

        Args:
            fuzzy_threshold: Similarity threshold for fuzzy matching (0.0 to 1.0, default 1.0 = exact match)
            buffer_lines: Number of extra context lines to search (default 40)
        """
        self._strategy = MultiSearchReplaceDiffStrategy(
            fuzzy_threshold=fuzzy_threshold, buffer_lines=buffer_lines
        )

    def apply_content_patch(self, original_content: str, patch: str) -> str:
        """
        Apply SEARCH/REPLACE format patch to content.

        Patch format:
            <<<<<<< SEARCH
            :start_line:10
            -------
            Original content
            =======
            New content
            >>>>>>> REPLACE

        Supports multiple SEARCH/REPLACE blocks in a single patch.

        Args:
            original_content: Original content string
            patch: Patch string in SEARCH/REPLACE format

        Returns:
            Updated content

        Raises:
            PatchParseError: If patch format is invalid
        """
        if not patch or not patch.strip():
            return original_content

        # First validate marker sequencing - raise PatchParseError for invalid patches
        valid_seq = validate_marker_sequencing(patch)
        if not valid_seq["success"]:
            raise PatchParseError(valid_seq["error"])

        result = self._strategy.apply_diff(original_content, patch)

        if result.success:
            return result.content if result.content is not None else original_content
        else:
            # If failed but patch was valid (e.g., search content not found), fall back to appending
            error_msg = result.error or "Unknown error"
            logger.warning(f"Patch application failed, falling back to append: {error_msg}")
            return original_content + "\n" + self._extract_replace_content(patch)

    def _extract_replace_content(self, patch: str) -> str:
        """Extract replace content from patch for fallback append."""
        try:
            matches = self._strategy._parse_diff_blocks(patch)
            if matches:
                return matches[-1].get("replaceContent", "")
        except Exception:
            pass
        return patch


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

    # Directly convert StrPatch to internal format, skip string conversion
    strategy = MultiSearchReplaceDiffStrategy()

    # Convert StrPatch blocks to internal match format
    matches = []
    for block in patch.blocks:
        matches.append(
            {
                "startLine": block.start_line or 0,
                "searchContent": block.search,
                "replaceContent": block.replace,
            }
        )

    if not matches:
        return original_content

    # Apply using the same logic as apply_diff, but with pre-parsed matches
    # Detect line ending from original content
    line_ending = "\r\n" if "\r\n" in original_content else "\n"
    result_lines = re.split(r"\r?\n", original_content)
    diff_results = []
    applied_count = 0

    # Sort replacements by start_line
    replacements = [
        {
            "startLine": int(match.get("startLine", 0)),
            "searchContent": match.get("searchContent", ""),
            "replaceContent": match.get("replaceContent", ""),
        }
        for match in matches
    ]
    replacements.sort(key=lambda x: x["startLine"])

    for replacement in replacements:
        search_content = replacement["searchContent"]
        replace_content = replacement["replaceContent"]
        start_line = replacement["startLine"] + (
            replacement["startLine"] if replacement["startLine"] != 0 else 0
        )

        # Unescape markers
        search_content = unescape_markers(search_content)
        replace_content = unescape_markers(replace_content)

        # Strip line numbers if present
        has_all_line_numbers = (
            every_line_has_line_numbers(search_content)
            and every_line_has_line_numbers(replace_content)
        ) or (every_line_has_line_numbers(search_content) and replace_content.strip() == "")

        if has_all_line_numbers and start_line == 0:
            # Extract start line from first line
            first_line = search_content.split("\n")[0]
            if "|" in first_line:
                start_line = int(first_line.split("|")[0].strip())

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
            if similarity >= strategy.fuzzy_threshold:
                match_index = exact_start_index
                best_match_score = similarity
                best_match_content = original_chunk
            else:
                # Set bounds for buffered search
                search_start_index = max(0, start_line - (strategy.buffer_lines + 1))
                search_end_index = min(
                    len(result_lines), start_line + len(search_lines) + strategy.buffer_lines
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
        if match_index == -1 or best_match_score < strategy.fuzzy_threshold:
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
                and fuzzy_result["bestScore"] >= strategy.fuzzy_threshold
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
                                max(0, start_line - 1 - strategy.buffer_lines) : min(
                                    len(result_lines), end_line + strategy.buffer_lines
                                )
                            ]
                        ),
                        max(1, start_line - strategy.buffer_lines),
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
                            f"needs {int(strategy.fuzzy_threshold * 100)}%)\n\n"
                            "Debug Info:\n"
                            f"- Similarity Score: {int(best_match_score * 100)}%\n"
                            f"- Required Threshold: {int(strategy.fuzzy_threshold * 100)}%\n"
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
    all_successful = all(result.get("success", False) for result in diff_results)
    has_failures = any(not result.get("success", False) for result in diff_results)

    if applied_count == 0 and has_failures:
        # If failed, fall back to appending last replace content
        logger.warning("Patch application failed, falling back to append")
        last_replace = matches[-1].get("replaceContent", "") if matches else ""
        return original_content + "\n" + last_replace

    return final_content


# Import MergeOp here to avoid circular import
