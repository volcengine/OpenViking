"""Deterministic OKF Wiki rendering for compile bundles."""

from __future__ import annotations

import base64
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
from vikingbot.compile.models import CompileLimits, WikiBundleDraft, WikiLanguage

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


@dataclass(slots=True)
class FinalizedCheckout:
    files: dict[str, bytes] = field(default_factory=dict)
    wiki_paths: set[str] = field(default_factory=set)
    link_count: int = 0


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


def finalize_resource_checkout(
    files: Mapping[str, bytes],
    *,
    target_uri: str,
    source_roots: Mapping[str, str],
) -> FinalizedCheckout:
    """Validate and deterministically finalize one Resource checkout.

    The checkout already contains the final file layout. This pass only identifies
    self-declared OKF Wiki pages, makes supplied source URIs readable, and links the
    first body mention of another unambiguous Wiki filename. It does not decide create
    versus update.
    """
    wiki_paths: set[str] = set()
    for path, payload in files.items():
        page_type = validate_declared_okf_markdown(path, payload)
        if page_type is not None:
            text = payload.decode("utf-8")
            frontmatter, _body = _split_frontmatter(text)
            missing = [
                field
                for field in ("type", "title", "description")
                if not isinstance(frontmatter.get(field), str)
                or not str(frontmatter[field]).strip()
            ]
            if missing:
                raise ValueError(
                    f'OKF Markdown file "{path}" must have non-empty YAML frontmatter fields: '
                    + ", ".join(missing)
                )
            description = str(frontmatter["description"]).strip()
            if "\n" in description or "\r" in description:
                raise ValueError(
                    f'OKF Markdown file "{path}" frontmatter description must be one line'
                )
            wiki_paths.add(path)

    wiki_uris = {safe_join_viking_uri(target_uri, path).rstrip("/") for path in wiki_paths}
    mention_targets = _wiki_mention_targets(wiki_uris)
    finalized = dict(files)
    link_count = 0
    for path in sorted(wiki_paths):
        payload = files[path]
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f'OKF Markdown file "{path}" must be UTF-8') from exc
        uri = safe_join_viking_uri(target_uri, path).rstrip("/")
        frontmatter = _FRONTMATTER_RE.match(content)
        if frontmatter:
            content = content[: frontmatter.end()] + _linkify_source_uris(
                content[frontmatter.end() :], source_roots
            )
        else:
            content = _linkify_source_uris(content, source_roots)
        content, rendered_count = _link_wiki_mentions(
            content,
            source_uri=uri,
            targets=mention_targets,
        )
        finalized[path] = content.encode("utf-8")
        link_count += rendered_count

    return FinalizedCheckout(
        files=finalized,
        wiki_paths=wiki_paths,
        link_count=link_count,
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
        wiki_language: WikiLanguage | None = None,
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
        if len(bundle.pages) + len(bundle.files) > self.limits.output_operations:
            raise ValueError("Wiki bundle exceeds the combined output operation limit")
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
        total_bytes = 0
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

            total_bytes += len(candidate.encode("utf-8"))
            if total_bytes > self.limits.output_total_bytes:
                raise ValueError("Wiki bundle exceeds the final content size limit")
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
                total_bytes += len(candidate.encode("utf-8"))
                if total_bytes > self.limits.output_total_bytes:
                    raise ValueError("Wiki bundle exceeds the final content size limit")
                result.updated.append(uri)
                result.wiki_uris.append(uri)
                result.operations.append(
                    {
                        "uri": uri,
                        "content": candidate,
                        "mode": "upsert",
                    }
                )
            if len(result.created) + len(result.updated) > self.limits.output_pages:
                raise ValueError("Wiki mention linking exceeds the page limit")

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
        if len(result.operations) > self.limits.output_operations:
            raise ValueError("Wiki bundle exceeds the combined output operation limit")
        return result


__all__ = [
    "FinalizedCheckout",
    "RenderedBundle",
    "WikiRenderer",
    "finalize_resource_checkout",
    "has_unclosed_frontmatter",
    "strip_okf_frontmatter",
    "is_reserved_wiki_page_uri",
    "validate_declared_okf_markdown",
    "validate_relative_file_path",
    "validate_relative_page_path",
    "wiki_page_path_from_title",
]
