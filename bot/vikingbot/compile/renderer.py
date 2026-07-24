"""Deterministic OKF Wiki rendering for compile bundles."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import unquote

import yaml

from openviking.core.namespace import context_type_for_uri, relative_uri_path
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.utils.link_renderer import LinkRenderer
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
from vikingbot.compile.models import CompileLimits, WikiBundleDraft

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FRONTMATTER_START_RE = re.compile(rb"\A---[ \t]*\r?\n")
_FRONTMATTER_END_RE = re.compile(rb"\r?\n---[ \t]*(?:\r?\n|\Z)")
_OKF_TYPE_DECLARATION_RE = re.compile(rb"""(?m)^(?:type|["']type["'])[ \t]*:""")
_CITATION_LINE_RE = re.compile(r"^\[\d+\]\s+\[([^\]\n]+)\]\(([^)\n]+)\)\s*$")
_BARE_VIKING_URI_RE = re.compile(
    r"""viking://[^\s<>\[\](){}"'«»，。；：！？]+"""
)
_RELATED_PAGES_RE = re.compile(r"(?mi)^#{1,6}[ \t]+Related pages[ \t]*$")
_RESERVED_FILENAMES = frozenset(
    {"index.md", "log.md", ".abstract.md", ".overview.md", ".relations.json"}
)
_PLATFORM_FRONTMATTER_FIELDS = frozenset({"type", "title", "description", "tags"})


@dataclass(slots=True)
class RenderedBundle:
    operations: list[dict[str, Any]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    link_count: int = 0


def content_hash(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(content or "")
    if not match:
        return {}, content or ""
    parsed = yaml.safe_load(match.group(1)) or {}
    if not isinstance(parsed, dict):
        raise ValueError("existing OKF frontmatter must be a YAML object")
    return parsed, content[match.end() :]


def has_unclosed_frontmatter(content: bytes) -> bool:
    opening = _FRONTMATTER_START_RE.match(content)
    return opening is not None and _FRONTMATTER_END_RE.search(
        content[opening.end() :]
    ) is None


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


def _split_citations(body: str) -> tuple[str, list[tuple[str, str]]]:
    protected = [
        (start, end)
        for start, end in LinkRenderer.protected_markdown_spans(body)
        if not body[start:end].startswith("# Citations")
    ]
    heading = None
    for match in re.finditer(r"(?m)^# Citations[ \t]*$", body):
        if not any(start <= match.start() < end for start, end in protected):
            heading = match
            break
    if heading is None:
        return body.rstrip(), []
    citations: list[tuple[str, str]] = []
    for line in body[heading.end() :].strip().splitlines():
        match = _CITATION_LINE_RE.match(line.strip())
        if match:
            citations.append((match.group(1).strip(), match.group(2).strip()))
    return body[: heading.start()].rstrip(), citations


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


def _linkify_source_uris(
    body: str, source_roots: Mapping[str, str]
) -> tuple[str, list[tuple[str, str]]]:
    protected = LinkRenderer.protected_markdown_spans(body)
    replacements: list[tuple[int, int, str]] = []
    citations: list[tuple[str, str]] = []
    seen: set[str] = set()
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
        if target not in seen:
            seen.add(target)
            citations.append((label, target))

    rendered = list(body)
    for start, end, replacement in reversed(replacements):
        rendered[start:end] = replacement
    return "".join(rendered), citations


def _render_related_pages(
    body: str,
    *,
    page_uri: str,
    incoming: list[StoredLink],
    page_titles: Mapping[str, str],
) -> str:
    if not incoming or _RELATED_PAGES_RE.search(body):
        return body
    lines: list[str] = []
    seen: set[str] = set()
    for link in incoming:
        source_uri = link.from_uri
        if source_uri in seen:
            continue
        seen.add(source_uri)
        target = LinkRenderer.relative_path(page_uri, source_uri) or source_uri
        target = target.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
        if f"]({target})" in body:
            continue
        label = page_titles.get(source_uri) or source_uri.rstrip("/").rsplit("/", 1)[-1]
        label = label.replace("[", r"\[").replace("]", r"\]")
        lines.append(f"- [{label}]({target})")
    if not lines:
        return body
    return body.rstrip() + "\n\n## Related pages\n\n" + "\n".join(lines)


def _render_citations(
    body: str,
    *,
    old_body: str,
    source_ids: list[str],
    source_roots: Mapping[str, str],
    inline_citations: list[tuple[str, str]] | None = None,
) -> str:
    body, draft_citations = _split_citations(body)
    _old_without_citations, old_citations = _split_citations(old_body)
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, target in [
        *old_citations,
        *draft_citations,
        *(inline_citations or []),
    ]:
        if not _citation_target_allowed(target, source_roots) or target in seen:
            continue
        seen.add(target)
        merged.append((label, target))
    for source_id in source_ids:
        target = source_roots[source_id]
        if target in seen:
            continue
        seen.add(target)
        label = target.rstrip("/").rsplit("/", 1)[-1] or f"Source {source_id}"
        merged.append((label, target))
    lines = [f"[{index}] [{label}]({target})" for index, (label, target) in enumerate(merged, 1)]
    return body.rstrip() + "\n\n# Citations\n\n" + "\n".join(lines) + "\n"


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
    def __init__(self, limits: CompileLimits | None = None):
        self.limits = limits or CompileLimits()

    def render(
        self,
        *,
        bundle: WikiBundleDraft,
        target_uri: str,
        source_roots: Mapping[str, str],
        catalog_uris: set[str],
        existing_raw: Mapping[str, str],
        file_catalog_uris: set[str] | None = None,
        existing_bytes: Mapping[str, bytes] | None = None,
        file_payloads: list[bytes | None] | None = None,
    ) -> RenderedBundle:
        file_catalog_uris = set(catalog_uris) | set(file_catalog_uris or ())
        existing_bytes = existing_bytes or {}
        file_payloads = file_payloads or []
        if len(bundle.pages) > self.limits.output_pages:
            raise ValueError("Wiki bundle exceeds the page limit")
        if len(bundle.files) > self.limits.output_files:
            raise ValueError("Wiki bundle exceeds the file limit")
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
            source_ids = list(dict.fromkeys(value.strip() for value in page.source_ids if value.strip()))
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
                hint = page.path_hint or VikingURI.sanitize_segment(title)
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
            if LinkRenderer._find_match_span(
                source_page.body_markdown,
                link.match_text,
                LinkRenderer.protected_markdown_spans(source_page.body_markdown),
            ) is None:
                raise ValueError(
                    f"WikiLink match_text is not a linkable body anchor: {link.match_text!r}"
                )

        resolved_links = resolve_wiki_links(bundle.links, page_uris, strict=True)
        page_titles = {
            page_uris[page.page_id][0]: page.title.strip() for page in bundle.pages
        }
        result = RenderedBundle()
        total_bytes = 0
        for page in bundle.pages:
            uri = page_uris[page.page_id][0]
            is_update = page.update_uri is not None
            old_raw = existing_raw.get(uri, "")
            if memory_target and is_update:
                old_memory = MemoryFileUtils.read(old_raw, uri=uri)
                old_visible = old_memory.content
            else:
                old_memory = None
                old_visible = old_raw
            old_frontmatter, old_body = _split_frontmatter(old_visible)

            outgoing = [link for link in resolved_links if link.from_uri == uri]
            incoming = [link for link in resolved_links if link.to_uri == uri]
            rendered_body, rendered_count = LinkRenderer.render_links_with_count(
                page.body_markdown.strip(),
                uri,
                [link.model_dump() for link in outgoing],
            )
            result.link_count += rendered_count
            rendered_body, inline_citations = _linkify_source_uris(
                rendered_body, source_roots
            )
            if not memory_target:
                rendered_body = _render_related_pages(
                    rendered_body,
                    page_uri=uri,
                    incoming=incoming,
                    page_titles=page_titles,
                )
            source_ids = list(dict.fromkeys(value.strip() for value in page.source_ids if value.strip()))
            rendered_body = _render_citations(
                rendered_body,
                old_body=old_body,
                source_ids=source_ids,
                source_roots=source_roots,
                inline_citations=inline_citations,
            )
            visible = _frontmatter(
                old=old_frontmatter,
                page_type=page.page_type.strip(),
                title=page.title.strip(),
                summary=page.summary.strip(),
                tags=page.tags,
            ) + rendered_body

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

            total_bytes += len(candidate.encode("utf-8"))
            if total_bytes > self.limits.output_total_bytes:
                raise ValueError("Wiki bundle exceeds the final content size limit")
            if candidate == old_raw:
                result.unchanged.append(uri)
                continue
            if is_update:
                result.updated.append(uri)
                precondition = {"kind": "replace_if_hash", "base_hash": content_hash(old_raw)}
            else:
                result.created.append(uri)
                precondition = {"kind": "create_if_absent"}
            result.operations.append(
                {"uri": uri, "content": candidate, "precondition": precondition}
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

            total_bytes += len(candidate)
            if total_bytes > self.limits.output_total_bytes:
                raise ValueError("Wiki bundle exceeds the final content size limit")
            if target_type == "resource":
                page_type = validate_declared_okf_markdown(uri, candidate)
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
                precondition = {
                    "kind": "replace_if_hash",
                    "base_hash": content_hash(old),
                }
            else:
                result.created.append(uri)
                precondition = {"kind": "create_if_absent"}
            result.operations.append(
                {"uri": uri, **operation_content, "precondition": precondition}
            )
        return result


__all__ = [
    "RenderedBundle",
    "WikiRenderer",
    "content_hash",
    "has_unclosed_frontmatter",
    "is_reserved_wiki_page_uri",
    "validate_declared_okf_markdown",
    "validate_relative_file_path",
    "validate_relative_page_path",
]
