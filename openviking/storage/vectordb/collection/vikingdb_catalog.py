# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Map OV declaration management onto VikingDB's existing record APIs.

Declarations are separate, vectorless directory records. Effective field values
and candidate grouping remain the platform engine's responsibility.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any
from weakref import WeakValueDictionary

from openviking.storage.errors import VikingDBException
from openviking.storage.vector_ids import vector_record_id

CATALOG_LEVEL = -1
_BATCH_SIZE = 100
_INDEX_WAIT_SECONDS = 30


def is_account_sharded(meta: dict[str, Any]) -> bool:
    return meta.get("ShardBy", meta.get("PartitionBy")) == "account_id"


def catalog_mode(meta: dict[str, Any]) -> str | None:
    fields = {f["FieldName"]: f for f in meta.get("Fields", [])}
    ids, attrs = fields.get("item_uuids", {}), fields.get("item_attrs", {})
    mode = ids.get("InheritanceMode")
    if (
        is_account_sharded(meta)
        and fields.get("uri", {}).get("FieldType") == "path"
        and ids.get("FieldType") == "list<string>"
        and ids.get("MountPathField") == "uri"
        and mode in ("shadow", "union")
        and attrs.get("FieldType") == "list<string>"
        and not attrs.get("MountPathField")
        and not attrs.get("InheritanceMode")
    ):
        return mode
    return None


