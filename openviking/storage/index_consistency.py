# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Data consistency checks between VikingFS content and vector index records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking.core.context import ResourceContentType
from openviking.server.identity import RequestContext
from openviking.storage.expr import Eq
from openviking.utils.embedding_utils import get_resource_content_type
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

NON_INDEX_SCOPES = frozenset({"session"})
PUBLIC_MISSING_RECORD_LIMIT = 20
ERROR_DETAILS_MISSING_RECORD_LIMIT = 1


@dataclass(frozen=True)
class IndexExpectation:
    """One vector index record expected from filesystem content."""

    uri: str
    rel_path: str
    level: int

    @property
    def key(self) -> str:
        path = self.rel_path or "."
        return f"{path}#level={self.level}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "path": self.rel_path,
            "level": self.level,
            "key": self.key,
        }


@dataclass(frozen=True)
class IndexConsistencyReport:
    """Result of checking filesystem/index consistency for a subtree."""

    expected: tuple[IndexExpectation, ...]
    missing_records: tuple[IndexExpectation, ...]

    @property
    def ok(self) -> bool:
        return not self.missing_records

    def details(self, limit: int = ERROR_DETAILS_MISSING_RECORD_LIMIT) -> dict[str, Any]:
        limited = self.missing_records[:limit]
        return {
            "expected_count": len(self.expected),
            "missing_record_count": len(self.missing_records),
            "missing_records": [item.key for item in limited],
            "missing_records_truncated": len(self.missing_records) > len(limited),
        }

    def to_dict(self, limit: int = PUBLIC_MISSING_RECORD_LIMIT) -> dict[str, Any]:
        limited = self.missing_records[:limit]
        return {
            "ok": self.ok,
            "expected_count": len(self.expected),
            "missing_record_count": len(self.missing_records),
            "missing_records": [item.to_dict() for item in limited],
            "missing_records_truncated": len(self.missing_records) > len(limited),
        }


def _join_uri(base_uri: str, rel_path: str) -> str:
    return VikingURI(base_uri).join(rel_path).uri


def _entry_uri(root_uri: str, entry: dict[str, Any]) -> str:
    uri = entry.get("uri")
    if isinstance(uri, str) and uri:
        return uri
    rel_path = str(entry.get("rel_path") or "")
    return _join_uri(root_uri, rel_path)


def _is_index_scope(uri: str) -> bool:
    try:
        return VikingURI(uri).scope not in NON_INDEX_SCOPES
    except Exception:
        return False


def _leaf_name(uri_or_path: str) -> str:
    return uri_or_path.rstrip("/").split("/")[-1]


async def _read_text_if_exists(viking_fs, uri: str, ctx: RequestContext) -> str:
    try:
        if not await viking_fs.exists(uri, ctx=ctx):
            return ""
        content = await viking_fs.read_file(uri, ctx=ctx)
        return content.decode("utf-8") if isinstance(content, bytes) else str(content)
    except Exception:
        return ""


def _directory_candidates(
    root_uri: str,
    entries: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if root_uri != "viking://" and _is_index_scope(root_uri):
        candidates.append((root_uri, ""))

    for entry in entries:
        if not entry.get("isDir"):
            continue
        uri = _entry_uri(root_uri, entry)
        if not _is_index_scope(uri):
            continue
        rel_path = str(entry.get("rel_path") or "")
        candidates.append((uri, rel_path))
    return candidates


def _file_candidates(
    root_uri: str,
    entries: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    for entry in entries:
        if entry.get("isDir"):
            continue
        uri = _entry_uri(root_uri, entry)
        if not _is_index_scope(uri):
            continue
        rel_path = str(entry.get("rel_path") or "")
        name = str(entry.get("name") or _leaf_name(rel_path))
        if name.startswith("."):
            continue
        candidates.append((uri, rel_path, name))
    return candidates


async def build_index_expectations(
    viking_fs,
    root_uri: str,
    entries: list[dict[str, Any]],
    ctx: RequestContext,
) -> tuple[IndexExpectation, ...]:
    """Build the index records expected from current filesystem content."""
    expectations: list[IndexExpectation] = []

    for uri, rel_path in _directory_candidates(root_uri, entries):
        abstract = await _read_text_if_exists(viking_fs, f"{uri}/.abstract.md", ctx)
        overview = await _read_text_if_exists(viking_fs, f"{uri}/.overview.md", ctx)
        if abstract:
            expectations.append(IndexExpectation(uri=uri, rel_path=rel_path, level=0))
        if overview:
            expectations.append(IndexExpectation(uri=uri, rel_path=rel_path, level=1))

    for uri, rel_path, name in _file_candidates(root_uri, entries):
        if get_resource_content_type(name) == ResourceContentType.TEXT:
            expectations.append(IndexExpectation(uri=uri, rel_path=rel_path, level=2))

    return tuple(sorted(expectations, key=lambda item: (item.rel_path, item.level)))


def _record_level(record: dict[str, Any]) -> int | None:
    level = record.get("level")
    if isinstance(level, bool) or not isinstance(level, int):
        return None
    return level


async def _fetch_index_records(vector_store, uri: str, ctx: RequestContext) -> list[dict[str, Any]]:
    if not vector_store or not hasattr(vector_store, "filter"):
        return []

    kwargs = {
        "filter": Eq("uri", uri),
        "limit": 10,
        "output_fields": ["uri", "level"],
    }
    try:
        return await vector_store.filter(**kwargs, ctx=ctx)
    except TypeError:
        try:
            return await vector_store.filter(**kwargs)
        except Exception as exc:
            logger.warning(f"Failed to check vector index records for {uri}: {exc}")
    except Exception as exc:
        logger.warning(f"Failed to check vector index records for {uri}: {exc}")
    return []


async def check_index_consistency(
    viking_fs,
    vector_store,
    root_uri: str,
    entries: list[dict[str, Any]],
    ctx: RequestContext,
) -> IndexConsistencyReport:
    """Check that filesystem content has the expected vector index records."""
    expectations = await build_index_expectations(viking_fs, root_uri, entries, ctx)
    missing_records: list[IndexExpectation] = []

    records_by_uri: dict[str, dict[int, dict[str, Any]]] = {}
    for expectation in expectations:
        if expectation.uri not in records_by_uri:
            records = await _fetch_index_records(vector_store, expectation.uri, ctx)
            records_by_uri[expectation.uri] = {
                level: record for record in records if (level := _record_level(record)) is not None
            }
        record = records_by_uri[expectation.uri].get(expectation.level)
        if record is None:
            missing_records.append(expectation)

    return IndexConsistencyReport(
        expected=expectations,
        missing_records=tuple(missing_records),
    )
