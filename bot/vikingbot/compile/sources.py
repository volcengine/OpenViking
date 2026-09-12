"""Lossless source ranges prepared in memory for bounded compile assignments."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Iterable

from vikingbot.compile.models import CompileLimits

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+\S")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n(?:---|\.\.\.)(?:\r?\n|$)", re.DOTALL)
_TABLE_SEPARATOR = re.compile(r"\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*")


@dataclass(frozen=True)
class CompileSourceRange:
    """An exact excerpt of one original source, with separately repeated reading context.

    ``start_char``/``end_char`` are zero-based, half-open Unicode character offsets;
    ``start_line``/``end_line`` are inclusive, one-based line numbers. ``content``
    preserves the original text, including line endings. ``context`` repeats source
    frontmatter, enclosing headings and a table header when they fit the repeat budget.
    Continuation flags identify cuts inside an oversized line or table row;
    these fragments require their adjacent ranges for complete interpretation.
    """

    uri: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    content: str
    context: str = ""
    continues_before: bool = False
    continues_after: bool = False

    @property
    def input_chars(self) -> int:
        """Count source text and repeated context against the batch character budget."""
        return len(self.content) + len(self.context)


def split_source(uri: str, content: str, max_chars: int) -> list[CompileSourceRange]:
    """Partition original text by characters, preferring headings, paragraphs and whole lines.

    Complete table rows fit together with their header whenever the budget permits.
    An oversized line uses explicitly marked character continuations. Repeated
    frontmatter/headings/table headers use at most one quarter of the budget; their
    original text remains in the source ranges. Empty files retain one empty range.
    """
    if max_chars < 1:
        raise ValueError("Source character budget must be positive")
    lines = content.splitlines(keepends=True)
    offsets = [0]
    contexts = []
    preferred = [0]
    headings = []
    table_header = ""
    match = _FRONTMATTER.match(content)
    frontmatter = match[0] if match else ""
    fence = ""
    for i, line in enumerate(lines):
        start = offsets[-1]
        offsets.append(start + len(line))
        marker = _FENCE.match(line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
        if not fence and start >= len(frontmatter):
            heading = _HEADING.match(line)
            if heading:
                depth = len(heading[1])
                headings = [(level, text) for level, text in headings if level < depth]
                headings.append((depth, line.rstrip()))
                preferred.append(start)
            if _TABLE_SEPARATOR.fullmatch(line) and i:
                table_header = lines[i - 1] + line
            elif not line.lstrip().startswith("|"):
                table_header = ""
            if not line.strip():
                preferred.append(offsets[-1])
        contexts.append(
            frontmatter
            + "\n".join(text for _, text in headings)
            + ("\n" + table_header if table_header else "")
        )

    def context_at(offset: int) -> str:
        """Return enclosing source context when it fits the repeat budget."""
        if not offset or not contexts:
            return ""
        value = contexts[min(bisect_right(offsets, offset) - 1, len(contexts) - 1)]
        return value if len(value) <= max_chars // 4 else ""

    ranges = []
    start = 0
    while start < len(content) or not ranges:
        context = context_at(start)
        target = min(len(content), start + max_chars - len(context))
        end = target
        if target < len(content):
            boundary = preferred[bisect_right(preferred, target) - 1]
            line_index = bisect_right(offsets, target) - 1
            line_end = offsets[line_index]
            if boundary > start and boundary >= (start + target) // 2:
                end = boundary
            elif line_end > start:
                next_line_size = offsets[line_index + 1] - line_end
                if line_end >= (start + target) // 2 or next_line_size <= max_chars - len(
                    context_at(line_end)
                ):
                    end = line_end
        start_line = bisect_right(offsets, start)
        end_line = bisect_right(offsets, max(start, end - 1))
        ranges.append(
            CompileSourceRange(
                uri=uri,
                start_char=start,
                end_char=end,
                start_line=start_line,
                end_line=end_line,
                content=content[start:end],
                context=context,
                continues_before=start != offsets[start_line - 1],
                continues_after=end < len(content)
                and end != offsets[bisect_right(offsets, end) - 1],
            )
        )
        start = end
    return ranges


def pack_source_batches(
    sources: Iterable[tuple[str, str]], limits: CompileLimits
) -> list[list[CompileSourceRange]]:
    """Pack exact source ranges in input order, bounded only by characters.

    Adjacent ranges may share a batch when they fit. Small files share remaining
    capacity; original URIs remain the citation targets for all ranges. Source text
    and repeated context both count toward ``source_batch_chars``.
    """
    batches: list[list[CompileSourceRange]] = []
    batch: list[CompileSourceRange] = []
    size = 0
    for uri, content in sources:
        for part in split_source(uri, content, limits.source_batch_chars):
            if batch and size + part.input_chars > limits.source_batch_chars:
                batches.append(batch)
                batch, size = [], 0
            batch.append(part)
            size += part.input_chars
    if batch:
        batches.append(batch)
    return batches
