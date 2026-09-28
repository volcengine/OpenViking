# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OKF documents and atomic writeback for generated abstract overviews.

Only .abstract.md and .overview.md use this module. Ordinary Markdown keeps
its frontmatter as user content and is never parsed implicitly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional, Sequence, TypeVar
from urllib.parse import quote, unquote

import yaml

from openviking.core.context import ContextLevel
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessDecision

logger = get_logger(__name__)

ABSTRACT_OVERVIEW_FILENAMES = frozenset({".abstract.md", ".overview.md"})
EMBEDDING_METADATA_FIELDS = ("directory",)
_METADATA_ORDER = ("directory", "source", "generated_by", "freshness")
_MARKDOWN_URI_SAFE_ASCII = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/:"
)
_MAX_SOURCE_URI_CHARS = 4096
_MAX_LABEL_CHARS = 128
_T = TypeVar("_T")
_VIKING_URI_BODY_DELIMITERS = frozenset(" \t\r\n)]}>,'\"`")
_VIKING_URI_TRAILING_PUNCTUATION = frozenset(".,;:!?")


class AbstractOverviewFormatError(ValueError):
    """A generated abstract overview has invalid OKF frontmatter."""


class AbstractOverviewMetadataError(AbstractOverviewFormatError):
    """A public write attempted to change protected sidecar metadata."""


@dataclass(frozen=True)
class AbstractOverviewDocument:
    """Parsed machine metadata and visible Markdown body."""

    metadata: Dict[str, Any]
    body: str
    legacy: bool = False


@dataclass(frozen=True)
class AbstractOverviewWriteResult:
    """Visible-body changes produced by one successful sidecar writeback."""

    wrote: bool
    overview_body_changed: bool = False
    abstract_body_changed: bool = False

    def __bool__(self) -> bool:
        return self.wrote


def is_abstract_overview_uri(uri: str) -> bool:
    """Return whether uri names one of the two generated sidecars."""

    return uri.rstrip("/").rsplit("/", 1)[-1] in ABSTRACT_OVERVIEW_FILENAMES


def _normalize_directory_uri(dir_uri: str) -> str:
    value = str(dir_uri or "").strip()
    if not value:
        raise AbstractOverviewFormatError("abstract overview directory must not be empty")
    if not value.startswith("viking://"):
        raise AbstractOverviewFormatError("abstract overview directory must be a viking:// URI")
    return value.rstrip("/") + "/"


