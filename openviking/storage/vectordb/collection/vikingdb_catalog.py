# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Read Item declarations from the directory abstract records in VikingDB."""

from __future__ import annotations

from typing import Any

from openviking.storage.errors import VikingDBException
from openviking.storage.vector_ids import vector_record_id

_BATCH_SIZE = 100


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
    return vector_record_id(scope, "viking://" + uri.lstrip("/"), level=0)


def _attrs(values: Any) -> list[str]:
    if not isinstance(values, list) or any(
        not isinstance(v, str) or "\0" in v or "=" not in v or not v.split("=", 1)[0]
        for v in values
    ):
        raise ValueError("item_attrs must contain key=value strings")
    return sorted(values)


class VikingDBCatalog:
    def __init__(self, collection):
        self.collection = collection

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
            or row.get("level") != 0
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
        fields = ["account_id", "uri", "level", "item_uuids", "item_attrs"]
        options = (
            {"ignore_unknown_fields": True} if self.collection._item_metadata() is None else {}
        )
        for start in range(0, len(ids), _BATCH_SIZE):
            result = self.collection._data_post(
                "/api/vikingdb/data/fetch_in_collection",
                self._body(
                    partition_by=scope,
                    ids=ids[start : start + _BATCH_SIZE],
                    output_fields=fields,
                    **options,
                ),
            )
            if not isinstance(result.get("fetch"), list):
                raise VikingDBException("Invalid VikingDB Catalog fetch response")
            for item in result["fetch"]:
                row = {**item["fields"], "id": item["id"]}
                self._validate_row(scope, row)
                rows[row["id"]] = row
        return rows

    def _resolve(
        self, scope: str, uris: list[str], mode: str, *, include_items: bool = False
    ) -> dict[str, Any]:
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
            if include_items:
                row["items"] = [
                    {
                        "entity_id": d["item_uuids"][0],
                        "entity_uri": d["uri"],
                        "entity_attrs": d.get("item_attrs"),
                        "entity_score": 0.0,
                    }
                    for d in declarations
                ]
            if declarations:
                row.update(
                    item_uuid=declarations[0]["item_uuids"][0], item_root_uri=declarations[0]["uri"]
                )
            result.append(row)
        return {"data": result}

    def filter_item_groups(
        self, request: dict[str, Any], *, max_groups: int, score_threshold: float | None
    ) -> dict[str, Any]:
        """Enumerate scalar matches without an ANN recall window or similarity scores."""
        scope = request["partition"]
        groups: dict[str, dict[str, Any]] = {}
        offset = 0
        while True:
            result = self.collection._data_post(
                "/api/vikingdb/data/search/scalar",
                {
                    **request,
                    "limit": _BATCH_SIZE,
                    "offset": offset,
                    "output_fields": ["uri", "item_uuids"],
                },
            )
            page = result.get("data")
            if not isinstance(page, list):
                raise VikingDBException("Invalid VikingDB Item scalar response")
            if not page:
                break
            uris = list(dict.fromkeys(_uri(item["fields"]["uri"]) for item in page))
            roots = {
                r["uri"]: r["items"]
                for r in self._resolve(scope, uris, self._schema_mode(), include_items=True)["data"]
            }
            for member in page:
                # Use identities selected by the engine's original member filter.
                ids = member["fields"].get("item_uuids") or []
                for root in roots[_uri(member["fields"]["uri"])]:
                    if root["entity_id"] in ids:
                        groups[root["entity_id"]] = root
            if len(page) < _BATCH_SIZE:
                break
            offset += len(page)
        ordered = sorted(groups.values(), key=lambda g: g["entity_id"])
        if score_threshold is not None and score_threshold > 0:
            ordered = []
        return {"groups": ordered[:max_groups]}

    def _schema_mode(self) -> str:
        if "Fields" not in self.collection.meta_data and self.collection._item_metadata() is None:
            # The public data-plane Item protocol uses union inheritance.
            # Its schema is checked by the engine, not a provisioning snapshot.
            return "union"
        mode = catalog_mode(self.collection.meta_data)
        fields = {f["FieldName"]: f for f in self.collection.meta_data.get("Fields", [])}
        required = {"id": "string", "account_id": "string", "level": "int64"}
        if mode is None or any(
            fields.get(name, {}).get("FieldType") != kind for name, kind in required.items()
        ):
            raise NotImplementedError(
                "VikingDB Catalog requires an account-partitioned inheritance collection"
            )
        return mode

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request["op"]
        if op not in (
            "capabilities",
            "configure",
            "resolve",
            "upsert_nodes",
            "declare_item",
            "validate_resource_update",
        ):
            # The ordinary vector lifecycle already moves and deletes Item roots.
            raise NotImplementedError(f"VikingDB Catalog operation is not supported: {op}")
        mode = self._schema_mode()
        scope = request.get("scope")
        if not isinstance(scope, str) or not scope or "\0" in scope:
            raise ValueError("Catalog requires an account scope")
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
            return {}  # Directory registration is part of the regular root write.

        uri = _uri(request["uri"])
        record_id = _id(scope, uri)
        # The filesystem update plan already enumerates the real child directories.
        child_ids = [_id(scope, _uri(p)) for p in request.get("uris", [])]
        rows = self._fetch(scope, list(dict.fromkeys([record_id, *child_ids])))
        own = rows.get(record_id, {})
        existing = own.get("item_uuids") or []
        attrs = _attrs(request["item_attrs"]) if "item_attrs" in request else None
        if existing and attrs is not None and attrs != sorted(own.get("item_attrs") or []):
            raise ValueError("item_attrs cannot change after an Item is declared")

        if op == "declare_item":
            ids = request.get("item_uuids")
            if (
                not isinstance(ids, list)
                or len(ids) != 1
                or not isinstance(ids[0], str)
                or not ids[0]
                or "\0" in ids[0]
            ):
                raise ValueError("Item declaration requires one item_uuids value")
            return {
                "uri": uri,
                "item_uuid": (existing or ids)[0],
                "item_attrs": own.get("item_attrs") if existing else attrs or [],
                "index_with_root": not existing,
            }

        if existing and request.get("node_type", "dir") != "dir":
            raise ValueError("Cannot change an Item root node type")
        if any(r["uri"] != uri and r.get("item_uuids") for r in rows.values()):
            raise ValueError("Cannot replace a resource root containing independent child Items")
        return {"valid": True, "has_item": bool(existing)}
