import re
from typing import Dict, List, Optional

from openviking.core.namespace import uri_parts


class LinkRenderer:
    """Renders and strips local markdown links in memory file content based on StoredLink metadata."""

    _RELATIVE_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)\s]+)\)")
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
    def _find_match_span(content: str, match_text: str) -> Optional[tuple[int, int]]:
        escaped = re.escape(match_text)
        if LinkRenderer._contains_cjk(match_text):
            match = re.search(escaped, content)
            if not match:
                return None
            return match.start(), match.end()

        pattern = re.compile(escaped, re.IGNORECASE)
        for match in pattern.finditer(content):
            start, end = match.start(), match.end()
            left_char = content[start - 1] if start > 0 else ""
            right_char = content[end] if end < len(content) else ""
            if LinkRenderer._is_ascii_word_char(left_char) or LinkRenderer._is_ascii_word_char(
                right_char
            ):
                continue
            return start, end
        return None

    @staticmethod
    def render_links(content: str, source_uri: str, links: List[Dict]) -> str:
        """Replace match_text in content with relative markdown links.

        Args:
            content: Plain markdown body.
            source_uri: The viking:// URI of the file being written.
            links: List of link dicts (from links + backlinks in MEMORY_FIELDS).
        """
        eligible = [l for l in links if l.get("match_text")]
        if not eligible:
            return content

        eligible.sort(key=lambda l: l.get("weight", 0), reverse=True)

        replacements: List[tuple] = []  # (start, end, replacement_text)
        for link in eligible:
            match_text = link["match_text"]
            to_uri = link["to_uri"]

            if to_uri == source_uri:
                continue

            rel = LinkRenderer.relative_path(source_uri, to_uri)
            link_target = rel if rel is not None else to_uri

            match_span = LinkRenderer._find_match_span(content, match_text)
            if not match_span:
                continue

            start, end = match_span
            # Skip if overlaps with an existing replacement
            if any(not (end <= rs or start >= re_) for rs, re_, _ in replacements):
                continue

            rendered = f"[{content[start:end]}]({link_target})"
            replacements.append((start, end, rendered))

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
        for s, t in zip(src, tgt):
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