def _bounded_string(value: Any, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AbstractOverviewFormatError(f"abstract overview {field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise AbstractOverviewFormatError(
            f"abstract overview {field} exceeds the {limit}-character policy limit"
        )
    return normalized


def _normalize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate known fields and silently discard fields outside the schema.

    Ignoring unknown fields keeps readers forward-compatible with metadata
    written by newer OpenViking versions.  Known fields remain strict so a
    malformed value cannot leak into previews, embeddings, or writeback.
    """

    normalized: Dict[str, Any] = {}
    if "directory" in metadata:
        normalized["directory"] = _normalize_directory_uri(str(metadata["directory"]))

    source = metadata.get("source")
    if source is not None:
        if not isinstance(source, Mapping) or not {"kind", "uri"}.issubset(source):
            raise AbstractOverviewFormatError("abstract overview source must contain kind and uri")
        normalized["source"] = {
            "kind": _bounded_string(source["kind"], field="source.kind", limit=_MAX_LABEL_CHARS),
            "uri": _bounded_string(source["uri"], field="source.uri", limit=_MAX_SOURCE_URI_CHARS),
        }

    generated_by = metadata.get("generated_by")
    if generated_by is not None:
        if not isinstance(generated_by, Mapping) or not {
            "component",
            "trigger",
        }.issubset(generated_by):
            raise AbstractOverviewFormatError(
                "abstract overview generated_by must contain component and trigger"
            )
        normalized["generated_by"] = {
            "component": _bounded_string(
                generated_by["component"],
                field="generated_by.component",
                limit=_MAX_LABEL_CHARS,
            ),
            "trigger": _bounded_string(
                generated_by["trigger"],
                field="generated_by.trigger",
                limit=_MAX_LABEL_CHARS,
            ),
        }

    freshness = metadata.get("freshness")
    if freshness is not None:
        required = {
            "total_entries",
            "sampled_entries",
            "unsampled_entries",
            "pending_child_changes",
        }
        if not isinstance(freshness, Mapping) or not required.issubset(freshness):
            raise AbstractOverviewFormatError(
                "abstract overview freshness has an invalid field set"
            )
        counters: Dict[str, int] = {}
        for field in (
            "total_entries",
            "sampled_entries",
            "unsampled_entries",
            "pending_child_changes",
        ):
            value = freshness[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AbstractOverviewFormatError(
                    f"abstract overview freshness.{field} must be a non-negative integer"
                )
            counters[field] = value
        if "missing_summary_entries" in freshness:
            value = freshness["missing_summary_entries"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AbstractOverviewFormatError(
                    "abstract overview freshness.missing_summary_entries "
                    "must be a non-negative integer"
                )
            counters["missing_summary_entries"] = value
        if counters["sampled_entries"] + counters["unsampled_entries"] != counters["total_entries"]:
            raise AbstractOverviewFormatError(
                "abstract overview freshness sampled + unsampled must equal total"
            )
        normalized["freshness"] = counters

    return {field: normalized[field] for field in _METADATA_ORDER if field in normalized}


def parse_abstract_overview(raw: str | bytes) -> AbstractOverviewDocument:
    """Parse OKF Markdown while accepting frontmatter-free legacy content.

    A leading delimiter opts a generated sidecar into strict OKF parsing.
    Invalid YAML, non-object YAML, and missing closing delimiters fail loudly.
    """

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AbstractOverviewFormatError("abstract overview is not valid UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("abstract overview content must be str or bytes")

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return AbstractOverviewDocument(metadata={}, body=text, legacy=True)

    closing_index: Optional[int] = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing_index = index
            break
    if closing_index is None:
        raise AbstractOverviewFormatError("abstract overview frontmatter is not closed")

    try:
        loaded = yaml.safe_load("".join(lines[1:closing_index]))
    except yaml.YAMLError as exc:
        raise AbstractOverviewFormatError(f"invalid abstract overview YAML: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, Mapping):
        raise AbstractOverviewFormatError("abstract overview frontmatter must be a YAML object")
    if "directory" not in loaded:
        raise AbstractOverviewFormatError("abstract overview frontmatter must contain directory")

    body = "".join(lines[closing_index + 1 :])
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return AbstractOverviewDocument(metadata=_normalize_metadata(loaded), body=body, legacy=False)


def render_abstract_overview(
    level: int | ContextLevel,
    dir_uri: str,
    body: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render deterministic OKF Markdown for an L0 or L1 sidecar."""

    try:
        resolved_level = ContextLevel(int(level))
    except (TypeError, ValueError) as exc:
        raise AbstractOverviewFormatError(f"invalid abstract overview level: {level}") from exc
    if resolved_level not in {ContextLevel.ABSTRACT, ContextLevel.OVERVIEW}:
        raise AbstractOverviewFormatError("abstract overviews only support L0 and L1")
    if not isinstance(body, str):
        raise TypeError("abstract overview body must be a string")

    merged: Dict[str, Any] = dict(metadata or {})
    merged["directory"] = _normalize_directory_uri(dir_uri)
    frontmatter = yaml.safe_dump(
        _normalize_metadata(merged),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def markdown_safe_viking_uri(uri: str) -> str:
    """Encode ASCII-only URI syntax hazards while preserving Unicode path text."""

    if not isinstance(uri, str):
        raise TypeError("URI must be a string")
    return "".join(
        char if ord(char) > 127 or char in _MARKDOWN_URI_SAFE_ASCII else f"%{ord(char):02X}"
        for char in uri
    )


def _normalize_markdown_viking_uris(text: str) -> str:
    """Normalize embedded Viking URIs to the canonical Markdown-safe form."""

    normalized: list[str] = []
    cursor = 0
    while True:
        start = text.find("viking://", cursor)
        if start < 0:
            normalized.append(text[cursor:])
            return "".join(normalized)
        normalized.append(text[cursor:start])
        end = start + len("viking://")
        while end < len(text) and text[end] not in _VIKING_URI_BODY_DELIMITERS:
            end += 1
        raw_uri = text[start:end]
        trailing = ""
        while raw_uri and raw_uri[-1] in _VIKING_URI_TRAILING_PUNCTUATION:
            trailing = raw_uri[-1] + trailing
            raw_uri = raw_uri[:-1]
        normalized.append(markdown_safe_viking_uri(unquote(raw_uri)))
        normalized.append(trailing)
        cursor = end


def rewrite_viking_uri_references(text: str, source_uri: str, target_uri: str) -> str:
    """Rewrite generated raw or URL-encoded URI references within one transfer scope."""

    if not isinstance(text, str):
        raise TypeError("abstract overview body must be a string")
    source = source_uri.rstrip("/")
    target = target_uri.rstrip("/")
    if not source or source == target:
        return text

    markdown_safe_target = markdown_safe_viking_uri(target)
    variants = (
        (quote(source, safe=":/"), markdown_safe_target),
        (markdown_safe_viking_uri(source), markdown_safe_target),
        (source, markdown_safe_target),
    )
    rewritten = text
    seen: set[str] = set()
    # Generated summaries contain Markdown links and occasional URI prose.  A
    # boundary is required so moving ``.../foo`` never rewrites ``.../foobar``.
    suffix_boundary = r"(?=$|[/#\s)\]}>,'\"`.,;:!?])"
    for old, new in variants:
        if old in seen:
            continue
        seen.add(old)
        rewritten = re.sub(
            re.escape(old) + suffix_boundary,
            lambda _match, replacement=new: replacement,
            rewritten,
        )
    return _normalize_markdown_viking_uris(rewritten)


def rewrite_abstract_overview_for_transfer(
    raw: str | bytes,
    *,
    level: int | ContextLevel,
    source_dir_uri: str,
    target_dir_uri: str,
    source_scope_uri: Optional[str] = None,
    target_scope_uri: Optional[str] = None,
) -> str:
    """Retarget generated sidecar metadata and body links without regeneration."""

    document = parse_abstract_overview(raw)
    body = rewrite_viking_uri_references(
        document.body,
        source_scope_uri or source_dir_uri,
        target_scope_uri or target_dir_uri,
    )
    if document.legacy:
        return body
    return render_abstract_overview(level, target_dir_uri, body, document.metadata)


def prepare_abstract_overview_write(
    uri: str,
    current_raw: str | bytes,
    requested_raw: str | bytes,
    *,
    mode: str = "replace",
) -> str:
    """Protect metadata while applying a public body write.

    Callers may round-trip the full raw document, but any supplied metadata
    must equal the stored metadata after schema normalization.  A body-only
    request inherits the stored metadata.  The result is always rendered in
    canonical OKF form so metadata cannot be removed by omission.

    This helper assumes the target already exists.  Creating a generated
    sidecar through the public write API is intentionally rejected by the
    coordinator because there is no trusted metadata baseline to preserve.
    """

    if mode not in {"replace", "append"}:
        raise ValueError(f"unsupported abstract overview write mode: {mode}")
    if not is_abstract_overview_uri(uri):
        raise ValueError(f"not an abstract overview URI: {uri}")

    current = parse_abstract_overview(current_raw)
    requested = parse_abstract_overview(requested_raw)
    if not requested.legacy and requested.metadata != current.metadata:
        raise AbstractOverviewMetadataError("cannot modify protected abstract overview metadata")

    requested_body = requested.body
    body = current.body + requested_body if mode == "append" else requested_body
    filename = uri.rstrip("/").rsplit("/", 1)[-1]
    level = ContextLevel.ABSTRACT if filename == ".abstract.md" else ContextLevel.OVERVIEW
    # Preserve even a historically inconsistent stored directory value.  A
    # public body edit must never repair or otherwise mutate protected
    # metadata as a side effect.  Legacy sidecars have no metadata baseline,
    # so their first body edit naturally migrates them using the target URI.
    dir_uri = current.metadata.get("directory") or uri.rstrip("/").rsplit("/", 1)[0]
    return render_abstract_overview(level, dir_uri, body, current.metadata)


def body_for_preview(raw: str | bytes) -> str:
    """Return only user-visible Markdown from an abstract overview."""

    document = parse_abstract_overview(raw)
    if document.legacy:
        return document.body
    # Rendering adds one canonical terminal newline to the stored document.
    # It is a serialization detail, not part of semantic accessor output.
    return document.body.rstrip("\r\n")


def body_for_embedding(
    raw: str | bytes,
    whitelist: Sequence[str] = EMBEDDING_METADATA_FIELDS,
) -> str:
    """Return body plus explicitly whitelisted stable metadata."""

    document = parse_abstract_overview(raw)
    selected = {
        field: document.metadata[field] for field in whitelist if field in document.metadata
    }
    if not selected:
        return document.body
    frontmatter = yaml.safe_dump(
        selected,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    return f"---\n{frontmatter}\n---\n\n{document.body.strip()}"


def embedding_text_for_body(level: int | ContextLevel, dir_uri: str, body: str) -> str:
    """Build embedding text for a freshly generated visible body."""

    return body_for_embedding(render_abstract_overview(level, dir_uri, body))


def semantic_body_digest(body: str) -> str:
    """Hash normalized visible Markdown, excluding all OKF metadata."""

    normalized = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deterministic_sample(items: Sequence[_T], limit: int) -> list[_T]:
    """Select a stable, order-preserving sample spanning the full sequence."""

    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    indices = [(index * (len(items) - 1)) // (limit - 1) for index in range(limit)]
    return [items[index] for index in indices]


def freshness_metadata(
    total_entries: int,
    sampled_entries: int,
    pending: int = 0,
    missing_summary_entries: Optional[int] = None,
) -> Dict[str, int]:
    """Create validated direct-child freshness counters."""

    freshness = {
        "total_entries": total_entries,
        "sampled_entries": sampled_entries,
        "unsampled_entries": total_entries - sampled_entries,
        "pending_child_changes": pending,
    }
    if missing_summary_entries is not None:
        freshness["missing_summary_entries"] = missing_summary_entries
    return _normalize_metadata({"freshness": freshness})["freshness"]


async def _read_existing_document(
    viking_fs: Any, uri: str, ctx: Optional[RequestContext]
) -> Optional[AbstractOverviewDocument]:
    raw = await _raw_if_exists(viking_fs, uri, ctx)
    try:
        return parse_abstract_overview(raw) if raw is not None else None
    except AbstractOverviewFormatError as exc:
        logger.warning("[Semantic] Ignoring malformed sidecar %s: %s", uri, exc)
        return None


async def write_abstract_overview(
    *,
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    is_stale: Callable[[], bool],
    metadata: Optional[Mapping[str, Any]] = None,
    consume_pending: Optional[int] = None,
    lock: Optional[Dict[str, Any]] = None,
    log_prefix: str = "[Semantic]",
    existing_raw: Optional[Mapping[int, str | bytes]] = None,
) -> AbstractOverviewWriteResult:
    """Render and atomically write sidecars while preserving source metadata.

    Exact equality is checked under the lock so stable refreshes do not touch
    modification times or create noisy Git diffs.
    """

    if is_stale():
        logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
        return AbstractOverviewWriteResult(wrote=False)

    overview_uri = f"{dir_uri}/.overview.md"
    abstract_uri = f"{dir_uri}/.abstract.md"
    lock_paths = [
        viking_fs._uri_to_path(overview_uri, ctx=ctx),
        viking_fs._uri_to_path(abstract_uri, ctx=ctx),
    ]
    sidecar_lease = lock
    owns_lease = sidecar_lease is None
    if sidecar_lease is None:
        sidecar_lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(lock_paths)
    try:
        if is_stale():
            logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
            return AbstractOverviewWriteResult(wrote=False)

        existing_overview = (
            parse_abstract_overview(existing_raw[1])
            if existing_raw is not None and 1 in existing_raw
            else await _read_existing_document(viking_fs, overview_uri, ctx)
            if existing_raw is None
            else None
        )
        existing_abstract = (
            parse_abstract_overview(existing_raw[0])
            if existing_raw is not None and 0 in existing_raw
            else await _read_existing_document(viking_fs, abstract_uri, ctx)
            if existing_raw is None
            else None
        )
        merged_metadata = dict(metadata or {})
        for existing in (existing_overview, existing_abstract):
            if existing and "source" in existing.metadata and "source" not in merged_metadata:
                merged_metadata["source"] = existing.metadata["source"]
                break

        # A refresh consumes only the counter observed when that refresh
        # started. Changes arriving during generation remain pending.
        requested_freshness = merged_metadata.get("freshness")
        if isinstance(requested_freshness, Mapping):
            current_pending = 0
            for existing in (existing_overview, existing_abstract):
                freshness = existing.metadata.get("freshness") if existing else None
                if isinstance(freshness, Mapping):
                    current_pending = max(
                        current_pending, int(freshness.get("pending_child_changes", 0))
                    )
            next_freshness = dict(requested_freshness)
            consumed = current_pending if consume_pending is None else max(consume_pending, 0)
            next_freshness["pending_child_changes"] = max(current_pending - consumed, 0)
            merged_metadata["freshness"] = next_freshness

        overview_body_changed = existing_overview is None or semantic_body_digest(
            existing_overview.body
        ) != semantic_body_digest(overview)
        abstract_body_changed = existing_abstract is None or semantic_body_digest(
            existing_abstract.body
        ) != semantic_body_digest(abstract)

        rendered_overview = render_abstract_overview(
            ContextLevel.OVERVIEW, dir_uri, overview, merged_metadata
        )
        rendered_abstract = render_abstract_overview(
            ContextLevel.ABSTRACT, dir_uri, abstract, merged_metadata
        )
        current_overview = (
            existing_raw.get(1)
            if existing_raw is not None
            else await _raw_if_exists(viking_fs, overview_uri, ctx)
        )
        current_abstract = (
            existing_raw.get(0)
            if existing_raw is not None
            else await _raw_if_exists(viking_fs, abstract_uri, ctx)
        )
        if isinstance(current_overview, bytes):
            current_overview = current_overview.decode("utf-8")
        if isinstance(current_abstract, bytes):
            current_abstract = current_abstract.decode("utf-8")

        if current_overview != rendered_overview:
            await viking_fs.write_file(
                overview_uri, rendered_overview, ctx=ctx, lease_ref=sidecar_lease
            )
        if current_abstract != rendered_abstract:
            await viking_fs.write_file(
                abstract_uri, rendered_abstract, ctx=ctx, lease_ref=sidecar_lease
            )
        return AbstractOverviewWriteResult(
            wrote=True,
            overview_body_changed=overview_body_changed,
            abstract_body_changed=abstract_body_changed,
        )
    finally:
        if owns_lease:
            await viking_fs._async_agfs.pathlock_release(sidecar_lease)


async def _raw_if_exists(viking_fs: Any, uri: str, ctx: Optional[RequestContext]) -> Optional[str]:
    read_file = getattr(viking_fs, "read_file", None)
    if read_file is None:
        return None
    try:
        return await read_file(uri, ctx=ctx)
    except KeyError:
        # Lightweight storage doubles commonly expose an in-memory mapping and
        # use KeyError for a missing optional sidecar. Production backends use
        # their regular not-found exception, handled below.
        return None
    except Exception as exc:
        if is_not_found_error(exc):
            return None
        raise


async def plan_abstract_overview_refresh(
    *,
    viking_fs: Any,
    dir_uri: str,
    changed_entries: int,
    ctx: Optional[RequestContext],
    l0_body_changed: bool = True,
    force_refresh: bool = False,
    overview_sample_limit: int = 32,
    refresh_ratio: float = 0.10,
    lock_timeout_secs: float = 0.0,
) -> FreshnessDecision:
    """Atomically record direct-child changes and choose refresh scheduling.

    Both sidecars receive the same counter snapshot under one exact-path lease.
    A missing or legacy baseline is refreshed immediately and migrated by the
    ensuing semantic task.
    """
    # Lazy import keeps this low-level sidecar module importable by VikingFS
    # while queuefs itself imports VikingFS during package initialization.
    from openviking.storage.queuefs.semantic_ops.freshness_policy import (
        FreshnessAction,
        FreshnessDecision,
        decide_parent_refresh,
    )

    if changed_entries <= 0 or not l0_body_changed:
        return FreshnessDecision(FreshnessAction.NOOP, 0, 0)
    uris = [
        f"{dir_uri.rstrip('/')}/.overview.md",
        f"{dir_uri.rstrip('/')}/.abstract.md",
    ]
    # Missing/legacy sidecars have no counter to update and must refresh now.
    # Avoid acquiring exact paths in that common migration case; if a baseline
    # appears immediately afterwards, the queued full refresh safely replaces it.
    preliminary = [await _read_existing_document(viking_fs, uri, ctx) for uri in uris]
    if not all(
        document is not None
        and not document.legacy
        and isinstance(document.metadata.get("freshness"), Mapping)
        for document in preliminary
    ):
        return decide_parent_refresh(
            l0_body_changed=True,
            has_freshness_baseline=False,
            total_entries=0,
            pending_before=0,
            current_change_count=changed_entries,
            overview_sample_limit=overview_sample_limit,
            refresh_ratio=refresh_ratio,
            force_refresh=force_refresh,
        )
    lock_paths = [viking_fs._uri_to_path(uri, ctx=ctx) for uri in uris]
    lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(
        lock_paths, timeout_secs=lock_timeout_secs
    )
    try:
        documents = [await _read_existing_document(viking_fs, uri, ctx) for uri in uris]
        baselines = [
            document.metadata["freshness"]
            for document in documents
            if document is not None
            and not document.legacy
            and isinstance(document.metadata.get("freshness"), Mapping)
        ]
        if len(baselines) != len(uris):
            return decide_parent_refresh(
                l0_body_changed=True,
                has_freshness_baseline=False,
                total_entries=0,
                pending_before=0,
                current_change_count=changed_entries,
                overview_sample_limit=overview_sample_limit,
                refresh_ratio=refresh_ratio,
                force_refresh=force_refresh,
            )

        baseline = baselines[0]
        decision = decide_parent_refresh(
            l0_body_changed=True,
            has_freshness_baseline=True,
            total_entries=int(baseline["total_entries"]),
            # A mismatch should not normally exist, but preserving the larger
            # counter is safer than losing an already-observed change.
            pending_before=max(int(item["pending_child_changes"]) for item in baselines),
            current_change_count=changed_entries,
            overview_sample_limit=overview_sample_limit,
            refresh_ratio=refresh_ratio,
            force_refresh=force_refresh,
        )
        for level, uri, document in zip(
            (ContextLevel.OVERVIEW, ContextLevel.ABSTRACT), uris, documents, strict=True
        ):
            if document is None or document.legacy:
                continue
            freshness = document.metadata.get("freshness")
            if not isinstance(freshness, Mapping):
                continue
            metadata = dict(document.metadata)
            updated_freshness = dict(freshness)
            updated_freshness["pending_child_changes"] = decision.pending_after
            metadata["freshness"] = updated_freshness
            rendered = render_abstract_overview(level, dir_uri, document.body, metadata)
            raw = await _raw_if_exists(viking_fs, uri, ctx)
            if rendered != raw:
                await viking_fs.write_file(uri, rendered, ctx=ctx, lease_ref=lease)
        return decision
    finally:
        await viking_fs._async_agfs.pathlock_release(lease)


async def read_abstract_overview_pending_snapshot(
    *,
    viking_fs: Any,
    dir_uri: str,
    ctx: Optional[RequestContext],
    lock: Optional[Dict[str, Any]] = None,
    existing_raw: Optional[Mapping[int, str | bytes]] = None,
) -> int:
    """Read the pending counter captured at the start of an aggregation."""

    uris = [
        f"{dir_uri.rstrip('/')}/.overview.md",
        f"{dir_uri.rstrip('/')}/.abstract.md",
    ]

    def _pending_of(documents: Sequence[Optional[AbstractOverviewDocument]]) -> int:
        pending_values = []
        for document in documents:
            freshness = document.metadata.get("freshness") if document else None
            if document is not None and not document.legacy and isinstance(freshness, Mapping):
                pending_values.append(int(freshness["pending_child_changes"]))
        return max(pending_values, default=0)

    if existing_raw is not None:
        return _pending_of(
            [
                parse_abstract_overview(existing_raw[level]) if level in existing_raw else None
                for level in (1, 0)
            ]
        )

    owns_lease = lock is None
    snapshot_lease = lock
    if owns_lease:
        # Read-only: when neither sidecar exists there is no counter to
        # snapshot, and acquiring exact locks there would create the parent
        # directory just to hold lock metadata (a deleted directory reappears).
        preliminary = [await _read_existing_document(viking_fs, uri, ctx) for uri in uris]
        if all(document is None for document in preliminary):
            return 0
        lock_paths = [viking_fs._uri_to_path(uri, ctx=ctx) for uri in uris]
        snapshot_lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(lock_paths)
    try:
        return _pending_of([await _read_existing_document(viking_fs, uri, ctx) for uri in uris])
    finally:
        if owns_lease:
            await viking_fs._async_agfs.pathlock_release(snapshot_lease)


async def mark_abstract_overview_pending(
    *,
    viking_fs: Any,
    dir_uri: str,
    changed_entries: int,
    ctx: Optional[RequestContext],
) -> None:
    """Compatibility wrapper for callers that only need the metadata mark."""

    await plan_abstract_overview_refresh(
        viking_fs=viking_fs,
        dir_uri=dir_uri,
        changed_entries=changed_entries,
        ctx=ctx,
    )
