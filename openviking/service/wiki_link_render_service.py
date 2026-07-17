# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Render Wiki page links into Resource Markdown without persisting graph metadata."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import unquote

from openviking.core.namespace import classify_uri, uri_parts
from openviking.server.identity import RequestContext
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_SIDECAR_NAMES = (".overview.md", ".abstract.md")
_MEMORY_FIELDS_RE = re.compile(r"\n*<!--\s*MEMORY_FIELDS\b.*?-->", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[(?P<text>[^\]]+)\]\((?P<target>(?:[^()\n]|\([^()\n]*\))+?)\)"
)
_ATX_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+.*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(?P<ticks>`+).*?(?P=ticks)", re.DOTALL)
_FENCE_START_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
_H1_RE = re.compile(r"(?m)^[ \t]{0,3}#[ \t]+(?P<name>.+?)[ \t]*$")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _fold_char(char: str) -> str:
    if "A" <= char <= "Z":
        return chr(ord(char) + 32)
    return char


def _fold_text(text: str) -> str:
    return "".join(_fold_char(char) for char in text)


@dataclass(frozen=True)
class _WikiPageAlias:
    text: str
    uri: str


@dataclass
class _AutomatonNode:
    transitions: Dict[str, int] = field(default_factory=dict)
    failure: int = 0
    outputs: List[_WikiPageAlias] = field(default_factory=list)


class _WikiPageMatcher:
    """Small standard-library Aho-Corasick matcher."""

    def __init__(self, aliases: Iterable[_WikiPageAlias]):
        self._nodes = [_AutomatonNode()]
        for alias in aliases:
            self._insert(alias)
        self._build_failures()

    def _insert(self, alias: _WikiPageAlias) -> None:
        state = 0
        for char in _fold_text(alias.text):
            state = self._nodes[state].transitions.setdefault(char, len(self._nodes))
            if state == len(self._nodes):
                self._nodes.append(_AutomatonNode())
        self._nodes[state].outputs.append(alias)

    def _build_failures(self) -> None:
        queue: deque[int] = deque(self._nodes[0].transitions.values())
        while queue:
            state = queue.popleft()
            for char, child in self._nodes[state].transitions.items():
                queue.append(child)
                failure = self._nodes[state].failure
                while failure and char not in self._nodes[failure].transitions:
                    failure = self._nodes[failure].failure
                self._nodes[child].failure = self._nodes[failure].transitions.get(char, 0)
                self._nodes[child].outputs.extend(self._nodes[self._nodes[child].failure].outputs)

    def find(self, text: str) -> List[tuple[int, int, _WikiPageAlias]]:
        state = 0
        matches: List[tuple[int, int, _WikiPageAlias]] = []
        for index, raw_char in enumerate(text):
            char = _fold_char(raw_char)
            while state and char not in self._nodes[state].transitions:
                state = self._nodes[state].failure
            state = self._nodes[state].transitions.get(char, 0)
            for alias in self._nodes[state].outputs:
                start = index - len(alias.text) + 1
                if _has_valid_boundaries(text, start, index + 1, alias.text):
                    matches.append((start, index + 1, alias))
        return matches


def _has_valid_boundaries(text: str, start: int, end: int, alias: str) -> bool:
    if _CJK_RE.search(alias):
        return True
    left = text[start - 1] if start > 0 else ""
    right = text[end] if end < len(text) else ""
    return not _ASCII_WORD_RE.fullmatch(left or " ") and not _ASCII_WORD_RE.fullmatch(right or " ")


def _relative_path(source_uri: str, target_uri: str) -> str:
    source = uri_parts(source_uri)
    target = uri_parts(target_uri)
    common = 0
    for source_part, target_part in zip(source, target, strict=False):
        if source_part != target_part:
            break
        common += 1
    up = [".."] * max(0, len(source) - common - 1)
    down = target[common:]
    return "/".join(up + down) or "./"


def _resolve_link_target(source_uri: str, target: str) -> Optional[str]:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if target.startswith("viking://"):
        return "viking://" + "/".join(uri_parts(target))
    if not target or "://" in target or target.startswith("/"):
        return None

    parts = uri_parts(source_uri)[:-1]
    for part in target.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "viking://" + "/".join(parts)


def _is_wiki_page_target(source_uri: str, target: str, wiki_pages_root: str) -> bool:
    resolved = _resolve_link_target(source_uri, target)
    if not resolved:
        return False
    root_parts = uri_parts(wiki_pages_root)
    try:
        root_parts = root_parts[root_parts.index("memories") :]
    except ValueError:
        pass
    resolved_parts = uri_parts(resolved)
    return any(
        resolved_parts[index : index + len(root_parts)] == root_parts
        for index in range(len(resolved_parts) - len(root_parts) + 1)
    )


def _strip_previous_wiki_links(content: str, source_uri: str, wiki_pages_root: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if _is_wiki_page_target(source_uri, match.group("target"), wiki_pages_root):
            return match.group("text")
        return match.group(0)

    return _MARKDOWN_LINK_RE.sub(replace, content)


def _fenced_code_spans(content: str) -> List[tuple[int, int]]:
    spans: List[tuple[int, int]] = []
    opening: Optional[tuple[int, str, int]] = None
    offset = 0
    for line in content.splitlines(keepends=True):
        match = _FENCE_START_RE.match(line)
        if opening is None and match:
            fence = match.group("fence")
            opening = (offset, fence[0], len(fence))
        elif opening is not None and match:
            fence = match.group("fence")
            if fence[0] == opening[1] and len(fence) >= opening[2]:
                spans.append((opening[0], offset + len(line)))
                opening = None
        offset += len(line)
    if opening is not None:
        spans.append((opening[0], len(content)))
    return spans


def _protected_prefix(content: str) -> List[int]:
    spans = _fenced_code_spans(content)
    for pattern in (_INLINE_CODE_RE, _HTML_COMMENT_RE, _ATX_HEADING_RE, _MARKDOWN_LINK_RE):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(content))

    delta = [0] * (len(content) + 1)
    for start, end in spans:
        delta[start] += 1
        delta[end] -= 1
    prefix = [0] * (len(content) + 1)
    active = 0
    for index in range(len(content)):
        active += delta[index]
        prefix[index + 1] = prefix[index] + int(active > 0)
    return prefix


def _render_content(
    raw_content: str,
    source_uri: str,
    matcher: Optional[_WikiPageMatcher],
    wiki_pages_root: str,
) -> tuple[str, int]:
    content = _MEMORY_FIELDS_RE.sub("", raw_content)
    content = _strip_previous_wiki_links(content, source_uri, wiki_pages_root)
    if matcher is None:
        return content, 0

    protected = _protected_prefix(content)
    candidates_by_start: Dict[int, List[tuple[int, _WikiPageAlias]]] = {}
    for start, end, alias in matcher.find(content):
        candidates_by_start.setdefault(start, []).append((end, alias))
    claimed_uris = set()
    replacements: List[tuple[int, int, str]] = []
    covered_until = -1
    for start in range(len(content)):
        if start < covered_until:
            continue
        selected: Optional[tuple[int, _WikiPageAlias]] = None
        for end, alias in candidates_by_start.get(start, []):
            if alias.uri in claimed_uris or protected[end] != protected[start]:
                continue
            if selected is None or (end - start, alias.uri) > (
                selected[0] - start,
                selected[1].uri,
            ):
                selected = (end, alias)
        if selected is None:
            continue
        end, alias = selected
        target = (
            _relative_path(source_uri, alias.uri)
            .replace(" ", "%20")
            .replace("(", "%28")
            .replace(")", "%29")
        )
        replacements.append((start, end, f"[{content[start:end]}]({target})"))
        claimed_uris.add(alias.uri)
        covered_until = end

    result = content
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]
    return result, len(replacements)


def _resource_directories(resource_uri: str) -> List[str]:
    parts = uri_parts(resource_uri)
    classification = classify_uri(resource_uri)
    root_depth = 1 if parts[:1] == ["resources"] else (classification.content_index or 0) + 1
    return [
        "viking://" + "/".join(parts[:depth]) for depth in range(len(parts), root_depth - 1, -1)
    ]


class WikiLinkRenderService:
    """Render Wiki page links into imported Markdown and Resource sidecars."""

    def __init__(self, viking_fs: VikingFS, *, read_concurrency: int = 16):
        self._viking_fs = viking_fs
        self._read_concurrency = max(1, read_concurrency)

    async def render(
        self,
        *,
        ctx: RequestContext,
        resource_uri: str,
        wiki_pages_root: str,
    ) -> Dict[str, int]:
        aliases, page_count = await self._load_aliases(ctx=ctx, wiki_pages_root=wiki_pages_root)
        matcher = _WikiPageMatcher(aliases) if aliases else None
        updated = 0
        links_created = 0
        files_seen = 0

        try:
            result = await self._viking_fs.glob("**/*.md", uri=resource_uri, ctx=ctx)
            render_uris = [
                str(uri)
                for uri in result.get("matches", [])
                if not str(uri).rstrip("/").rsplit("/", 1)[-1].startswith(".")
            ]
        except (NotFoundError, FileNotFoundError, KeyError):
            render_uris = []
        except Exception as exc:
            logger.warning("Failed to list Resource Markdown under %s: %s", resource_uri, exc)
            render_uris = []

        for directory_uri in _resource_directories(resource_uri):
            for sidecar_name in _SIDECAR_NAMES:
                render_uris.append(f"{directory_uri}/{sidecar_name}")

        for render_uri in dict.fromkeys(render_uris):
            try:
                raw = await self._viking_fs.read_file(render_uri, ctx=ctx)
            except (NotFoundError, FileNotFoundError, KeyError):
                continue
            except Exception as exc:
                logger.warning("Failed to read Resource Markdown %s: %s", render_uri, exc)
                continue
            files_seen += 1
            rendered, link_count = _render_content(str(raw), render_uri, matcher, wiki_pages_root)
            links_created += link_count
            if rendered == raw:
                continue
            await self._viking_fs.write_file(render_uri, rendered, ctx=ctx)
            updated += 1
        return {
            "wiki_pages_scanned": page_count,
            "files_seen": files_seen,
            "files_updated": updated,
            "links_created": links_created,
        }

    async def _load_aliases(
        self,
        *,
        ctx: RequestContext,
        wiki_pages_root: str,
    ) -> tuple[List[_WikiPageAlias], int]:
        try:
            result = await self._viking_fs.glob("**/*.md", uri=wiki_pages_root, ctx=ctx)
        except (NotFoundError, FileNotFoundError, KeyError):
            return [], 0
        uris = [
            str(uri)
            for uri in result.get("matches", [])
            if str(uri).endswith(".md")
            and not str(uri).rstrip("/").rsplit("/", 1)[-1].startswith(".")
        ]
        semaphore = asyncio.Semaphore(self._read_concurrency)

        async def read_aliases(uri: str) -> tuple[str, Sequence[str]]:
            async with semaphore:
                try:
                    raw = await self._viking_fs.read_file(uri, ctx=ctx)
                except Exception as exc:
                    logger.warning("Failed to read Entity page %s: %s", uri, exc)
                    return uri, []
            memory_file = MemoryFileUtils.read(str(raw), uri=uri)
            names = [str(memory_file.extra_fields.get("name") or "").strip()]
            heading = _H1_RE.search(memory_file.content)
            if heading:
                names.append(heading.group("name").strip().strip("*_`"))
            names.append(unquote(uri.rsplit("/", 1)[-1].removesuffix(".md")))
            return uri, list(dict.fromkeys(name for name in names if name))

        loaded = await asyncio.gather(*(read_aliases(uri) for uri in uris))
        alias_targets: Dict[str, set[str]] = {}
        alias_text: Dict[str, str] = {}
        for uri, names in loaded:
            for name in names:
                key = _fold_text(name)
                alias_targets.setdefault(key, set()).add(uri)
                alias_text.setdefault(key, name)

        aliases = [
            _WikiPageAlias(text=alias_text[key], uri=next(iter(targets)))
            for key, targets in alias_targets.items()
            if len(targets) == 1
        ]
        return aliases, len(uris)
