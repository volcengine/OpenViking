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
    def __init__(self, collection):
        self.collection = collection
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
                    partition_by=scope, ids=ids[start : start + _BATCH_SIZE], output_fields=fields
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
                index_name=self.collection.catalog_index_name,
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
        empty_text = {
            f["FieldName"]: ""
            for f in self.collection.meta_data.get("Fields", [])
            if f.get("FieldType") == "text"
        }
        for start in range(0, len(rows), _BATCH_SIZE):
            self.collection.upsert_data(
                [{**empty_text, **row} for row in rows[start : start + _BATCH_SIZE]]
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
            "id": _id(scope, uri),
            "account_id": scope,
            "uri": uri,
            "level": CATALOG_LEVEL,
            "type": node_type,
        }
        for field in self.collection.meta_data.get("Fields", []):
            if field.get("FieldType") == "text":
                row[field["FieldName"]] = ""
        return row

    def _resolve(self, scope: str, uris: list[str], mode: str) -> dict[str, Any]:
        chains = {}
        for uri in uris:
            chain = []
            current = uri
            while current:
                chain.append(current)
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
        added = [_id(scope, _uri(uri)) for uri in undo["added_uris"]]
        self._write(rows)
        self._delete(scope, added)
        self._wait_visible(
            scope, {**dict.fromkeys(undo["added_uris"]), **{r["uri"]: r for r in rows}}
        )

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
                node = snapshot[old_id]
                if new_id in snapshot:
                    target_node = snapshot[new_id]
                    previous[new_id] = target_node
                    # Merging an ordinary directory must not undeclare the target Item.
                    if not node.get("item_uuids") and target_node.get("item_uuids"):
                        node = target_node
                moved[new_id] = {**node, "id": new_id, "uri": new_uri}

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
                    **{previous[key]["uri"]: None for key in source_ids},
                    **{row["uri"]: row for row in moved.values()},
                },
            )
        except Exception:
            self._restore(scope, undo)
            raise
        return {"moved_nodes": len(moved), "undo": undo}

    def _schema_mode(self) -> str:
        mode = catalog_mode(self.collection.meta_data)
        fields = {f["FieldName"]: f for f in self.collection.meta_data.get("Fields", [])}
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

    def _execute(self, request: dict[str, Any], scope: str, mode: str) -> dict[str, Any]:
        op = request["op"]
        if op in ("capabilities", "configure"):
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
        own = self._fetch(scope, [_id(scope, uri)]).get(_id(scope, uri))
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
                own = {**own, "item_uuids": ids, "item_attrs": attrs if attrs is not None else []}
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
            ids = [
                r["id"] for r in rows if not (request.get("preserve_item_root") and r["uri"] == uri)
            ]
            self._delete(scope, ids)
            deleted_ids = set(ids)
            self._wait_visible(scope, {r["uri"]: None for r in rows if r["id"] in deleted_ids})
            return {}
        raise NotImplementedError(f"VikingDB Catalog operation is not supported: {op}")
