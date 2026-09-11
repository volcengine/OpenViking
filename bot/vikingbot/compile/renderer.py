"""Deterministic OKF Wiki rendering for compile bundles."""

from __future__ import annotations

import base64
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote, unquote

import yaml

from openviking.core.namespace import context_type_for_uri, relative_uri_path
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.utils.link_renderer import LinkRenderer, MarkdownLink
from openviking.session.memory.utils.link_resolver import resolve_wiki_links
from openviking.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
    next_memory_version,
)
from openviking.session.memory.utils.resource_refs import sync_memory_resource_refs
from openviking.utils.path_safety import (
    safe_join_viking_uri,
    sanitize_relative_viking_path,
    validate_safe_viking_uri_path,
)
from openviking_cli.utils import VikingURI
from vikingbot.compile.models import (
    COMPILE_STAGING_ROOT,
    WikiBundleDraft,
    WikiLanguage,
)

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FRONTMATTER_START_RE = re.compile(rb"\A---[ \t]*\r?\n")
_FRONTMATTER_END_RE = re.compile(rb"\r?\n---[ \t]*(?:\r?\n|\Z)")
_OKF_TYPE_DECLARATION_RE = re.compile(rb"""(?m)^(?:type|["']type["'])[ \t]*:""")
_BARE_VIKING_URI_RE = re.compile(r"""viking://[^\s<>\[\](){}"'«»，。；：！？]+""")
_LEADING_H1_RE = re.compile(r"\A(?:[ \t]*\r?\n)*#[ \t]+[^\r\n]*(?:\r?\n|\Z)")
_LEGACY_RELATED_PAGES_RE = re.compile(
    r"(?mi)^##[ \t]+(?:Related pages|相关页面)[ \t]*\r?\n"
    r"(?:[ \t]*\r?\n)*(?:[ \t]*-[^\r\n]*(?:\r?\n|\Z))+"
)
_RESERVED_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json", ".source.json"})
_PLATFORM_FRONTMATTER_FIELDS = frozenset({"type", "title", "description", "tags"})


@dataclass(slots=True)
class RenderedBundle:
    operations: list[dict[str, Any]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    wiki_uris: list[str] = field(default_factory=list)
    link_count: int = 0
    # Deterministic link diagnostics do not require an agent repair round.
    link_report: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FinalizedOutput:
    """Submitted relative paths and bytes, with Wiki paths restricted to valid OKF pages."""

    files: dict[str, bytes] = field(default_factory=dict)
    wiki_paths: set[str] = field(default_factory=set)
    link_count: int = 0
    link_report: dict[str, Any] = field(default_factory=dict)


def wiki_page_path_from_title(title: str) -> str:
    title = re.sub(r"\s+[-–—]\s+", " ", title.strip())
    return VikingURI.sanitize_segment(title)


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(content or "")
    if not match:
        return {}, content or ""
    parsed = yaml.safe_load(match.group(1)) or {}
    if not isinstance(parsed, dict):
        raise ValueError("existing OKF frontmatter must be a YAML object")
    return parsed, content[match.end() :]


def strip_okf_frontmatter(content: str) -> str:
    """Return the editable Wiki body from a materialized OKF Markdown file."""
    return _split_frontmatter(content)[1].lstrip("\r\n")


def has_unclosed_frontmatter(content: bytes) -> bool:
    opening = _FRONTMATTER_START_RE.match(content)
    return opening is not None and _FRONTMATTER_END_RE.search(content[opening.end() :]) is None


def validate_declared_okf_markdown(path: str, content: bytes) -> str | None:
    """Validate a Markdown artifact and return its declared OKF type, if any."""
    if not path.casefold().endswith(".md"):
        return
    opening = _FRONTMATTER_START_RE.match(content)
    if opening is None:
        return

    remainder = content[opening.end() :]
    closing = _FRONTMATTER_END_RE.search(remainder)
    raw_frontmatter = remainder[: closing.start()] if closing else remainder
    raw_declares_type = _OKF_TYPE_DECLARATION_RE.search(raw_frontmatter) is not None

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" must be UTF-8') from exc
        return

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" has unterminated YAML frontmatter')
        return
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" has invalid YAML frontmatter') from exc
        return
    if not isinstance(frontmatter, dict):
        if raw_declares_type:
            raise ValueError(f'OKF Markdown file "{path}" frontmatter must be a YAML object')
        return
    if "type" not in frontmatter:
        return
    if not isinstance(frontmatter["type"], str) or not frontmatter["type"].strip():
        raise ValueError(
            f'OKF Markdown file "{path}" frontmatter field "type" must be a non-empty string'
        )
    return frontmatter["type"].strip()


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in tags:
        tag = value.strip()
        if tag and tag not in normalized:
            normalized.append(tag)
    return normalized


def _frontmatter(
    *,
    old: Mapping[str, Any],
    page_type: str,
    title: str,
    summary: str,
    tags: list[str],
) -> str:
    data = {key: value for key, value in old.items() if key not in _PLATFORM_FRONTMATTER_FIELDS}
    data = {
        "type": page_type,
        "title": title,
        "description": summary,
        **data,
    }
    normalized_tags = _normalize_tags(tags)
    dumped = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=10**9)
    if normalized_tags:
        inline_tags = yaml.safe_dump(
            normalized_tags,
            allow_unicode=True,
            width=10**9,
            default_flow_style=True,
        ).strip()
        dumped += f"tags: {inline_tags}\n"
    return "---\n" + dumped + "---\n\n"


