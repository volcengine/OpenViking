import re
from typing import Dict, List, Optional

from openviking.core.namespace import uri_parts
from openviking.session.memory.dataclass import StoredLink


class LinkRenderer:
    """Renders and strips local markdown links in memory file content based on StoredLink metadata."""

    _RELATIVE_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<target>[^)\s]+)\)")

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
            from_uri = link["from_uri"]
            to_uri = link["to_uri"]

            target_uri = to_uri if from_uri == source_uri else from_uri
            if target_uri == source_uri:
                continue

            rel = LinkRenderer.relative_path(source_uri, target_uri)
            link_target = rel if rel is not None else target_uri

            pattern = re.compile(r"\b" + re.escape(match_text) + r"\b", re.IGNORECASE)
            match = pattern.search(content)
            if not match:
                continue

            start, end = match.start(), match.end()
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

        Absolute links (viking://, http://, etc.) and anchor links are preserved.
        """

        def _replace_link(m: re.Match) -> str:
            target = m.group("target")
            if "://" in target or target.startswith("/") or target.startswith("#"):
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

        common = 0
        for s, t in zip(src, tgt):
            if s == t:
                common += 1
            else:
                break

        if common < 2:
            return None

        # -1 because the last segment of source is a filename, not a directory
        up_count = len(src) - common - 1
        down_parts = tgt[common:]

        if up_count == 0:
            return "/".join(down_parts) or "./"

        up_parts = [".."] * up_count
        return "/".join(up_parts + list(down_parts))
