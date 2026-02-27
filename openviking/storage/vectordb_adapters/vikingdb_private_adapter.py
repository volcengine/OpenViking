# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Private VikingDB backend collection adapter."""

from __future__ import annotations

from typing import Any, Dict, Optional

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.vikingdb_clients import VIKINGDB_APIS, VikingDBClient
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection

from .base import CollectionAdapter


class VikingDBPrivateCollectionAdapter(CollectionAdapter):
    """Adapter for private VikingDB deployment."""

    def __init__(
        self,
        *,
        host: str,
        headers: Optional[dict[str, str]],
        project_name: str,
        collection_name: str,
    ):
        super().__init__(collection_name=collection_name)
        self.mode = "vikingdb"
        self._host = host
        self._headers = headers
        self._project_name = project_name

    @classmethod
    def from_config(cls, config: Any):
        if not config.vikingdb or not config.vikingdb.host:
            raise ValueError("VikingDB backend requires a valid host")
        return cls(
            host=config.vikingdb.host,
            headers=config.vikingdb.headers,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
        )

    def _client(self) -> VikingDBClient:
        return VikingDBClient(self._host, self._headers)

    def _fetch_collection_meta(self) -> Optional[Dict[str, Any]]:
        path, method = VIKINGDB_APIS["GetVikingdbCollection"]
        req = {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }
        response = self._client().do_req(method, path=path, req_body=req)
        if response.status_code != 200:
            return None
        result = response.json()
        meta = result.get("Result", {})
        return meta or None

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        meta = self._fetch_collection_meta()
        if meta is None:
            return
        self._collection = Collection(
            VikingDBCollection(
                host=self._host,
                headers=self._headers,
                meta_data=meta,
            )
        )

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        self._load_existing_collection_if_needed()
        if self._collection is None:
            raise NotImplementedError("private vikingdb collection should be pre-created")
        return self._collection

    def _sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        date_time_fields = {
            field.get("FieldName") for field in fields_meta if field.get("FieldType") == "date_time"
        }
        return [field for field in scalar_index_fields if field not in date_time_fields]

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        index_type = "hnsw_hybrid" if use_sparse else "hnsw"
        index_meta: Dict[str, Any] = {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": index_type,
                "Distance": distance,
                "Quant": "int8",
            },
            "ScalarIndex": scalar_index_fields,
        }
        if use_sparse:
            index_meta["VectorIndex"]["EnableSparse"] = True
            index_meta["VectorIndex"]["SearchWithSparseLogitAlpha"] = sparse_weight
        return index_meta

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("uri", "parent_uri"):
            value = record.get(key)
            if isinstance(value, str) and not value.startswith("viking://"):
                stripped = value.strip("/")
                if stripped:
                    record[key] = f"viking://{stripped}"
        return record