def _citation_target_allowed(target: str, source_roots: Mapping[str, str]) -> bool:
    if not target.startswith("viking://"):
        return False
    try:
        target = validate_safe_viking_uri_path(target)
    except ValueError:
        return False
    for root in source_roots.values():
        if target.rstrip("/") == root.rstrip("/") or relative_uri_path(root, target):
            return True
    return False


def _linkify_source_uris(body: str, source_roots: Mapping[str, str]) -> str:
    protected = LinkRenderer.protected_markdown_spans(body)
    replacements: list[tuple[int, int, str]] = []
    for match in _BARE_VIKING_URI_RE.finditer(body):
        start = match.start()
        target = match.group(0).rstrip(".,;:!?")
        end = start + len(target)
        if any(not (end <= span_start or start >= span_end) for span_start, span_end in protected):
            continue
        if start > 0 and end < len(body) and body[start - 1] == "<" and body[end] == ">":
            continue
        if not _citation_target_allowed(target, source_roots):
            continue
        label = unquote(target.rstrip("/").rsplit("/", 1)[-1]).removesuffix(".md")
        label = label.replace("[", r"\[").replace("]", r"\]") or "Source"
        replacements.append((start, end, f"[{label}]({target})"))

    rendered = list(body)
    for start, end, replacement in reversed(replacements):
        rendered[start:end] = replacement
    return "".join(rendered)


def _wiki_page_basename(uri: str) -> str:
    name = unquote(uri.rstrip("/").rsplit("/", 1)[-1])
    return name[:-3] if name.casefold().endswith(".md") else name