def _uri(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("Catalog URI must be an absolute path")
    if "\0" in value or any(p in (".", "..") for p in value.split("/")):
        raise ValueError("Invalid Catalog URI")
    return value.rstrip("/") or "/"


def _id(scope: str, uri: str) -> str:
    return "ov-item:" + vector_record_id(scope, "viking://" + uri.lstrip("/"))


def _attrs(values: Any) -> list[str]:
    if not isinstance(values, list) or any(
        not isinstance(v, str) or "\0" in v or "=" not in v or not v.split("=", 1)[0]
        for v in values
    ):
        raise ValueError("item_attrs must contain key=value strings")
    return sorted(values)


def _and(*conditions: dict[str, Any]) -> dict[str, Any]:
    return {"op": "and", "conds": list(conditions)}


def _eq(field: str, values: list[Any]) -> dict[str, Any]:
    return {"op": "must", "field": field, "conds": values}


class VikingDBCatalog:
    def __init__(self, collection, index_name: str, *, schema: dict[str, Any] | None = None):
        self.collection = collection
        self.index_name = index_name
        self._schema = schema
        self._empty_text = (
            {"content": ""}
            if schema is None
            else {
                field["FieldName"]: ""
                for field in schema.get("Fields", [])
                if field.get("FieldType") == "text"
            }
        )
        self._locks_guard = threading.Lock()
        self._scope_locks = WeakValueDictionary()

    def _scope_lock(self, scope: str):
        # Operations in a partition share a lock while any caller holds or waits for it.
        with self._locks_guard:
            lock = self._scope_locks.get(scope)
            if lock is None:
                lock = threading.Lock()
                self._scope_locks[scope] = lock
            return lock

    def _body(self, **kwargs) -> dict[str, Any]:
        return {
            "project": self.collection.project_name,
            "collection_name": self.collection.collection_name,
            **kwargs,
        }

    def _validate_row(self, scope: str, row: dict[str, Any]) -> None:
        ids = row.get("item_uuids")
        if (
            row.get("account_id") != scope
            or row.get("level") != CATALOG_LEVEL
            or row.get("id") != _id(scope, _uri(row.get("uri")))
            or (
                ids is not None
                and (
                    not isinstance(ids, list)
                    or len(ids) > 1
                    or any(not isinstance(v, str) or not v or "\0" in v for v in ids)
                )
            )
        ):
            raise ValueError("Invalid account-scoped Catalog record")

    def _fetch(self, scope: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        rows = {}
        fields = ["id", "account_id", "uri", "level", "type", "item_uuids", "item_attrs"]
        for start in range(0, len(ids), _BATCH_SIZE):
            result = self.collection._data_post(
                "/api/vikingdb/data/fetch_in_collection",
                self._body(
                    partition_by=scope,
                    ids=ids[start : start + _BATCH_SIZE],
                    output_fields=fields,
                    ignore_unknown_fields=True,
                ),
            )
            if not isinstance(result.get("fetch"), list):
                raise VikingDBException("Invalid VikingDB Catalog fetch response")
            for item in result["fetch"]:
                row = {**item["fields"], "id": item["id"]}
                self._validate_row(scope, row)
                rows[row["id"]] = row
        return rows

    def _search(self, scope: str, condition: dict[str, Any], **kwargs) -> list[dict[str, Any]]:
        result = self.collection._data_post(
            "/api/vikingdb/data/search/scalar",
            self._body(
                index_name=self.index_name,
                field="level",
                order="asc",
                filter=_and(_eq("account_id", [scope]), _eq("level", [CATALOG_LEVEL]), condition),
                **kwargs,
            ),
            include_catalog=True,
        )
        if not isinstance(result.get("data"), list):
            raise VikingDBException("Invalid VikingDB Catalog index response")
        return result["data"]

    def _scan_pages(
        self, scope: str, condition: dict[str, Any], output_fields: list[str]
    ) -> Iterator[list[dict[str, Any]]]:
        offset = 0
        while True:
            page = self._search(
                scope, condition, limit=_BATCH_SIZE, offset=offset, output_fields=output_fields
            )
            if page:
                yield page
            if len(page) < _BATCH_SIZE:
                return
            offset += len(page)

    def _scan(self, scope: str, condition: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for page in self._scan_pages(scope, condition, ["id"]):
            rows.extend(self._fetch(scope, [str(item["id"]) for item in page]).values())
        return rows

    def _subtree(self, scope: str, uri: str) -> list[dict[str, Any]]:
        return self._scan(scope, {**_eq("uri", [uri]), "para": "-d=-1"})

    def _write(self, rows: list[dict[str, Any]]) -> None:
        for start in range(0, len(rows), _BATCH_SIZE):
            self.collection._data_post(
                "/api/vikingdb/data/upsert",
                self._body(
                    data=[{**self._empty_text, **row} for row in rows[start : start + _BATCH_SIZE]],
                    ttl=0,
                    ignore_unknown_fields=self._schema is not None,
                ),
            )

    def _delete(self, scope: str, ids: list[str]) -> None:
        for start in range(0, len(ids), _BATCH_SIZE):
            self.collection._data_post(
                "/api/vikingdb/data/delete",
                self._body(partition_by=scope, ids=ids[start : start + _BATCH_SIZE]),
            )

    def _wait_visible(self, scope: str, expected: dict[str, dict[str, Any] | None]) -> None:
        deadline = time.monotonic() + _INDEX_WAIT_SECONDS
        pending = dict(expected)
        delay = 0.1
        while pending:
            uris = list(pending)
            for start in range(0, len(uris), _BATCH_SIZE):
                batch = uris[start : start + _BATCH_SIZE]
                page = self._search(
                    scope,
                    {**_eq("uri", batch), "para": "-d=0"},
                    limit=_BATCH_SIZE,
                    output_fields=["id", "uri", "item_uuids", "item_attrs"],
                )
                indexed = {str(item["id"]): item.get("fields", {}) for item in page}
                for uri in batch:
                    row, visible = pending[uri], indexed.get(_id(scope, uri))
                    if row is None:
                        matches = visible is None
                    else:
                        # Index reads may return the ancestor union; collection
                        # reads above are the source of local declarations.
                        matches = (
                            visible is not None
                            and visible.get("uri") == row["uri"]
                            and set(row.get("item_uuids") or []).issubset(
                                visible.get("item_uuids") or []
                            )
                            and visible.get("item_attrs") == row.get("item_attrs")
                        )
                    if matches:
                        pending.pop(uri)
            if pending:
                if time.monotonic() >= deadline:
                    raise VikingDBException(
                        "VikingDB Catalog changes are not visible in the index yet; retry the operation",
                        status_code=503,
                        error_type="index_not_ready",
                        retryable=True,
                    )
                time.sleep(min(delay, max(0, deadline - time.monotonic())))
                delay = min(delay * 2, 1)

    def _new_node(self, scope: str, uri: str, node_type: str) -> dict[str, Any]:
        if node_type not in ("file", "dir"):
            raise ValueError("Invalid Catalog node_type")
        row = {
            **self._empty_text,
            "id": _id(scope, uri),
            "account_id": scope,
            "uri": uri,
            "level": CATALOG_LEVEL,
            "type": node_type,
        }
        if self._schema is None:
            # Reject unsupported declarations at the first write on the data API.
            row.update(item_uuids=[], item_attrs=[])
        return row

    def _resolve(self, scope: str, uris: list[str], mode: str | None) -> dict[str, Any]:
        chains = {}
        for uri in uris:
            chain = []
            current = uri
            while current:
                chain.append(current)
                if mode is None:
                    # API-key callers only need to check the exact declared root.
                    break
                current = current.rsplit("/", 1)[0]
            chains[uri] = chain
        ids = list(dict.fromkeys(_id(scope, uri) for chain in chains.values() for uri in chain))
        rows = self._fetch(scope, ids)
        result = []
        for uri, chain in chains.items():
            declarations = [
                rows[_id(scope, p)] for p in chain if rows.get(_id(scope, p), {}).get("item_uuids")
            ]
            if mode == "shadow":
                declarations = declarations[:1]
            row = {"uri": uri, "item_uuids": [d["item_uuids"][0] for d in declarations]}
            if declarations:
                row.update(
                    item_uuid=declarations[0]["item_uuids"][0], item_root_uri=declarations[0]["uri"]
                )
            result.append(row)
        return {"data": result}

    def _restore(self, scope: str, undo: dict[str, Any]) -> None:
        if undo.get("scope") != scope:
            raise ValueError("Catalog rollback belongs to a different account")
        rows = undo["rows"]
        for row in rows:
            self._validate_row(scope, row)
        added = [_uri(uri) for uri in undo["added_uris"]]
        self._write(rows)
        self._delete(scope, [_id(scope, uri) for uri in added])
        self._wait_visible(scope, {**dict.fromkeys(added), **{r["uri"]: r for r in rows}})

    def _move_subtree(self, scope: str, source: str, target: str) -> dict[str, Any]:
        if source == target:
            return {"moved_nodes": 0}
        if source.startswith(target.rstrip("/") + "/") or target.startswith(
            source.rstrip("/") + "/"
        ):
            raise ValueError("Cannot move overlapping Catalog subtrees")

        moved = {}
        previous = {}
        source_ids = []
        condition = {**_eq("uri", [source]), "para": "-d=-1"}
        for page in self._scan_pages(scope, condition, ["uri"]):
            destinations = {}
            for item in page:
                uri = _uri(item["fields"].get("uri"))
                if (uri != source and not uri.startswith(source + "/")) or item["id"] != _id(
                    scope, uri
                ):
                    raise VikingDBException("Invalid source row in VikingDB Catalog move")
                new_uri = target + uri[len(source) :]
                destinations[item["id"]] = (_id(scope, new_uri), new_uri)

            # Read the local source rows and the exact destination keys needed for rollback.
            target_ids = [key for key, _ in destinations.values()]
            snapshot = self._fetch(scope, [*destinations, *target_ids])
            for old_id, (new_id, new_uri) in destinations.items():
                if old_id not in snapshot:
                    continue  # An index entry may outlive its Collection record.
                source_ids.append(old_id)
                previous[old_id] = snapshot[old_id]
                if new_id in snapshot:
                    previous[new_id] = snapshot[new_id]
                moved[new_id] = {**snapshot[old_id], "id": new_id, "uri": new_uri}

        if not moved:
            return {"moved_nodes": 0}
        undo = {
            "scope": scope,
            "rows": list(previous.values()),
            "added_uris": [row["uri"] for key, row in moved.items() if key not in previous],
        }
        try:
            self._write(list(moved.values()))
            self._delete(scope, source_ids)
            self._wait_visible(
                scope,
                {
                    **dict.fromkeys(previous[key]["uri"] for key in source_ids),
                    **{row["uri"]: row for row in moved.values()},
                },
            )
        except Exception:
            self._restore(scope, undo)
            raise
        return {"moved_nodes": len(moved), "undo": undo}

    def search_item_groups(
        self,
        index_name: str,
        *,
        dense_vector: list[float] | None = None,
        sparse_vector: dict[str, float] | None = None,
        filters: dict[str, Any] | None = None,
        max_groups: int = 10,
        aggregation: str = "max",
        score_threshold: float | None = None,
        filter_only: bool = False,
    ) -> dict[str, Any]:
        from .vikingdb_item_search import (
            _has_item_attrs,
            build_item_group_request,
            parse_item_group_result,
        )

        request = build_item_group_request(
            self._schema,
            project=self.collection.project_name,
            collection_name=self.collection.collection_name,
            index_name=index_name,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            filters=filters,
            max_groups=max_groups,
            aggregation=aggregation,
            filter_only=filter_only,
        )
        if _has_item_attrs(filters):
            compiled = self.compile_item_filter(request["partition"], filters)
            if compiled is False:
                return {"groups": []}
            request["filter"] = compiled
        result = self.collection._data_post("/api/vikingdb/data/search/vector", request)
        return parse_item_group_result(result, score_threshold=score_threshold)

    def compile_item_filter(self, scope: str, condition: dict[str, Any]) -> dict[str, Any] | bool:
        """Translate root-attribute predicates into effective identity predicates.

        This only selects declaration IDs. The platform still computes every
        candidate's inherited IDs and all Item scores.
        """
        self._schema_mode()
        predicates = []

        def collect(node):
            if not isinstance(node, dict):
                return
            if node.get("field") == "item_attrs":
                values = node.get("conds")
                if (
                    node.get("op") not in ("must", "must_not")
                    or not isinstance(values, list)
                    or any(not isinstance(v, str) for v in values)
                ):
                    raise ValueError("item_attrs filters require must/must_not and string conds")
                predicates.append(node)
            else:
                for child in node.get("conds", []):
                    collect(child)
                if "filter" in node:
                    collect(node["filter"])

        collect(condition)
        if any(p["op"] == "must_not" for p in predicates):
            selector = _eq("account_id", [scope])
        else:
            values = sorted({v for p in predicates for v in p["conds"]})
            branches = [_eq("item_attrs", [v]) for v in values]
            selector = branches[0] if len(branches) == 1 else {"op": "or", "conds": branches}
        with self._scope_lock(scope):
            rows = self._scan(scope, selector) if selector.get("conds") else []
        declarations = [r for r in rows if r.get("item_uuids")]

        def rewrite(node):
            if node.get("field") == "item_attrs":
                ids = sorted(
                    {
                        r["item_uuids"][0]
                        for r in declarations
                        if (any(v in (r.get("item_attrs") or []) for v in node["conds"]))
                        == (node["op"] == "must")
                    }
                )
                return _eq("item_uuids", ids) if ids else False
            if node.get("op") in ("and", "or"):
                children = [rewrite(c) for c in node["conds"]]
                conjunction = node["op"] == "and"
                if any(c is (False if conjunction else True) for c in children):
                    return not conjunction
                children = [c for c in children if isinstance(c, dict)]
                return {**node, "conds": children} if children else conjunction
            if "filter" in node:
                child = rewrite(node["filter"])
                return {**node, "filter": child} if isinstance(child, dict) else child
            return node

        return rewrite(condition)

    def _schema_mode(self) -> str | None:
        if self._schema is None:
            return None
        mode = catalog_mode(self._schema)
        fields = {f["FieldName"]: f for f in self._schema.get("Fields", [])}
        required = {"id": "string", "account_id": "string", "level": "int64", "type": "string"}
        if mode is None or any(
            fields.get(name, {}).get("FieldType") != kind for name, kind in required.items()
        ):
            raise NotImplementedError(
                "VikingDB Catalog requires an account-partitioned inheritance collection"
            )
        return mode

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        mode = self._schema_mode()
        scope = request.get("scope")
        if not isinstance(scope, str) or not scope or "\0" in scope:
            raise ValueError("Catalog requires an account scope")
        with self._scope_lock(scope):
            return self._execute(request, scope, mode)

    def _execute(self, request: dict[str, Any], scope: str, mode: str | None) -> dict[str, Any]:
        op = request["op"]
        if op in ("capabilities", "configure"):
            if mode is None:
                raise NotImplementedError(
                    "VikingDB API keys only support record-level Catalog operations"
                )
            if request.get("item_identity_mode", mode) != mode:
                raise NotImplementedError(
                    "VikingDB inheritance mode is fixed by the pre-created collection schema"
                )
            return {
                "enabled": True,
                "item_identity_mode": mode,
                "item_identity_modes": [mode],
                "item_attrs_inherit": False,
                "preserve_item_root": True,
            }
        if op == "resolve":
            return self._resolve(scope, [_uri(uri) for uri in request["uris"]], mode)
        if op == "query_items":
            wanted = request.get("item_uuids")
            if wanted == []:
                return {"data": []}
            condition = _eq("item_uuids", wanted) if wanted else _eq("account_id", [scope])
            rows = self._scan(scope, condition)
            return {
                "data": [
                    {
                        "uri": r["uri"],
                        "item_uuid": r["item_uuids"][0],
                        "item_attrs": r.get("item_attrs"),
                    }
                    for r in rows
                    if r.get("item_uuids") and (wanted is None or r["item_uuids"][0] in wanted)
                ]
            }
        if op == "upsert_nodes":
            nodes = [
                self._new_node(scope, _uri(n["uri"]), n["node_type"]) for n in request["nodes"]
            ]
            existing = self._fetch(scope, [n["id"] for n in nodes])
            for node in nodes:
                old = existing.get(node["id"])
                if old is not None and old.get("type") != node["type"]:
                    raise ValueError("Catalog node type conflicts with existing node")
            self._write([n for n in nodes if n["id"] not in existing])
            return {}
        if op == "restore_move":
            self._restore(scope, request["undo"])
            return {}
        if op == "move_subtree":
            return self._move_subtree(scope, _uri(request["from_uri"]), _uri(request["to_uri"]))

        if op not in ("declare_item", "validate_resource_update", "delete_subtree"):
            raise NotImplementedError(f"VikingDB Catalog operation is not supported: {op}")
        uri = _uri(request["uri"])
        own = None
        if op != "delete_subtree":
            node_id = _id(scope, uri)
            own = self._fetch(scope, [node_id]).get(node_id)
        if op == "declare_item":
            if own is None or own.get("type") != "dir":
                raise ValueError("Item declarations require a directory Catalog node")
            ids = request.get("item_uuids")
            if (
                not isinstance(ids, list)
                or len(ids) != 1
                or not isinstance(ids[0], str)
                or not ids[0]
                or "\0" in ids[0]
            ):
                raise ValueError("Item declaration requires one item_uuids value")
            attrs = _attrs(request["item_attrs"]) if "item_attrs" in request else None
            if own.get("item_uuids"):
                if attrs is not None and attrs != sorted(own.get("item_attrs") or []):
                    raise ValueError("item_attrs cannot change after an Item is declared")
            else:
                own = {**own, "item_uuids": ids, "item_attrs": attrs}
                self._write([own])
            self._wait_visible(scope, {uri: own})
            return {
                "uri": uri,
                "item_uuid": own["item_uuids"][0],
                "item_attrs": own.get("item_attrs"),
            }
        if op in ("validate_resource_update", "delete_subtree"):
            rows = self._subtree(scope, uri)
            if any(r["uri"] != uri and r.get("item_uuids") for r in rows) and (
                op == "validate_resource_update" or request.get("preserve_item_root")
            ):
                raise ValueError(
                    "Cannot replace a resource root containing independent child Items"
                )
            if op == "validate_resource_update":
                if own and own.get("item_uuids"):
                    if request.get("node_type", "dir") != "dir":
                        raise ValueError("Cannot change an Item root node type")
                    if "item_attrs" in request and _attrs(request["item_attrs"]) != sorted(
                        own.get("item_attrs") or []
                    ):
                        raise ValueError("item_attrs cannot change after an Item is declared")
                return {"valid": True, "has_item": bool(own and own.get("item_uuids"))}
            removed = [
                r for r in rows if not (request.get("preserve_item_root") and r["uri"] == uri)
            ]
            self._delete(scope, [r["id"] for r in removed])
            self._wait_visible(scope, dict.fromkeys(r["uri"] for r in removed))
            return {}
        raise NotImplementedError(f"VikingDB Catalog operation is not supported: {op}")
