import re
from typing import Dict, List, Optional

from openviking.core.namespace import uri_parts


class LinkRenderer:
    """Renders and strips local markdown links in memory file content based on StoredLink metadata."""

    # Target may contain spaces (e.g. `[Frank Ocean](entities/frank ocean.md)`).
    # Markdown permits literal spaces in destinations, though they are not portable
    # across renderers; `render_links` therefore percent-encodes spaces in generated
    # targets so they round-trip cleanly. We accept both forms when matching.
    _RELATIVE_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)]+)\)")
    _ATX_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+.*$")
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
    def _match_spans(content: str, match_text: str) -> List[tuple[int, int]]:
        escaped = re.escape(match_text)
        if LinkRenderer._contains_cjk(match_text):
            return [(match.start(), match.end()) for match in re.finditer(escaped, content)]

        pattern = re.compile(escaped, re.IGNORECASE)
        spans = []
        for match in pattern.finditer(content):
            start, end = match.start(), match.end()
            left_char = content[start - 1] if start > 0 else ""
            right_char = content[end] if end < len(content) else ""
            if LinkRenderer._is_ascii_word_char(left_char) or LinkRenderer._is_ascii_word_char(
                right_char
            ):
                continue
            spans.append((start, end))
        return spans

    @staticmethod
    def _find_match_span(
        content: str, match_text: str, excluded_spans: List[tuple[int, int]]
    ) -> Optional[tuple[int, int]]:
        for start, end in LinkRenderer._match_spans(content, match_text):
            if any(
                start < excluded_end and end > excluded_start
                for excluded_start, excluded_end in excluded_spans
            ):
                continue
            return start, end
        return None

    @staticmethod
    def _render_target(source_uri: str, to_uri: str) -> str:
        relative = LinkRenderer.relative_path(source_uri, to_uri)
        return (relative if relative is not None else to_uri).replace(" ", "%20")

    @staticmethod
    def _strip_managed_links(content: str, source_uri: str, links: List[Dict]) -> str:
        """Remove links previously rendered from the same StoredLink metadata."""
        managed = {
            (
                str(link["match_text"]).casefold(),
                LinkRenderer._render_target(source_uri, link["to_uri"]),
            )
            for link in links
        }

        def _strip(match: re.Match) -> str:
            key = (match.group("text").casefold(), match.group("target").replace(" ", "%20"))
            return match.group("text") if key in managed else match.group(0)

        return LinkRenderer._RELATIVE_LINK_RE.sub(_strip, content)

    @staticmethod
    def render_links(content: str, source_uri: str, links: List[Dict]) -> str:
        """Replace match_text in content with relative markdown links.

        Args:
            content: Plain markdown body.
            source_uri: The viking:// URI of the file being written.
            links: List of link dicts (from links + backlinks in MEMORY_FIELDS).
        """
        eligible = [
            link for link in links if link.get("match_text") and link.get("to_uri") != source_uri
        ]
        if not eligible:
            return content

        # Rebuild only links managed by this metadata so repeated file writes are
        # idempotent and old duplicate renderings collapse back to one link.
        content = LinkRenderer._strip_managed_links(content, source_uri, eligible)
        eligible.sort(key=lambda link: (-len(str(link["match_text"])), -link.get("weight", 0)))

        existing_link_matches = list(LinkRenderer._RELATIVE_LINK_RE.finditer(content))
        excluded_spans = [(match.start(), match.end()) for match in existing_link_matches]
        excluded_spans.extend(
            (match.start(), match.end()) for match in LinkRenderer._ATX_HEADING_RE.finditer(content)
        )

        # An anchor already covered by a hand-authored Markdown link is visible;
        # do not add the same anchor again elsewhere on the page.
        claimed = {
            str(link["match_text"]).casefold()
            for link in eligible
            if any(
                LinkRenderer._match_spans(match.group("text"), str(link["match_text"]))
                for match in existing_link_matches
            )
        }

        replacements: List[tuple] = []  # (start, end, replacement_text)
        for link in eligible:
            match_text = str(link["match_text"])
            match_key = match_text.casefold()
            if match_key in claimed:
                continue

            to_uri = link["to_uri"]
            longer_anchor_spans = [
                span
                for other in eligible
                if len(str(other["match_text"])) > len(match_text)
                for span in LinkRenderer._match_spans(content, str(other["match_text"]))
            ]
            match_span = LinkRenderer._find_match_span(
                content,
                match_text,
                excluded_spans + longer_anchor_spans,
            )
            if not match_span:
                continue

            start, end = match_span
            rendered = f"[{content[start:end]}]({LinkRenderer._render_target(source_uri, to_uri)})"
            replacements.append((start, end, rendered))
            excluded_spans.append((start, end))
            claimed.add(match_key)

        # Apply in reverse order to preserve indices
        result = list(content)
        for start, end, repl in sorted(replacements, key=lambda x: x[0], reverse=True):
            result[start:end] = list(repl)

        return "".join(result)

    @staticmethod
    def strip_links(content: str) -> str:
        """Remove relative markdown links, keeping only the link text.

        External links, viking:// links, anchor links, and absolute-path links are preserved.
        """

        def _replace_link(m: re.Match) -> str:
            target = m.group("target")
            if target.startswith("#"):
                return m.group(0)
            if target.startswith("/"):
                return m.group(0)
            if "://" in target:
                return m.group(0)
            return m.group("text")

        return LinkRenderer._RELATIVE_LINK_RE.sub(_replace_link, content)

    @staticmethod
    def strip_all_links(content: str) -> str:
        """Remove markdown links regardless of target scheme, keeping only link text."""

        return LinkRenderer._RELATIVE_LINK_RE.sub(lambda m: m.group("text"), content)

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
            return "/".join(down_parts) or "./"

        up_parts = [".."] * up_count
        return "/".join(up_parts + list(down_parts))
