# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OKF documents and atomic writeback for generated semantic sidecars.

Only .abstract.md and .overview.md use this module. Ordinary Markdown keeps
its frontmatter as user content and is never parsed implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, TypeVar

import yaml

from openviking.core.context import ContextLevel
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

SEMANTIC_SIDECAR_FILENAMES = frozenset({".abstract.md", ".overview.md"})
EMBEDDING_METADATA_FIELDS = ("directory",)
_METADATA_ORDER = ("directory", "source", "generated_by", "freshness")
_MAX_SOURCE_URI_CHARS = 4096
_MAX_LABEL_CHARS = 128
_T = TypeVar("_T")


class SemanticSidecarFormatError(ValueError):
    """A generated semantic sidecar has invalid OKF frontmatter."""


class SemanticSidecarMetadataError(SemanticSidecarFormatError):
    """A public write attempted to change protected sidecar metadata."""


@dataclass(frozen=True)
class SemanticSidecarDocument:
    """Parsed machine metadata and visible Markdown body."""

    metadata: Dict[str, Any]
    body: str
    legacy: bool = False


def is_semantic_sidecar_uri(uri: str) -> bool:
    """Return whether uri names one of the two generated sidecars."""

    return uri.rstrip("/").rsplit("/", 1)[-1] in SEMANTIC_SIDECAR_FILENAMES


def _normalize_directory_uri(dir_uri: str) -> str:
    value = str(dir_uri or "").strip()
    if not value:
        raise SemanticSidecarFormatError("semantic sidecar directory must not be empty")
    if not value.startswith("viking://"):
        raise SemanticSidecarFormatError("semantic sidecar directory must be a viking:// URI")
    return value.rstrip("/") + "/"


