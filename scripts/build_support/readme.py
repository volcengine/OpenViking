"""Build the PyPI long description from README.md.

README.md uses repository-relative paths for images and links, which work on
GitHub but not on PyPI. This rewrites them to absolute GitHub URLs at build
time so README.md itself can stay relative.
"""

import posixpath
import re
from html import escape, unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO = "volcengine/OpenViking"
DEFAULT_REF = "main"

_ABSOLUTE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|#)")
_HTML_ATTR = re.compile(r'\b(src|srcset|href)="([^"]*)"')
_MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)((?:\s+\"[^\"]*\")?)\)")


def _is_relative(target: str) -> bool:
    return bool(target) and not _ABSOLUTE.match(target)


def _normalize(target: str) -> str:
    path, sep, fragment = target.partition("#")
    path = posixpath.normpath(path.lstrip("/"))
    return path + (sep + fragment if sep else "")


def _raw_url(target: str, ref: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{ref}/{_normalize(target)}"


def _blob_url(target: str, ref: str) -> str:
    return f"https://github.com/{REPO}/blob/{ref}/{_normalize(target)}"


def rewrite_relative_urls(markdown: str, ref: str = DEFAULT_REF) -> str:
    """Rewrite repository-relative image sources and links to absolute URLs."""

    def html_attr(match: re.Match) -> str:
        attr, value = match.group(1), match.group(2)
        if not _is_relative(value):
            return match.group(0)
        url = _blob_url(value, ref) if attr == "href" else _raw_url(value, ref)
        return f'{attr}="{url}"'

    def md_link(match: re.Match) -> str:
        bang, text, target, title = match.groups()
        if not _is_relative(target):
            return match.group(0)
        url = _raw_url(target, ref) if bang else _blob_url(target, ref)
        return f"{bang}[{text}]({url}{title})"

    return _MD_LINK.sub(md_link, _HTML_ATTR.sub(html_attr, markdown))


def pypi_long_description(readme_path: Path, ref: str = DEFAULT_REF) -> str:
    """PyPI 复用 README 时移除仅适用于 GitHub 展示的云服务 UTM。"""

    def without_github_utm(target: str) -> str:
        try:
            url = urlsplit(unescape(target))
        except ValueError:
            return target
        managed_service = (
            url.netloc == "www.volcengine.com"
            and url.path.rstrip("/") == "/product/openviking-service"
        ) or (
            url.netloc == "console.volcengine.com"
            and (url.path == "/vikingdb/openviking" or url.path.startswith("/vikingdb/openviking/"))
        )
        if url.scheme != "https" or not managed_service:
            return target
        params = parse_qsl(url.query, keep_blank_values=True)
        if ("utm_source", "github") not in params:
            return target
        utm_keys = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}
        query = urlencode([(key, value) for key, value in params if key not in utm_keys])
        return urlunsplit(url._replace(query=query))

    def html_attr(match: re.Match) -> str:
        attr, target = match.groups()
        if attr != "href":
            return match.group(0)
        next_target = without_github_utm(target)
        return (
            match.group(0) if next_target == target else f'href="{escape(next_target, quote=True)}"'
        )

    def md_link(match: re.Match) -> str:
        bang, text, target, title = match.groups()
        if bang:
            return match.group(0)
        return f"[{text}]({without_github_utm(target)}{title})"

    markdown = rewrite_relative_urls(readme_path.read_text(encoding="utf-8"), ref)
    return _MD_LINK.sub(md_link, _HTML_ATTR.sub(html_attr, markdown))