def _wiki_mention_targets(uris: set[str]) -> dict[str, str]:
    """Return unambiguous basename -> URI targets, excluding the root index."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for uri in sorted(uris):
        name = _wiki_page_basename(uri).strip()
        if not name or name.casefold() == "index":
            continue
        grouped.setdefault(name.casefold(), []).append((name, uri))
    return {items[0][0]: items[0][1] for items in grouped.values() if len(items) == 1}


def _has_link_to(body: str, source_uri: str, target_uri: str) -> bool:
    relative = LinkRenderer.relative_path(source_uri, target_uri)
    expected = {
        LinkRenderer.normalize_markdown_target(target_uri),
        LinkRenderer.normalize_markdown_target(relative if relative is not None else target_uri),
    }
    return any(
        link.start == 0 or body[link.start - 1] != "!"
        for link in LinkRenderer.iter_markdown_links(body)
        if LinkRenderer.normalize_markdown_target(link.target) in expected
    )


def _strip_legacy_related_pages(body: str) -> str:
    rendered, count = _LEGACY_RELATED_PAGES_RE.subn("", body)
    return rendered.rstrip() if count else body


def _link_wiki_mentions(
    content: str,
    *,
    source_uri: str,
    targets: Mapping[str, str],
) -> tuple[str, int]:
    """Link the first body mention of each unambiguous Wiki filename."""
    frontmatter = _FRONTMATTER_RE.match(content)
    prefix = content[: frontmatter.end()] if frontmatter else ""
    body = content[frontmatter.end() :] if frontmatter else content
    title = _LEADING_H1_RE.match(body)
    if title:
        prefix += body[: title.end()]
        body = body[title.end() :]
    body = _strip_legacy_related_pages(body)

    links = [
        {
            "match_text": name,
            "to_uri": target_uri,
            "weight": len(name),
        }
        for name, target_uri in targets.items()
        if target_uri != source_uri and not _has_link_to(body, source_uri, target_uri)
    ]
    rendered, count = LinkRenderer.render_links_with_count(body, source_uri, links)
    return prefix + rendered, count


def validate_resource_file(path: str, payload: bytes) -> bool:
    """Validate one Resource artifact; return whether it declares a valid OKF Wiki page."""
    if validate_declared_okf_markdown(path, payload) is None:
        return False
    frontmatter, _body = _split_frontmatter(payload.decode("utf-8"))
    missing = [
        field
        for field in ("type", "title", "description")
        if not isinstance(frontmatter.get(field), str) or not str(frontmatter[field]).strip()
    ]
    if missing:
        raise ValueError(
            f'OKF Markdown file "{path}" must have non-empty YAML frontmatter fields: '
            + ", ".join(missing)
        )
    description = str(frontmatter["description"]).strip()
    if "\n" in description or "\r" in description:
        raise ValueError(f'OKF Markdown file "{path}" frontmatter description must be one line')
    return True


def _markdown_link(label: str, target: str) -> str:
    """Escape a display label and URI without changing the underlying filename."""
    label = " ".join(label.split()).replace("\\", "\\\\").replace("[", r"\[").replace("]", r"\]")
    return f"[{label}]({quote(target, safe='/:@#?=&%+,-._~')})"


def _body_markdown_links(body: str) -> list[MarkdownLink]:
    """Return editable inline links outside code, comments and image syntax."""
    links = list(LinkRenderer.iter_markdown_links(body))
    link_spans = {(link.start, link.end) for link in links}
    protected = [s for s in LinkRenderer.protected_markdown_spans(body) if s not in link_spans]
    protected.extend((m.start(), m.end()) for m in re.finditer(r"<!--.*?-->", body, re.DOTALL))
    protected.extend((m.start(), m.end()) for m in re.finditer(r"(?m)^(?: {4}|\t)[^\n]*", body))
    return [
        link
        for link in links
        if not (link.start > 0 and body[link.start - 1] == "!")
        and not any(link.start < end and link.end > start for start, end in protected)
    ]


def _link_uri(target: str, source_uri: str) -> str:
    """Resolve a Markdown destination to an absolute URI for deduplication and lookup."""
    target = LinkRenderer.normalize_markdown_target(target)
    if re.match(r"^[A-Za-z][\w+.-]*:", target):
        return target
    directory = posixpath.dirname(source_uri.removeprefix("viking://"))
    return "viking://" + posixpath.normpath(posixpath.join(directory, target))


def _append_link_list(
    body: str, kind: str, source_uri: str, entries: Mapping[str, str]
) -> tuple[str, int]:
    """Render plain Markdown links and return their count; navigation replaces its heading section."""
    pattern = rf"\n*<!-- ov-compile:{kind}:start -->.*?<!-- ov-compile:{kind}:end -->\n*"
    clean = re.sub(pattern, "\n\n", body, flags=re.DOTALL).rstrip()
    if kind == "navigation":
        clean = re.sub(
            r"(?ms)^## (?:分类导航|Navigation)[ \t]*\n.*?(?=^## |\Z)", "", clean
        ).rstrip()
    linked = {_link_uri(link.target, source_uri) for link in _body_markdown_links(clean)}
    lines = []
    for uri, text in entries.items():
        target = _link_uri(uri, source_uri)
        if kind == "navigation" or target not in linked:
            lines.append("- " + text)
            linked.add(target)
    if not lines:
        return (clean if clean != body.rstrip() else body), 0
    title = (
        {"sources": "来源", "navigation": "分类导航"}[kind]
        if re.search(r"[\u4e00-\u9fff]", body + "".join(entries.values()))
        else kind.title()
    )
    heading = f"## {title}" if kind == "navigation" else f"**{title}**"
    block = heading + "\n\n" + "\n".join(lines)
    if kind == "navigation":
        sections = re.split(r"(?m)(?=^## |^\*\*(?:来源|Sources)\*\*)", clean, maxsplit=1)
        return "\n\n".join([sections[0].rstrip(), block, *sections[1:]]) + "\n", len(lines)
    return clean + "\n\n" + block + "\n", len(lines)


def _repair_relative_links(
    body: str,
    *,
    source_path: str,
    target_uri: str,
    pages: Mapping[str, Mapping[str, Any]],
    known_paths: set[str],
    paths_by_name: Mapping[str, list[str]],
) -> tuple[str, int, list[dict[str, Any]]]:
    """Correct only unique filenames confirmed by the link label; retain and report other broken paths."""
    source_uri = safe_join_viking_uri(target_uri, source_path)
    repaired, unresolved = 0, []
    for link in reversed(_body_markdown_links(body)):
        target = link.target.strip().removeprefix("<").removesuffix(">")
        if target.startswith(("/", "#", "?")) or re.match(r"^[A-Za-z][\w+.-]*:", target):
            continue
        raw_path = re.split(r"[#?]", target, maxsplit=1)[0]
        resolved = relative_uri_path(target_uri, _link_uri(raw_path, source_uri))
        if not resolved or resolved in known_paths:
            continue
        name = posixpath.basename(resolved)
        candidates = paths_by_name.get(name, [])
        candidate = candidates[0] if len(candidates) == 1 else None
        metadata = pages.get(candidate, {})
        aliases = metadata.get("aliases")
        labels = [metadata.get("title"), posixpath.splitext(name)[0]]
        labels.extend(aliases if isinstance(aliases, list) else [])
        label = LinkRenderer._BACKSLASH_ESCAPE_RE.sub(r"\1", link.text).strip()
        if candidate is None or candidate == source_path or label not in labels:
            unresolved.append(
                {
                    "source_path": source_path,
                    "target": link.target,
                    "label": label,
                    "candidates": candidates,
                }
            )
            continue
        corrected = posixpath.relpath(candidate, posixpath.dirname(source_path) or ".")
        corrected = corrected if "/" in corrected else "./" + corrected
        corrected = quote(corrected, safe="/,-._~") + target[len(raw_path) :]
        # Replace only the destination, retaining the original label and optional tooltip.
        start = body.index("](", link.start, link.end) + 2
        body = (
            body[:start]
            + body[start : link.end].replace(link.target, corrected, 1)
            + body[link.end :]
        )
        repaired += 1
    return body, repaired, list(reversed(unresolved))


def _source_link_entries(metadata: Mapping[str, Any], target_uri: str) -> dict[str, str]:
    """Build source entries from resource/path metadata without inventing sources or renaming files."""
    entries = {}
    sources = metadata.get("sources")
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, Mapping):
            continue
        uri = source.get("resource") or source.get("path")
        if not isinstance(uri, str):
            continue
        uri = uri.strip()
        if uri.startswith("viking://"):
            try:
                validate_safe_viking_uri_path(uri)
            except ValueError:
                continue
            if uri.rstrip("/") == target_uri or relative_uri_path(target_uri, uri):
                continue
        elif not uri.startswith(("https://", "http://")):
            continue
        label = source.get("title") or source.get("file") or _wiki_page_basename(uri)
        entries.setdefault(uri, _markdown_link(str(label), uri))
    return entries


def finalize_resource_output(
    files: Mapping[str, bytes],
    *,
    target_uri: str,
    source_roots: Mapping[str, str],
    existing_files: Mapping[str, bytes] | None = None,
    known_paths: set[str] | None = None,
) -> FinalizedOutput:
    """Finalize Wiki links without model calls or modifying unrelated retained files.

    Submitted files override the retained catalog. Retained index pages are
    refreshed with direct pages and child-directory indexes, and missing ancestor
    indexes are created; other retained pages only supply link targets.
    Broken links that cannot be identified uniquely are
    returned in link_report, never as validation errors.
    """
    existing_files = existing_files or {}
    all_files = {**existing_files, **files}
    pages: dict[str, dict[str, Any]] = {}
    for path, payload in all_files.items():
        if path not in files and any(part.startswith(".") for part in path.split("/")):
            continue
        try:
            if validate_resource_file(path, payload):
                pages[path] = _split_frontmatter(payload.decode("utf-8"))[0]
        except (ValueError, UnicodeError):
            if path in files:
                raise
    wiki_paths = set(pages) & set(files)

    finalized = dict(files)
    if not wiki_paths:
        return FinalizedOutput(files=finalized)
    # Every nonempty Wiki directory has an index, even when the model omits it.
    chinese = any(re.search(r"[\u4e00-\u9fff]", metadata["title"]) for metadata in pages.values())
    directories = {parent for path in pages for parent in PurePosixPath(path).parents}
    for directory in sorted(directories):
        index = str(directory / "index.md")
        if index not in all_files:
            title = directory.name or _wiki_page_basename(target_uri)
            description = (
                f"本目录汇总 {title} 下的知识页面与分类入口，可通过分类导航逐层查阅。"
                if chinese
                else f"Browse the knowledge pages and categories in {title} using the navigation below."
            )
            metadata = {"type": "index", "title": title, "description": description, "sources": []}
            pages[index] = metadata
            header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
            all_files[index] = f"---\n{header}---\n\n# {title}\n".encode("utf-8")
        if index in pages and pages[index]["type"] == "index":
            wiki_paths.add(index)

    catalog_paths = set(known_paths or ()) | set(all_files) | {str(p) for p in directories}
    paths_by_name: dict[str, list[str]] = {}
    for path in pages:
        paths_by_name.setdefault(posixpath.basename(path), []).append(path)
    # Keep the existing filename-mention behavior scoped to submitted pages.
    mention_targets = _wiki_mention_targets(
        {safe_join_viking_uri(target_uri, path).rstrip("/") for path in wiki_paths}
    )
    link_count = 0
    report: dict[str, Any] = {
        "repaired_count": 0,
        "source_links": 0,
        "navigation_links": 0,
        "unresolved": [],
    }
    for path in sorted(wiki_paths):
        content = all_files[path].decode("utf-8")
        uri = safe_join_viking_uri(target_uri, path).rstrip("/")
        frontmatter = _FRONTMATTER_RE.match(content)
        assert frontmatter is not None
        prefix, body = content[: frontmatter.end()], content[frontmatter.end() :]
        body = _strip_legacy_related_pages(body)
        body, repaired, unresolved = _repair_relative_links(
            body,
            source_path=path,
            target_uri=target_uri,
            pages=pages,
            known_paths=catalog_paths,
            paths_by_name=paths_by_name,
        )
        report["repaired_count"] += repaired
        report["unresolved"].extend(unresolved)
        content = prefix + _linkify_source_uris(body, source_roots)
        content, rendered_count = _link_wiki_mentions(
            content,
            source_uri=uri,
            targets=mention_targets,
        )
        body = content[len(prefix) :]
        body, source_count = _append_link_list(
            body, "sources", uri, _source_link_entries(pages[path], target_uri)
        )
        report["source_links"] += source_count
        navigation_count = 0
        if pages[path]["type"] == "index":
            heading = _LEADING_H1_RE.match(body)
            if heading:
                rest = body[heading.end() :].lstrip()
                if not rest or rest.startswith(("#", "**", "- ", "<!--")):
                    body = (
                        body[: heading.end()].rstrip()
                        + "\n\n"
                        + pages[path]["description"]
                        + "\n\n"
                        + rest
                    )
            directory = posixpath.dirname(path)
            # Each index exposes one level; child indexes provide access to deeper pages.
            entries = {
                safe_join_viking_uri(target_uri, page): _markdown_link(
                    metadata["title"], posixpath.relpath(page, directory or ".")
                )
                + " — "
                + metadata["description"]
                for page, metadata in sorted(pages.items())
                if page != path and posixpath.dirname(page.removesuffix("/index.md")) == directory
            }
            body, navigation_count = _append_link_list(body, "navigation", uri, entries)
            report["navigation_links"] += navigation_count
        payload = (prefix + body).encode("utf-8")
        if path in files or existing_files.get(path) != payload:
            finalized[path] = payload
        link_count += rendered_count
        link_count += source_count + navigation_count

    return FinalizedOutput(
        files=finalized,
        wiki_paths=wiki_paths & set(finalized),
        link_count=link_count,
        link_report=report,
    )


def _render_source_fallback(
    body: str,
    *,
    source_ids: list[str],
    source_roots: Mapping[str, str],
    wiki_language: WikiLanguage | None,
) -> str:
    linked_targets = {
        LinkRenderer.normalize_markdown_target(link.target)
        for link in LinkRenderer.iter_markdown_links(body)
        if _citation_target_allowed(
            LinkRenderer.normalize_markdown_target(link.target), source_roots
        )
    }
    missing: list[tuple[str, str]] = []
    for source_id in source_ids:
        target = source_roots[source_id]
        if any(
            linked.rstrip("/") == target.rstrip("/") or relative_uri_path(target, linked)
            for linked in linked_targets
        ):
            continue
        label = unquote(target.rstrip("/").rsplit("/", 1)[-1]) or f"Source {source_id}"
        missing.append((label, target))
    if not missing:
        return body.rstrip()
    heading = "来源" if wiki_language == "zh-CN" else "Sources"
    lines = [f"- [{label}]({target})" for label, target in missing]
    return body.rstrip() + f"\n\n## {heading}\n\n" + "\n".join(lines) + "\n"


def validate_relative_page_path(path: str) -> str:
    relative = sanitize_relative_viking_path(path).strip("/")
    if not relative.lower().endswith(".md"):
        relative += ".md"
    segments = [segment for segment in relative.split("/") if segment]
    if not segments or any(segment.startswith(".") for segment in segments):
        raise ValueError(f"invalid Wiki page path: {path}")
    if segments[-1].lower() in _RESERVED_FILENAMES:
        raise ValueError(f"reserved Wiki page path: {path}")
    return "/".join(segments)


def validate_relative_file_path(path: str) -> str:
    """Validate an output-relative path, rejecting metadata and internal staging directories."""
    relative = sanitize_relative_viking_path(path).strip("/")
    segments = relative.split("/")
    if (
        not relative
        or any(not segment or segment in {".", ".."} for segment in segments)
        or any(segment.startswith(".") for segment in segments)
    ):
        raise ValueError(f"invalid output file path: {path}")
    if segments[-1].lower() in _RESERVED_FILENAMES:
        raise ValueError(f"reserved output file path: {path}")
    if COMPILE_STAGING_ROOT.casefold() in (segment.casefold() for segment in segments):
        raise ValueError(f"reserved staging directory in output path: {path}")
    return relative


def is_reserved_wiki_page_uri(uri: str) -> bool:
    return uri.rstrip("/").rsplit("/", 1)[-1].lower() in _RESERVED_FILENAMES


def _merge_stored_links(
    existing: list[dict[str, Any]], new_links: list[StoredLink]
) -> list[dict[str, Any]]:
    result = [dict(item) for item in existing if isinstance(item, dict)]
    seen = {
        (
            item.get("from_uri"),
            item.get("to_uri"),
            item.get("link_type"),
            item.get("weight"),
            item.get("match_text"),
            item.get("description"),
        )
        for item in result
    }
    for link in new_links:
        item = link.model_dump()
        key = (
            item.get("from_uri"),
            item.get("to_uri"),
            item.get("link_type"),
            item.get("weight"),
            item.get("match_text"),
            item.get("description"),
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


class WikiRenderer:
    """Validate and render complete Wiki bundles without count or byte quotas."""

    def render(
        self,
        *,
        bundle: WikiBundleDraft,
        target_uri: str,
        source_roots: Mapping[str, str],
        catalog_uris: set[str],
        existing_raw: Mapping[str, str],
        wiki_language: WikiLanguage | None = None,
        file_catalog_uris: set[str] | None = None,
        existing_bytes: Mapping[str, bytes] | None = None,
        file_payloads: list[bytes | None] | None = None,
    ) -> RenderedBundle:
        file_catalog_uris = set(catalog_uris) | set(file_catalog_uris or ())
        existing_bytes = existing_bytes or {}
        file_payloads = file_payloads or []
        if not bundle.pages and bundle.links:
            raise ValueError("an empty Wiki bundle cannot contain links")
        target_type = context_type_for_uri(target_uri)
        memory_target = target_type == "memory"
        if memory_target and bundle.files:
            raise ValueError("raw artifact files are only supported for Resource targets")

        page_ids: set[int] = set()
        page_uris: dict[int, list[str]] = {}
        page_by_id = {}
        output_uris: set[str] = set()
        for page in bundle.pages:
            if page.page_id in page_ids:
                raise ValueError(f"duplicate page_id: {page.page_id}")
            page_ids.add(page.page_id)
            page_by_id[page.page_id] = page
            title = page.title.strip()
            page_type = page.page_type.strip()
            summary = page.summary.strip()
            if not title or not page_type or not summary:
                raise ValueError(f"page {page.page_id} title, page_type and summary are required")
            if "\n" in summary or "\r" in summary:
                raise ValueError(f"page {page.page_id} summary must be a single line")
            if _FRONTMATTER_RE.match(page.body_markdown.lstrip()):
                raise ValueError(f"page {page.page_id} body_markdown must not contain frontmatter")
            source_ids = list(
                dict.fromkeys(value.strip() for value in page.source_ids if value.strip())
            )
            if not source_ids or any(source_id not in source_roots for source_id in source_ids):
                raise ValueError(f"page {page.page_id} must reference valid source_ids")

            if page.update_uri:
                uri = page.update_uri.rstrip("/")
                if is_reserved_wiki_page_uri(uri):
                    raise ValueError(f"reserved Wiki page cannot be updated: {uri}")
                if uri not in catalog_uris:
                    raise ValueError(f"update_uri is not in the target catalog: {uri}")
                if page.path_hint:
                    raise ValueError("path_hint is not allowed with update_uri")
                if uri not in existing_raw:
                    raise ValueError(f"raw content was not loaded for update_uri: {uri}")
            else:
                hint = page.path_hint or wiki_page_path_from_title(title)
                relative = validate_relative_page_path(hint)
                uri = safe_join_viking_uri(target_uri, relative).rstrip("/")
                if uri in file_catalog_uris:
                    raise ValueError(f"Wiki page already exists; use update_uri: {uri}")
            if uri in output_uris:
                raise ValueError(f"duplicate final Wiki path: {uri}")
            output_uris.add(uri)
            page_uris[page.page_id] = [uri]

        file_uris: list[str] = []
        for index, file in enumerate(bundle.files):
            if file.update_uri:
                uri = validate_safe_viking_uri_path(file.update_uri).rstrip("/")
                if is_reserved_wiki_page_uri(uri):
                    raise ValueError(f"reserved output file cannot be updated: {uri}")
                if uri not in file_catalog_uris:
                    raise ValueError(f"file update_uri is not in the target catalog: {uri}")
                if uri not in existing_bytes:
                    raise ValueError(f"raw bytes were not loaded for file update_uri: {uri}")
            else:
                relative = validate_relative_file_path(file.path or "")
                uri = safe_join_viking_uri(target_uri, relative).rstrip("/")
                if uri in file_catalog_uris:
                    raise ValueError(f"output file already exists; use update_uri: {uri}")
            if uri in output_uris:
                raise ValueError(f"duplicate final output path: {uri}")
            output_uris.add(uri)
            file_uris.append(uri)

            if file.workspace_path is not None and (
                index >= len(file_payloads) or file_payloads[index] is None
            ):
                raise ValueError(f"workspace payload was not loaded for file {index}")

        for link in bundle.links:
            if link.f is None or link.t is None or link.f == link.t:
                raise ValueError("WikiLink endpoints must be non-null and non-self")
            source_page = page_by_id.get(link.f)
            if source_page is None or link.t not in page_by_id:
                raise ValueError(f"WikiLink references an unknown page_id: f={link.f}, t={link.t}")
            if not link.match_text:
                raise ValueError("WikiLink match_text is required")
            if not LinkRenderer.can_render_link(
                source_page.body_markdown,
                link.match_text,
                page_uris[link.f][0],
                page_uris[link.t][0],
            ):
                raise ValueError(
                    f"WikiLink match_text is not a satisfiable body anchor: {link.match_text!r}"
                )

        resolved_links = resolve_wiki_links(bundle.links, page_uris, strict=True)
        mention_targets = (
            _wiki_mention_targets(set(existing_raw) | {uris[0] for uris in page_uris.values()})
            if not memory_target and bundle.pages
            else {}
        )
        result = RenderedBundle()
        for page in bundle.pages:
            uri = page_uris[page.page_id][0]
            result.wiki_uris.append(uri)
            is_update = page.update_uri is not None
            old_raw = existing_raw.get(uri, "")
            if memory_target and is_update:
                old_memory = MemoryFileUtils.read(old_raw, uri=uri)
                old_visible = old_memory.content
            else:
                old_memory = None
                old_visible = old_raw
            old_frontmatter, _ = _split_frontmatter(old_visible)

            outgoing = (
                [link for link in resolved_links if link.from_uri == uri] if memory_target else []
            )
            incoming = (
                [link for link in resolved_links if link.to_uri == uri] if memory_target else []
            )
            if memory_target:
                rendered_body, rendered_count = LinkRenderer.render_links_with_count(
                    page.body_markdown.strip(),
                    uri,
                    [link.model_dump() for link in outgoing],
                )
            else:
                rendered_body = page.body_markdown.strip()
                rendered_count = 0
            result.link_count += rendered_count
            rendered_body = _linkify_source_uris(rendered_body, source_roots)
            source_ids = list(
                dict.fromkeys(value.strip() for value in page.source_ids if value.strip())
            )
            rendered_body = _render_source_fallback(
                rendered_body,
                source_ids=source_ids,
                source_roots=source_roots,
                wiki_language=wiki_language,
            )
            visible = (
                _frontmatter(
                    old=old_frontmatter,
                    page_type=page.page_type.strip(),
                    title=page.title.strip(),
                    summary=page.summary.strip(),
                    tags=page.tags,
                )
                + rendered_body
            )

            if not memory_target:
                visible, automatic_count = _link_wiki_mentions(
                    visible,
                    source_uri=uri,
                    targets=mention_targets,
                )
                result.link_count += automatic_count

            if memory_target:
                mf = old_memory or MemoryFile(uri=uri)
                mf.uri = uri
                mf.content = visible
                mf.extra_fields["category"] = page.page_type.strip()
                mf.extra_fields["version"] = (
                    int(mf.extra_fields.get("version", 1) or 1) if old_memory else 1
                )
                mf.links = _merge_stored_links(mf.links, outgoing)
                mf.backlinks = _merge_stored_links(mf.backlinks, incoming)
                sync_memory_resource_refs(mf, source="compile")
                candidate = MemoryFileUtils.write(mf, render_links=False)
                if old_memory is not None and candidate != old_raw:
                    mf.extra_fields["version"] = next_memory_version(old_memory)
                    candidate = MemoryFileUtils.write(mf, render_links=False)
            else:
                candidate = visible

            if candidate == old_raw:
                result.unchanged.append(uri)
                continue
            if is_update:
                result.updated.append(uri)
            else:
                result.created.append(uri)
            result.operations.append({"uri": uri, "content": candidate, "mode": "upsert"})

        if not memory_target and bundle.pages:
            for uri, old_raw in sorted(existing_raw.items()):
                if uri in output_uris:
                    continue
                candidate, automatic_count = _link_wiki_mentions(
                    old_raw,
                    source_uri=uri,
                    targets=mention_targets,
                )
                if candidate == old_raw:
                    continue
                result.link_count += automatic_count
                result.updated.append(uri)
                result.wiki_uris.append(uri)
                result.operations.append(
                    {
                        "uri": uri,
                        "content": candidate,
                        "mode": "upsert",
                    }
                )

        for index, file in enumerate(bundle.files):
            uri = file_uris[index]
            if file.content is not None:
                candidate = file.content.encode("utf-8")
                operation_content = {"content": file.content}
            else:
                candidate = file_payloads[index]
                assert candidate is not None
                operation_content = {"content_base64": base64.b64encode(candidate).decode("ascii")}

            if target_type == "resource":
                page_type = validate_declared_okf_markdown(uri, candidate)
                if page_type is not None:
                    result.wiki_uris.append(uri)
                if file.update_uri and uri in catalog_uris and page_type is None:
                    raise ValueError(
                        "an existing Wiki page updated as a raw file must retain "
                        "valid OKF frontmatter with a non-empty type"
                    )

            is_update = file.update_uri is not None
            old = existing_bytes.get(uri)
            if old is not None and candidate == old:
                result.unchanged.append(uri)
                continue
            if is_update:
                assert old is not None
                result.updated.append(uri)
            else:
                result.created.append(uri)
            result.operations.append({"uri": uri, **operation_content, "mode": "upsert"})
        return result


__all__ = [
    "FinalizedOutput",
    "RenderedBundle",
    "WikiRenderer",
    "finalize_resource_output",
    "has_unclosed_frontmatter",
    "strip_okf_frontmatter",
    "is_reserved_wiki_page_uri",
    "validate_declared_okf_markdown",
    "validate_relative_file_path",
    "validate_relative_page_path",
    "validate_resource_file",
    "wiki_page_path_from_title",
]
