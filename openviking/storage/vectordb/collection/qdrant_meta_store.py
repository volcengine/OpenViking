# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Sidecar metadata persistence for Qdrant-backed OpenViking collections."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional

from .qdrant_rest import QdrantRestClient, QdrantRestError


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class QdrantMetaStore:
    """Store OpenViking collection/index metadata in a dedicated Qdrant collection."""

    COLLECTION_KIND = "collection"
    INDEX_KIND = "index"

    def __init__(
        self,
        *,
        client: QdrantRestClient,
        meta_collection_name: str,
    ) -> None:
        self._client = client
        self._meta_collection_name = meta_collection_name
        self._meta_collection_ensured = False

    @property
    def meta_collection_name(self) -> str:
        return self._meta_collection_name

    def _ensure_meta_collection(self) -> None:
        if self._meta_collection_ensured:
            return
        if self._client.collection_exists(self._meta_collection_name):
            self._meta_collection_ensured = True
            return
        self._client.request(
            "PUT",
            f"/collections/{self._meta_collection_name}",
            json_body={
                # Keep metadata in a tiny dedicated collection. Some Qdrant
                # deployments still require vector configuration, so we use a
                # 1D dummy vector payload for sidecar metadata documents.
                "vectors": {
                    "size": 1,
                    "distance": "Cosine",
                }
            },
            expected_statuses=(200, 201),
        )
        self._meta_collection_ensured = True

    @staticmethod
    def _collection_doc_id(collection_key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"openviking:qdrant:collection:{collection_key}"))

    @staticmethod
    def _index_doc_id(collection_key: str, index_name: str) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"openviking:qdrant:index:{collection_key}:{index_name}",
            )
        )

    def _upsert_meta_doc(self, *, point_id: str, payload: Dict[str, Any]) -> None:
        self._ensure_meta_collection()
        self._client.request(
            "PUT",
            f"/collections/{self._meta_collection_name}/points",
            json_body={
                "points": [
                    {
                        "id": point_id,
                        "vector": [0.0],
                        "payload": payload,
                    }
                ]
            },
            params={"wait": "true"},
            expected_statuses=(200, 202),
        )

    def _get_point(self, point_id: str) -> Optional[Dict[str, Any]]:
        if not self._meta_collection_ensured and not self._client.collection_exists(
            self._meta_collection_name
        ):
            return None
        self._meta_collection_ensured = True
        try:
            response = self._client.request(
                "POST",
                f"/collections/{self._meta_collection_name}/points",
                json_body={
                    "ids": [point_id],
                    "with_payload": True,
                    "with_vector": False,
                },
            )
        except QdrantRestError as exc:
            if exc.status_code == 404:
                return None
            raise

        result = response.get("result", {})
        if isinstance(result, list):
            points = result
        elif isinstance(result, dict):
            points = result.get("points", [])
        else:
            points = []
        return points[0] if points else None

    def _scroll_index_docs(self, collection_key: str) -> List[Dict[str, Any]]:
        if not self._meta_collection_ensured and not self._client.collection_exists(
            self._meta_collection_name
        ):
            return []
        self._meta_collection_ensured = True
        points: List[Dict[str, Any]] = []
        next_offset: Optional[Any] = None

        while True:
            request_body: Dict[str, Any] = {
                "limit": 128,
                "with_payload": True,
                "with_vector": False,
                "filter": {
                    "must": [
                        {"key": "kind", "match": {"value": self.INDEX_KIND}},
                        {"key": "collection_key", "match": {"value": collection_key}},
                    ]
                },
            }
            if next_offset is not None:
                request_body["offset"] = next_offset

            response = self._client.request(
                "POST",
                f"/collections/{self._meta_collection_name}/points/scroll",
                json_body=request_body,
            )
            result = response.get("result", {})
            if not isinstance(result, dict):
                break

            batch = result.get("points", [])
            if isinstance(batch, list):
                points.extend(batch)

            next_offset = result.get("next_page_offset")
            if next_offset is None:
                break

        return points

    def save_collection_meta(
        self,
        *,
        collection_key: str,
        logical_collection_name: str,
        project_name: str,
        meta: Dict[str, Any],
    ) -> None:
        now = _utcnow_iso()
        payload = {
            "kind": self.COLLECTION_KIND,
            "collection_key": collection_key,
            "logical_collection_name": logical_collection_name,
            "project_name": project_name,
            "meta": meta,
            "updated_at": now,
        }
        self._upsert_meta_doc(
            point_id=self._collection_doc_id(collection_key),
            payload=payload,
        )

    def get_collection_meta(self, *, collection_key: str) -> Optional[Dict[str, Any]]:
        point = self._get_point(self._collection_doc_id(collection_key))
        if not point:
            return None
        payload = point.get("payload", {})
        meta = payload.get("meta")
        return meta if isinstance(meta, dict) else None

    def update_collection_meta(
        self,
        *,
        collection_key: str,
        logical_collection_name: str,
        project_name: str,
        fields: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        meta = self.get_collection_meta(collection_key=collection_key) or {
            "CollectionName": logical_collection_name
        }
        if fields is not None:
            meta["Fields"] = fields
        if description is not None:
            meta["Description"] = description
        self.save_collection_meta(
            collection_key=collection_key,
            logical_collection_name=logical_collection_name,
            project_name=project_name,
            meta=meta,
        )
        return meta

    def save_index_meta(
        self,
        *,
        collection_key: str,
        logical_collection_name: str,
        index_name: str,
        meta: Dict[str, Any],
    ) -> None:
        now = _utcnow_iso()
        payload = {
            "kind": self.INDEX_KIND,
            "collection_key": collection_key,
            "logical_collection_name": logical_collection_name,
            "index_name": index_name,
            "meta": meta,
            "updated_at": now,
        }
        self._upsert_meta_doc(
            point_id=self._index_doc_id(collection_key, index_name),
            payload=payload,
        )

    def get_index_meta(self, *, collection_key: str, index_name: str) -> Optional[Dict[str, Any]]:
        point = self._get_point(self._index_doc_id(collection_key, index_name))
        if not point:
            return None
        payload = point.get("payload", {})
        meta = payload.get("meta")
        return meta if isinstance(meta, dict) else None

    def list_indexes(self, *, collection_key: str) -> List[str]:
        docs = self._scroll_index_docs(collection_key)
        index_names: List[str] = []
        for doc in docs:
            payload = doc.get("payload", {})
            index_name = payload.get("index_name")
            if isinstance(index_name, str):
                index_names.append(index_name)
        return index_names

    def delete_index_meta(self, *, collection_key: str, index_name: str) -> None:
        if not self._meta_collection_ensured and not self._client.collection_exists(
            self._meta_collection_name
        ):
            return
        self._meta_collection_ensured = True
        self._client.request(
            "POST",
            f"/collections/{self._meta_collection_name}/points/delete",
            json_body={"points": [self._index_doc_id(collection_key, index_name)]},
            params={"wait": "true"},
            expected_statuses=(200, 202),
        )

    def delete_collection_meta(self, *, collection_key: str) -> None:
        if not self._meta_collection_ensured and not self._client.collection_exists(
            self._meta_collection_name
        ):
            return
        self._meta_collection_ensured = True
        point_ids = [self._collection_doc_id(collection_key)]
        point_ids.extend(
            self._index_doc_id(collection_key, index_name)
            for index_name in self.list_indexes(collection_key=collection_key)
        )
        if not point_ids:
            return
        self._client.request(
            "POST",
            f"/collections/{self._meta_collection_name}/points/delete",
            json_body={"points": point_ids},
            params={"wait": "true"},
            expected_statuses=(200, 202),
        )