def _bounded_string(value: Any, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticSidecarFormatError(f"semantic sidecar {field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise SemanticSidecarFormatError(
            f"semantic sidecar {field} exceeds the {limit}-character policy limit"
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
            raise SemanticSidecarFormatError("semantic sidecar source must contain kind and uri")
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
            raise SemanticSidecarFormatError(
                "semantic sidecar generated_by must contain component and trigger"
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
            raise SemanticSidecarFormatError("semantic sidecar freshness has an invalid field set")
        counters: Dict[str, int] = {}
        for field in (
            "total_entries",
            "sampled_entries",
            "unsampled_entries",
            "pending_child_changes",
        ):
            value = freshness[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SemanticSidecarFormatError(
                    f"semantic sidecar freshness.{field} must be a non-negative integer"
                )
            counters[field] = value
        if counters["sampled_entries"] + counters["unsampled_entries"] != counters["total_entries"]:
            raise SemanticSidecarFormatError(
                "semantic sidecar freshness sampled + unsampled must equal total"
            )
        normalized["freshness"] = counters

    return {field: normalized[field] for field in _METADATA_ORDER if field in normalized}


def parse_semantic_sidecar(raw: str | bytes) -> SemanticSidecarDocument:
    """Parse OKF Markdown while accepting frontmatter-free legacy content.

    A leading delimiter opts a generated sidecar into strict OKF parsing.
    Invalid YAML, non-object YAML, and missing closing delimiters fail loudly.
    """

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SemanticSidecarFormatError("semantic sidecar is not valid UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("semantic sidecar content must be str or bytes")

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return SemanticSidecarDocument(metadata={}, body=text, legacy=True)

    closing_index: Optional[int] = None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            closing_index = index
            break
    if closing_index is None:
        raise SemanticSidecarFormatError("semantic sidecar frontmatter is not closed")

    try:
        loaded = yaml.safe_load("".join(lines[1:closing_index]))
    except yaml.YAMLError as exc:
        raise SemanticSidecarFormatError(f"invalid semantic sidecar YAML: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, Mapping):
        raise SemanticSidecarFormatError("semantic sidecar frontmatter must be a YAML object")
    if "directory" not in loaded:
        raise SemanticSidecarFormatError("semantic sidecar frontmatter must contain directory")

    body = "".join(lines[closing_index + 1 :])
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return SemanticSidecarDocument(metadata=_normalize_metadata(loaded), body=body, legacy=False)


def render_semantic_sidecar(
    level: int | ContextLevel,
    dir_uri: str,
    body: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render deterministic OKF Markdown for an L0 or L1 sidecar."""

    try:
        resolved_level = ContextLevel(int(level))
    except (TypeError, ValueError) as exc:
        raise SemanticSidecarFormatError(f"invalid semantic sidecar level: {level}") from exc
    if resolved_level not in {ContextLevel.ABSTRACT, ContextLevel.OVERVIEW}:
        raise SemanticSidecarFormatError("semantic sidecars only support L0 and L1")
    if not isinstance(body, str):
        raise TypeError("semantic sidecar body must be a string")

    merged: Dict[str, Any] = dict(metadata or {})
    merged["directory"] = _normalize_directory_uri(dir_uri)
    frontmatter = yaml.safe_dump(
        _normalize_metadata(merged),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def prepare_semantic_sidecar_write(
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
        raise ValueError(f"unsupported semantic sidecar write mode: {mode}")
    if not is_semantic_sidecar_uri(uri):
        raise ValueError(f"not a semantic sidecar URI: {uri}")

    current = parse_semantic_sidecar(current_raw)
    requested = parse_semantic_sidecar(requested_raw)
    if not requested.legacy and requested.metadata != current.metadata:
        raise SemanticSidecarMetadataError("cannot modify protected semantic sidecar metadata")

    requested_body = requested.body
    body = current.body + requested_body if mode == "append" else requested_body
    filename = uri.rstrip("/").rsplit("/", 1)[-1]
    level = ContextLevel.ABSTRACT if filename == ".abstract.md" else ContextLevel.OVERVIEW
    # Preserve even a historically inconsistent stored directory value.  A
    # public body edit must never repair or otherwise mutate protected
    # metadata as a side effect.  Legacy sidecars have no metadata baseline,
    # so their first body edit naturally migrates them using the target URI.
    dir_uri = current.metadata.get("directory") or uri.rstrip("/").rsplit("/", 1)[0]
    return render_semantic_sidecar(level, dir_uri, body, current.metadata)


def body_for_preview(raw: str | bytes) -> str:
    """Return only user-visible Markdown from a semantic sidecar."""

    document = parse_semantic_sidecar(raw)
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

    document = parse_semantic_sidecar(raw)
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

    return body_for_embedding(render_semantic_sidecar(level, dir_uri, body))


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
    total_entries: int, sampled_entries: int, pending: int = 0
) -> Dict[str, int]:
    """Create validated direct-child freshness counters."""

    return _normalize_metadata(
        {
            "freshness": {
                "total_entries": total_entries,
                "sampled_entries": sampled_entries,
                "unsampled_entries": total_entries - sampled_entries,
                "pending_child_changes": pending,
            }
        }
    )["freshness"]


async def _read_existing_document(
    viking_fs: Any, uri: str, ctx: Optional[RequestContext]
) -> Optional[SemanticSidecarDocument]:
    raw = await _raw_if_exists(viking_fs, uri, ctx)
    return parse_semantic_sidecar(raw) if raw is not None else None


async def write_semantic_sidecars(
    *,
    viking_fs: Any,
    dir_uri: str,
    overview: str,
    abstract: str,
    ctx: Optional[RequestContext],
    is_stale: Callable[[], bool],
    metadata: Optional[Mapping[str, Any]] = None,
    lock: Optional[Dict[str, Any]] = None,
    log_prefix: str = "[Semantic]",
) -> bool:
    """Render and atomically write sidecars while preserving source metadata.

    Exact equality is checked under the lock so stable refreshes do not touch
    modification times or create noisy Git diffs.
    """

    if is_stale():
        logger.info("%s Skipping stale semantic write for %s", log_prefix, dir_uri)
        return False

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
            return False

        existing_overview = await _read_existing_document(viking_fs, overview_uri, ctx)
        existing_abstract = await _read_existing_document(viking_fs, abstract_uri, ctx)
        merged_metadata = dict(metadata or {})
        for existing in (existing_overview, existing_abstract):
            if existing and "source" in existing.metadata and "source" not in merged_metadata:
                merged_metadata["source"] = existing.metadata["source"]
                break

        rendered_overview = render_semantic_sidecar(
            ContextLevel.OVERVIEW, dir_uri, overview, merged_metadata
        )
        rendered_abstract = render_semantic_sidecar(
            ContextLevel.ABSTRACT, dir_uri, abstract, merged_metadata
        )
        current_overview = await _raw_if_exists(viking_fs, overview_uri, ctx)
        current_abstract = await _raw_if_exists(viking_fs, abstract_uri, ctx)

        if current_overview != rendered_overview:
            await viking_fs.write_file(
                overview_uri, rendered_overview, ctx=ctx, lease_ref=sidecar_lease
            )
        if current_abstract != rendered_abstract:
            await viking_fs.write_file(
                abstract_uri, rendered_abstract, ctx=ctx, lease_ref=sidecar_lease
            )
        return True
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


async def mark_semantic_sidecars_pending(
    *,
    viking_fs: Any,
    dir_uri: str,
    changed_entries: int,
    ctx: Optional[RequestContext],
) -> None:
    """Increment pending changes while leaving visible sidecar bodies untouched.

    Legacy documents have no freshness baseline and are left alone until their
    next natural refresh migrates them to OKF. Both sidecars are updated under
    one exact-path lease so their counters remain symmetric.
    """

    if changed_entries <= 0:
        return
    uris = [
        f"{dir_uri.rstrip('/')}/.overview.md",
        f"{dir_uri.rstrip('/')}/.abstract.md",
    ]
    preliminary = [await _raw_if_exists(viking_fs, uri, ctx) for uri in uris]
    if not any(
        raw is not None
        and not parse_semantic_sidecar(raw).legacy
        and "freshness" in parse_semantic_sidecar(raw).metadata
        for raw in preliminary
    ):
        return

    lock_paths = [viking_fs._uri_to_path(uri, ctx=ctx) for uri in uris]
    lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(lock_paths)
    try:
        for level, uri in zip((ContextLevel.OVERVIEW, ContextLevel.ABSTRACT), uris, strict=True):
            raw = await _raw_if_exists(viking_fs, uri, ctx)
            if raw is None:
                continue
            document = parse_semantic_sidecar(raw)
            freshness = document.metadata.get("freshness")
            if document.legacy or not isinstance(freshness, Mapping):
                continue
            metadata = dict(document.metadata)
            updated_freshness = dict(freshness)
            updated_freshness["pending_child_changes"] += changed_entries
            metadata["freshness"] = updated_freshness
            rendered = render_semantic_sidecar(level, dir_uri, document.body, metadata)
            if rendered != raw:
                await viking_fs.write_file(uri, rendered, ctx=ctx, lease_ref=lease)
    finally:
        await viking_fs._async_agfs.pathlock_release(lease)
