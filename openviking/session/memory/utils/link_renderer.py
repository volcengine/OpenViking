import posixpath
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional
from urllib.parse import unquote

from openviking.core.namespace import uri_parts


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    start: int
    end: int
    text: str
    target: str


class LinkRenderer:
    """Renders and strips local markdown links in memory file content based on StoredLink metadata."""

    _LINK_START_RE = re.compile(r"\[(?P<text>[^\]]+)\]\(")
    _LINK_TITLE_SUFFIX_RE = re.compile(
        r"""\s+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\))\s*\Z""",
        re.DOTALL,
    )
    _BACKSLASH_ESCAPE_RE = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])")
    _MAX_LINK_PAREN_DEPTH = 32
    _MEMORY_FIELDS_RE = re.compile(r"(\n\n<!--\s*MEMORY_FIELDS\s*\n)", re.DOTALL)
    _CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
    _ASCII_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_]")

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(LinkRenderer._CJK_RE.search(text))

    @staticmethod
    def _is_ascii_word_char(char: str) -> bool:
        return bool(char and LinkRenderer._ASCII_WORD_CHAR_RE.fullmatch(char))

    @staticmethod
    def _parse_inline_markdown_link(
        content: str, destination_start: int
    ) -> Optional[tuple[str, int]]:
        """Return the destination and end offset for one inline Markdown link."""
        while destination_start < len(content) and content[destination_start].isspace():
            destination_start += 1

        position = destination_start
        depth = 0
        quote = ""
        angle_end: Optional[int] = None
        in_angle = position < len(content) and content[position] == "<"
        while position < len(content):
            char = content[position]
            if char == "\\" and position + 1 < len(content):
                position += 2
                continue
            if in_angle:
                if char == "\n" or (char == "<" and position != destination_start):
                    return None
                if char == ">":
                    in_angle = False
                    angle_end = position + 1
                position += 1
                continue
            if quote:
                if char == quote:
                    quote = ""
                position += 1
                continue
            if (
                depth == 0
                and char in {'"', "'"}
                and position > destination_start
                and content[position - 1].isspace()
            ):
                quote = char
            elif char == "(":
                depth += 1
                if depth > LinkRenderer._MAX_LINK_PAREN_DEPTH:
                    return None
            elif char == ")":
                if depth:
                    depth -= 1
                else:
                    raw_target = content[destination_start:position]
                    if angle_end is not None:
                        suffix = raw_target[angle_end - destination_start :]
                        if suffix.strip() and not LinkRenderer._LINK_TITLE_SUFFIX_RE.fullmatch(
                            suffix
                        ):
                            return None
                        target = raw_target[: angle_end - destination_start]
                    else:
                        stripped = raw_target.strip()
                        title = LinkRenderer._LINK_TITLE_SUFFIX_RE.search(stripped)
                        target = stripped[: title.start()].rstrip() if title else stripped
                    return target, position + 1
            position += 1
        return None

    @staticmethod
    def iter_markdown_links(content: str) -> Iterator[MarkdownLink]:
        """Yield parsed inline Markdown links with their source spans."""
        position = 0
        while opener := LinkRenderer._LINK_START_RE.search(content, position):
            parsed = LinkRenderer._parse_inline_markdown_link(content, opener.end())
            if parsed is None:
                position = opener.start() + 1
                continue
            target, end = parsed
            yield MarkdownLink(opener.start(), end, opener.group("text"), target)
            position = end

    @staticmethod
    def _replace_markdown_links(content: str, replacement: Callable[[MarkdownLink], str]) -> str:
        result: List[str] = []
        position = 0
        for link in LinkRenderer.iter_markdown_links(content):
            result.append(content[position : link.start])
            result.append(replacement(link))
            position = link.end
        result.append(content[position:])
        return "".join(result)

    @staticmethod
    def protected_markdown_spans(content: str) -> List[tuple[int, int]]:
        """Return spans where Compile must never insert a generated WikiLink."""
        spans = [(link.start, link.end) for link in LinkRenderer.iter_markdown_links(content)]
        spans.extend(
            (m.start(), m.end())
            for m in re.finditer(r"(?ms)^(?:```|~~~).*?^(?:```|~~~)[ \t]*$", content)
        )
        spans.extend((m.start(), m.end()) for m in re.finditer(r"`+[^\n`]*?`+", content))

        for match in re.finditer(r"(?m)^# Citations[ \t]*$", content):
            if not any(start <= match.start() < end for start, end in spans):
                spans.append((match.start(), len(content)))
                break
        return sorted(spans)

    @staticmethod
    def _find_match_span(
        content: str,
        match_text: str,
        protected_spans: Optional[List[tuple[int, int]]] = None,
    ) -> Optional[tuple[int, int]]:
        escaped = re.escape(match_text)
        # Character spans already covered by an existing [text](target) link. A match
        # inside one is already linked, so wrapping it again would produce a broken
        # nested link like "[[Frank](..) Ocean](..)"; skip those candidates.
        linked_spans = protected_spans or [
            (link.start, link.end) for link in LinkRenderer.iter_markdown_links(content)
        ]

        def _overlaps_protected_span(start: int, end: int) -> bool:
            return any(
                not (end <= span_start or start >= span_end)
                for span_start, span_end in linked_spans
            )

        if LinkRenderer._contains_cjk(match_text):
            for match in re.finditer(escaped, content):
                start, end = match.start(), match.end()
                if _overlaps_protected_span(start, end):
                    continue
                return start, end
            return None

        pattern = re.compile(escaped, re.IGNORECASE)
        for match in pattern.finditer(content):
            start, end = match.start(), match.end()
            if _overlaps_protected_span(start, end):
                continue
            left_char = content[start - 1] if start > 0 else ""
            right_char = content[end] if end < len(content) else ""
            if LinkRenderer._is_ascii_word_char(left_char) or LinkRenderer._is_ascii_word_char(
                right_char
            ):
                continue
            return start, end
        return None

    @staticmethod
    def normalize_markdown_target(target: str) -> str:
        """Decode and normalize a parsed Markdown link destination."""
        target = unquote(target.strip())
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        target = LinkRenderer._BACKSLASH_ESCAPE_RE.sub(r"\1", target)
        target = target.split("#", 1)[0]
        return target.rstrip("/") if "://" in target else posixpath.normpath(target)

    @staticmethod
    def can_render_link(
        content: str,
        match_text: str,
        source_uri: str,
        target_uri: str,
    ) -> bool:
        """Return whether rendering can insert or preserve the requested link."""
        protected_spans = LinkRenderer.protected_markdown_spans(content)
        if LinkRenderer._find_match_span(content, match_text, protected_spans) is not None:
            return True

        markdown_links = list(LinkRenderer.iter_markdown_links(content))
        link_spans = {(link.start, link.end) for link in markdown_links}
        non_link_protected = [span for span in protected_spans if span not in link_spans]
        relative_target = LinkRenderer.relative_path(source_uri, target_uri)
        expected_targets = {
            LinkRenderer.normalize_markdown_target(target_uri),
            LinkRenderer.normalize_markdown_target(
                relative_target if relative_target is not None else target_uri
            ),
        }

        for link in markdown_links:
            if link.start > 0 and content[link.start - 1] == "!":
                continue
            if any(
                not (link.end <= start or link.start >= end) for start, end in non_link_protected
            ):
                continue
            if LinkRenderer._find_match_span(link.text, match_text) is None:
                continue
            if LinkRenderer.normalize_markdown_target(link.target) in expected_targets:
                return True
        return False

    @staticmethod
    def render_links(content: str, source_uri: str, links: List[Dict]) -> str:
        """Replace match_text in content with relative markdown links.

        Args:
            content: Plain markdown body.
            source_uri: The viking:// URI of the file being written.
            links: List of link dicts (from links + backlinks in MEMORY_FIELDS).
        """
        rendered, _count = LinkRenderer.render_links_with_count(content, source_uri, links)
        return rendered

    @staticmethod
    def render_links_with_count(
        content: str, source_uri: str, links: List[Dict]
    ) -> tuple[str, int]:
        """Render links outside protected Markdown spans and return the inserted count."""
        eligible = [l for l in links if l.get("match_text")]
        if not eligible:
            return content, 0

        eligible.sort(key=lambda l: l.get("weight", 0), reverse=True)

        replacements: List[tuple] = []  # (start, end, replacement_text)
        protected_spans = LinkRenderer.protected_markdown_spans(content)
        for link in eligible:
            match_text = link["match_text"]
            to_uri = link["to_uri"]

            if to_uri == source_uri:
                continue

            rel = LinkRenderer.relative_path(source_uri, to_uri)
            link_target = rel if rel is not None else to_uri

            match_span = LinkRenderer._find_match_span(
                content, match_text, protected_spans=protected_spans
            )
            if not match_span:
                continue

            start, end = match_span
            # Skip if overlaps with an existing replacement
            if any(not (end <= rs or start >= re_) for rs, re_, _ in replacements):
                continue

            # Percent-encode spaces in the rendered target so the link is portable
            # across markdown renderers (e.g. `[Frank](entities/frank ocean.md)`
            # would otherwise be ambiguous). We accept the literal-space form when
            # matching existing links, but always emit the encoded form when
            # generating new ones.
            encoded_target = link_target.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            rendered = f"[{content[start:end]}]({encoded_target})"
            replacements.append((start, end, rendered))

        # Apply in reverse order to preserve indices
        result = list(content)
        for start, end, repl in sorted(replacements, key=lambda x: x[0], reverse=True):
            result[start:end] = list(repl)

        return "".join(result), len(replacements)

    @staticmethod
    def strip_links(content: str) -> str:
        """Remove relative markdown links, keeping only the link text.

        External links, viking:// links, anchor links, and absolute-path links are preserved.
        """

        def _replace_link(link: MarkdownLink) -> str:
            target = link.target
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            if target.startswith("#"):
                return content[link.start : link.end]
            if target.startswith("/"):
                return content[link.start : link.end]
            if "://" in target:
                return content[link.start : link.end]
            return link.text

        return LinkRenderer._replace_markdown_links(content, _replace_link)

    @staticmethod
    def strip_all_links(content: str) -> str:
        """Remove markdown links regardless of target scheme, keeping only link text."""

        return LinkRenderer._replace_markdown_links(content, lambda link: link.text)

    @staticmethod
    def relative_path(source_uri: str, target_uri: str) -> Optional[str]:
        """Compute a relative path from source_uri to target_uri in the viking:// namespace.

        Returns None if the URIs are in incompatible scopes (e.g. user vs agent).
        """
        src = uri_parts(source_uri)
        tgt = uri_parts(target_uri)

        if not src or not tgt:
            return None
        if src[0] != tgt[0]:
            return None
        if len(src) < 2 or len(tgt) < 2 or src[1] != tgt[1]:
            return None

        common = 0
        for s, t in zip(src, tgt, strict=False):
            if s == t:
                common += 1
            else:
                break

        if common < 1:
            return None

        # -1 because the last segment of source is a filename, not a directory
        up_count = len(src) - common - 1
        down_parts = tgt[common:]

        if up_count == 0:
            relative = "/".join(down_parts)
            if not relative:
                return ""
            # Make a sibling-file target visibly relative.  Descendant paths remain
            # unchanged (for example, ``events/today.md``), while a file in the
            # source file's own directory is rendered as ``./target.md``.
            return f"./{relative}" if len(down_parts) == 1 else relative

        up_parts = [".."] * up_count
        return "/".join(up_parts + list(down_parts))
