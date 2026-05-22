# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangGraph Store implementation backed by OpenViking."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote, unquote

from openviking.core.namespace import UriClassification, classify_uri
from openviking.integrations.langchain.client import (
    OpenVikingConnection,
    call_openviking,
    ensure_client,
    item_value,
    iter_result_items,
)

try:
    from langgraph.store.base import (
        BaseStore,
        GetOp,
        Item,
        ListNamespacesOp,
        PutOp,
        SearchItem,
        SearchOp,
    )
except ImportError:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    _LANGGRAPH_IMPORT_ERROR = missing_dependency("langgraph", "langgraph")
    BaseStore = object  # type: ignore[assignment,misc]
    GetOp = PutOp = SearchOp = ListNamespacesOp = object  # type: ignore[misc,assignment]
    Item = SearchItem = object  # type: ignore[misc,assignment]
else:
    _LANGGRAPH_IMPORT_ERROR = None

logger = logging.getLogger(__name__)


class OpenVikingStore(BaseStore):
    """LangGraph ``BaseStore`` persisted and indexed through OpenViking.

    Values are stored as JSON records under ``<root_uri>/data``. A separate
    markdown projection under ``<root_uri>/index`` gives OpenViking semantic
    retrieval a compact document to index for query-based ``search`` calls.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        path: str | None = None,
        root_uri: str = "viking://user/memories/langgraph_store",
        index: bool | list[str] | None = None,
        wait: bool = True,
        timeout: float | None = None,
        search_fetch_limit: int = 50,
        auto_initialize: bool = True,
    ):
        if _LANGGRAPH_IMPORT_ERROR is not None:
            raise _LANGGRAPH_IMPORT_ERROR
        super().__init__()
        self._connection = OpenVikingConnection(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            auto_initialize=auto_initialize,
        )
        self.root_uri = root_uri.rstrip("/")
        self.index = index
        self.wait = wait
        self.timeout = timeout
        self.search_fetch_limit = search_fetch_limit
        self._client_cache: Any = None

    def batch(self, ops: Iterable[Any]) -> list[Any]:
        results: list[Any] = []
        for op in ops:
            if isinstance(op, GetOp):
                results.append(self.get(op.namespace, op.key))
            elif isinstance(op, PutOp):
                if op.value is None:
                    self.delete(op.namespace, op.key)
                else:
                    self.put(op.namespace, op.key, op.value, index=op.index, ttl=op.ttl)
                results.append(None)
            elif isinstance(op, SearchOp):
                results.append(
                    self.search(
                        op.namespace_prefix,
                        query=op.query,
                        filter=op.filter,
                        limit=op.limit,
                        offset=op.offset,
                    )
                )
            elif isinstance(op, ListNamespacesOp):
                prefix, suffix = self._match_conditions_to_prefix_suffix(op.match_conditions)
                results.append(
                    self.list_namespaces(
                        prefix=prefix,
                        suffix=suffix,
                        max_depth=op.max_depth,
                        limit=op.limit,
                        offset=op.offset,
                    )
                )
            else:
                raise TypeError(f"Unsupported LangGraph store operation: {type(op)!r}")
        return results

    async def abatch(self, ops: Iterable[Any]) -> list[Any]:
        return await asyncio.to_thread(lambda: self.batch(list(ops)))

    def get(self, namespace: tuple[str, ...], key: str, *, refresh_ttl: Any = None) -> Any:
        try:
            record = self._read_record(self._data_uri(namespace, key))
        except Exception:
            return None
        return Item(
            namespace=tuple(record["namespace"]),
            key=record["key"],
            value=record["value"],
            created_at=_parse_dt(record["created_at"]),
            updated_at=_parse_dt(record["updated_at"]),
        )

    def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        index: bool | list[str] | None = None,
        *,
        ttl: Any = None,
    ) -> None:
        if ttl is not None:
            raise NotImplementedError(
                "TTL is not supported by OpenVikingStore. "
                "OpenViking stores LangGraph values as durable content records."
            )
        namespace = tuple(namespace)
        now = datetime.now(timezone.utc)
        data_uri = self._data_uri(namespace, key)
        record = self._write_record(data_uri, namespace, key, value, now)

        effective_index = self.index if index is None else index
        index_uri = self._index_uri(namespace, key)
        if effective_index is False:
            self._remove(index_uri)
            return
        self._write(index_uri, self._index_document(record, effective_index))

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        self._remove(self._data_uri(namespace, key))
        self._remove(self._index_uri(namespace, key))

    def search(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        refresh_ttl: Any = None,
    ) -> list[Any]:
        namespace_prefix = tuple(namespace_prefix)
        if query:
            return self._semantic_search(namespace_prefix, query, filter, limit, offset)
        items = [
            self._item_to_search_item(item, score=None)
            for item in self._list_items(namespace_prefix)
            if _matches_filter(item.value, filter)
        ]
        items.sort(key=lambda item: (item.updated_at, item.namespace, item.key), reverse=True)
        return items[offset : offset + limit]

    def list_namespaces(
        self,
        *,
        prefix: tuple[str, ...] | None = None,
        suffix: tuple[str, ...] | None = None,
        max_depth: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[str, ...]]:
        namespaces = set()
        for uri in self._all_data_uris(prefix or ()):
            parsed = self._parse_data_uri(uri)
            if parsed is None:
                continue
            namespace, _key = parsed
            if prefix and not _tuple_matches_prefix(namespace, prefix):
                continue
            if suffix and not _tuple_matches_suffix(namespace, suffix):
                continue
            if max_depth is not None and len(namespace) > max_depth:
                namespace = namespace[:max_depth]
            namespaces.add(namespace)
        ordered = sorted(namespaces)
        return ordered[offset : offset + limit]

    def _semantic_search(
        self,
        namespace_prefix: tuple[str, ...],
        query: str,
        filter: dict[str, Any] | None,
        limit: int,
        offset: int,
    ) -> list[Any]:
        result = call_openviking(
            self._client(),
            "find",
            query=query,
            target_uri=self._index_prefix_uri(namespace_prefix),
            limit=max(limit + offset, self.search_fetch_limit),
        )
        items: list[Any] = []
        for _context_type, result_item in iter_result_items(
            result, ("memory", "resource", "skill")
        ):
            uri = item_value(result_item, "uri", "")
            parsed = self._parse_index_uri(uri)
            if parsed is None:
                continue
            namespace, key = parsed
            item = self.get(namespace, key)
            if item is None or not _matches_filter(item.value, filter):
                continue
            items.append(self._item_to_search_item(item, score=item_value(result_item, "score")))
        return items[offset : offset + limit]

    def _list_items(self, namespace_prefix: tuple[str, ...]) -> list[Any]:
        items = []
        for uri in self._all_data_uris(namespace_prefix):
            parsed = self._parse_data_uri(uri)
            if parsed is None:
                continue
            namespace, key = parsed
            item = self.get(namespace, key)
            if item is not None:
                items.append(item)
        return items

    def _all_data_uris(self, namespace_prefix: tuple[str, ...]) -> list[str]:
        base_uri = self._data_prefix_uri(namespace_prefix)
        uris: list[str] = []
        seen: set[str] = set()
        for pattern in ("*.json", "**/*.json"):
            try:
                result = call_openviking(self._client(), "glob", pattern=pattern, uri=base_uri)
            except Exception:
                continue
            for uri in _extract_uris(result):
                if uri not in seen:
                    seen.add(uri)
                    uris.append(uri)
        return uris

    def _client(self) -> Any:
        if self._client_cache is None:
            self._client_cache = ensure_client(self._connection)
        return self._client_cache

    def _read_record(self, uri: str) -> dict[str, Any]:
        content = call_openviking(self._client(), "read", uri=uri)
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        return json.loads(str(content))

    def _write_record(
        self,
        uri: str,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        record = _store_record(namespace, key, value, created_at=now, updated_at=now)
        content = json.dumps(record, ensure_ascii=False, indent=2)
        try:
            self._write_create(uri, content)
            return record
        except Exception as create_exc:
            try:
                existing = self._read_record(uri)
            except Exception:
                raise create_exc

            created_at = _parse_dt(existing["created_at"])
            record = _store_record(namespace, key, value, created_at=created_at, updated_at=now)
            self._write_replace(uri, json.dumps(record, ensure_ascii=False, indent=2))
            return record

    def _write(self, uri: str, content: str) -> None:
        try:
            self._write_create(uri, content)
            return
        except Exception as create_exc:
            try:
                self._write_replace(uri, content)
                return
            except Exception:
                raise create_exc

    def _write_create(self, uri: str, content: str) -> None:
        call_openviking(
            self._client(),
            "write",
            uri=uri,
            content=content,
            mode="create",
            wait=self.wait,
            timeout=self.timeout,
        )

    def _write_replace(self, uri: str, content: str) -> None:
        call_openviking(
            self._client(),
            "write",
            uri=uri,
            content=content,
            mode="replace",
            wait=self.wait,
            timeout=self.timeout,
        )

    def _remove(self, uri: str) -> None:
        try:
            call_openviking(self._client(), "rm", uri=uri, recursive=False)
        except Exception:
            logger.debug("OpenVikingStore remove ignored missing/unavailable URI", exc_info=True)
            pass

    def _data_uri(self, namespace: tuple[str, ...], key: str) -> str:
        return f"{self._data_prefix_uri(namespace)}/{_segment(key)}.json"

    def _index_uri(self, namespace: tuple[str, ...], key: str) -> str:
        return f"{self._index_prefix_uri(namespace)}/{_segment(key)}.md"

    def _data_prefix_uri(self, namespace: tuple[str, ...]) -> str:
        return _join_uri(self.root_uri, "data", *namespace)

    def _index_prefix_uri(self, namespace: tuple[str, ...]) -> str:
        return _join_uri(self.root_uri, "index", *namespace)

    def _parse_data_uri(self, uri: str) -> tuple[tuple[str, ...], str] | None:
        return self._parse_record_uri(uri, "data", ".json")

    def _parse_index_uri(self, uri: str) -> tuple[tuple[str, ...], str] | None:
        return self._parse_record_uri(uri, "index", ".md")

    def _parse_record_uri(
        self, uri: str, collection: str, suffix: str
    ) -> tuple[tuple[str, ...], str] | None:
        prefix = _join_uri(self.root_uri, collection) + "/"
        if not uri.startswith(prefix) or not uri.endswith(suffix):
            return _parse_canonicalized_record_uri(
                root_uri=self.root_uri,
                uri=uri,
                collection=collection,
                suffix=suffix,
            )
        return _parse_record_parts(uri[len(prefix) : -len(suffix)].split("/"))

    def _index_document(self, record: dict[str, Any], index: bool | list[str] | None) -> str:
        value = record["value"]
        projected = _project_value(value, index)
        return "\n".join(
            [
                f"# {record['key']}",
                "",
                f"Namespace: {'/'.join(record['namespace'])}",
                f"Key: {record['key']}",
                "",
                json.dumps(projected, ensure_ascii=False, indent=2),
            ]
        )

    def _item_to_search_item(self, item: Any, score: float | None) -> Any:
        return SearchItem(
            namespace=item.namespace,
            key=item.key,
            value=item.value,
            created_at=item.created_at,
            updated_at=item.updated_at,
            score=score,
        )

    def _match_conditions_to_prefix_suffix(
        self, conditions: Iterable[Any] | None
    ) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
        prefix = None
        suffix = None
        for condition in conditions or []:
            match_type = getattr(condition, "match_type", None)
            path = tuple(getattr(condition, "path", ()) or ())
            if match_type == "prefix":
                prefix = path
            elif match_type == "suffix":
                suffix = path
        return prefix, suffix


def _segment(value: str) -> str:
    return quote(str(value), safe="")


def _join_uri(root: str, *segments: str) -> str:
    suffix = "/".join(_segment(segment) for segment in segments if segment)
    return root.rstrip("/") if not suffix else f"{root.rstrip('/')}/{suffix}"


def _parse_canonicalized_record_uri(
    *,
    root_uri: str,
    uri: str,
    collection: str,
    suffix: str,
) -> tuple[tuple[str, ...], str] | None:
    root = classify_uri(root_uri)
    root_tail = _identity_relative_root_tail(root)
    if root_tail is None:
        return None

    candidate = classify_uri(uri)
    if candidate.scope != root.scope or candidate.content_index is None:
        return None

    candidate_tail = candidate.parts[candidate.content_index :]
    if len(candidate_tail) < len(root_tail) + 2:
        return None
    if candidate_tail[: len(root_tail)] != root_tail:
        return None

    collection_index = len(root_tail)
    if candidate_tail[collection_index] != collection:
        return None

    rel_parts = list(candidate_tail[collection_index + 1 :])
    if not rel_parts or not rel_parts[-1].endswith(suffix):
        return None
    rel_parts = [*rel_parts[:-1], rel_parts[-1][: -len(suffix)]]
    return _parse_record_parts(rel_parts)


def _identity_relative_root_tail(classification: UriClassification) -> tuple[str, ...] | None:
    if classification.scope not in {"user", "agent"}:
        return None
    if classification.content_index != 1:
        return None
    return classification.parts[classification.content_index :]


def _parse_record_parts(parts: list[str]) -> tuple[tuple[str, ...], str] | None:
    if not parts or not parts[-1]:
        return None
    namespace = tuple(unquote(part) for part in parts[:-1])
    key = unquote(parts[-1])
    return namespace, key


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _store_record(
    namespace: tuple[str, ...],
    key: str,
    value: dict[str, Any],
    *,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    return {
        "namespace": list(namespace),
        "key": key,
        "value": value,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def _extract_uris(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.startswith("viking://") else []
    if isinstance(value, dict):
        if isinstance(value.get("uri"), str):
            return [value["uri"]]
        uris: list[str] = []
        for key in ("matches", "result", "files", "items"):
            uris.extend(_extract_uris(value.get(key)))
        return uris
    if isinstance(value, list):
        uris: list[str] = []
        for item in value:
            uris.extend(_extract_uris(item))
        return uris
    return []


def _project_value(value: dict[str, Any], index: bool | list[str] | None) -> dict[str, Any]:
    if isinstance(index, list):
        return {field: _nested_value(value, field) for field in index}
    return value


def _nested_value(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _matches_filter(value: dict[str, Any], filter: dict[str, Any] | None) -> bool:
    if not filter:
        return True
    for path, expected in filter.items():
        actual = _nested_value(value, path)
        if isinstance(expected, dict):
            for op, target in expected.items():
                if not _compare(actual, op, target):
                    return False
        elif actual != expected:
            return False
    return True


def _compare(actual: Any, op: str, target: Any) -> bool:
    if op in {"$eq", "eq"}:
        return actual == target
    if op in {"$ne", "ne"}:
        return actual != target
    if op in {"$gt", "gt"}:
        return _safe_ordered_compare(actual, target, lambda left, right: left > right)
    if op in {"$gte", "gte"}:
        return _safe_ordered_compare(actual, target, lambda left, right: left >= right)
    if op in {"$lt", "lt"}:
        return _safe_ordered_compare(actual, target, lambda left, right: left < right)
    if op in {"$lte", "lte"}:
        return _safe_ordered_compare(actual, target, lambda left, right: left <= right)
    if op in {"$in", "in"}:
        try:
            return actual in target
        except TypeError:
            return False
    return actual == target


def _safe_ordered_compare(actual: Any, target: Any, compare) -> bool:
    if actual is None:
        return False
    try:
        return bool(compare(actual, target))
    except TypeError:
        return False


def _tuple_matches_prefix(value: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(value) >= len(prefix) and value[: len(prefix)] == prefix


def _tuple_matches_suffix(value: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    return len(value) >= len(suffix) and value[-len(suffix) :] == suffix
