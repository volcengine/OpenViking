# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Data consistency checks between VikingFS content and vector index records."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from typing import Any

from openviking.core.context import ResourceContentType
from openviking.core.namespace import is_session_uri, uri_parts
from openviking.core.namespace_policy import NamespaceProcessingMode, namespace_policy_for_uri
from openviking.observability.log_safety import safe_uri_for_log
from openviking.privacy.global_policy import is_user_global_policy_uri
from openviking.server.identity import RequestContext
from openviking.server.skill_history import is_skill_history_uri
from openviking.session.memory.agent_file import SOUL_FILENAME
from openviking.storage.expr import Eq
from openviking.utils.embedding_utils import get_resource_content_type
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

NON_INDEX_SCOPES = frozenset({"session"})
NON_INDEX_USER_DIRECTORIES = frozenset({"settings", "privacy"})
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


def is_index_source_uri(uri: str, *, is_directory: bool) -> bool:
    """Identify content paths eligible for index validation or recomputation."""
    try:
        parts = uri_parts(uri)
    except ValueError:
        return False
    if not parts or parts[0] in NON_INDEX_SCOPES or is_session_uri(uri):
        return False
    if (not is_directory and parts[-1].startswith(".")) or is_skill_history_uri(uri):
        return False
    if namespace_policy_for_uri(uri).processing_mode is NamespaceProcessingMode.FILE_ONLY:
        return False
    if parts[0] != "user" or len(parts) < 3:
        return True
    if parts[2] in NON_INDEX_USER_DIRECTORIES:
        return False
    return parts[2:] != ["memories", SOUL_FILENAME] and not is_user_global_policy_uri(uri)


async def _read_text_if_exists(viking_fs, uri: str, ctx: RequestContext) -> str:
    try:
        if not await viking_fs.exists(uri, ctx=ctx):
            return ""
        content = await viking_fs.read_file(uri, ctx=ctx)
        return content.decode("utf-8") if isinstance(content, bytes) else str(content)
    except Exception:
        return ""


async def build_index_expectations(
    viking_fs,
    root_uri: str,
    entries: list[dict[str, Any]],
    ctx: RequestContext,
) -> tuple[IndexExpectation, ...]:
    """Build the index records expected from current filesystem content."""
    expectations: list[IndexExpectation] = []
    root_entry = {"uri": root_uri, "isDir": True}
    for entry in chain((root_entry,), entries):
        is_directory = bool(entry.get("isDir"))
        size = entry.get("size")
        if not is_directory and isinstance(size, int) and not isinstance(size, bool) and size == 0:
            continue
        rel_path = str(entry.get("rel_path") or "")
        uri = entry.get("uri")
        if not isinstance(uri, str) or not uri:
            uri = VikingURI(root_uri).join(rel_path).uri
        if not is_index_source_uri(uri, is_directory=is_directory):
            continue

        if is_directory:
            parts = uri_parts(uri)
            # Memory directories can have saved descriptions without indexed summaries.
            if parts[0] == "user" and parts[2:3] == ["memories"]:
                continue
            for level, filename in ((0, ".abstract.md"), (1, ".overview.md")):
                if await _read_text_if_exists(viking_fs, f"{uri}/{filename}", ctx):
                    expectations.append(IndexExpectation(uri=uri, rel_path=rel_path, level=level))
            continue

        name = str(entry.get("name") or rel_path.rstrip("/").rsplit("/", 1)[-1])
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
            logger.info(
                "Failed to check vector index records for %s: %s",
                safe_uri_for_log(str(uri)),
                type(exc).__name__,
            )
    except Exception as exc:
        logger.info(
            "Failed to check vector index records for %s: %s",
            safe_uri_for_log(str(uri)),
            type(exc).__name__,
        )
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
