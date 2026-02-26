# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Volcengine VikingDB backend driver."""

from __future__ import annotations

from openviking.storage.vector_store.driver import VectorStoreDriver
from openviking.storage.vector_store.registry import register_driver
from openviking.storage.vectordb.collection.volcengine_collection import (
    VolcengineCollection,
    get_or_create_volcengine_collection,
)


@register_driver("volcengine")
class VolcengineVectorDriver(VectorStoreDriver):
    """Driver for Volcengine-hosted VikingDB."""

    def __init__(
        self,
        *,
        ak: str,
        sk: str,
        region: str,
        host: str,
        project_name: str,
        collection_name: str,
    ):
        self.mode = "volcengine"
        self._ak = ak
        self._sk = sk
        self._region = region
        self._host = host
        self._project_name = project_name
        self._collection_name = collection_name
        self._collection = None

    @classmethod
    def from_config(cls, config):
        if not (
            config.volcengine
            and config.volcengine.ak
            and config.volcengine.sk
            and config.volcengine.region
        ):
            raise ValueError("Volcengine backend requires AK, SK, and Region configuration")

        return cls(
            ak=config.volcengine.ak,
            sk=config.volcengine.sk,
            region=config.volcengine.region,
            host=config.volcengine.host or "",
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
        )

    def _match(self, name: str) -> bool:
        return name == self._collection_name

    def _meta(self) -> dict:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }

    def _config(self) -> dict:
        return {
            "AK": self._ak,
            "SK": self._sk,
            "Region": self._region,
            "Host": self._host,
        }

    def _new_collection_handle(self) -> VolcengineCollection:
        return VolcengineCollection(
            ak=self._ak,
            sk=self._sk,
            region=self._region,
            host=self._host,
            meta_data=self._meta(),
        )

    def has_collection(self, name: str) -> bool:
        if not self._match(name):
            return False
        candidate = self._collection or self._new_collection_handle()
        meta = candidate.get_meta_data() or {}
        exists = bool(meta and meta.get("CollectionName"))
        if exists and self._collection is None:
            self._collection = candidate
        return exists

    def get_collection(self, name: str):
        if not self._match(name):
            return None
        if self._collection is not None:
            return self._collection
        if self.has_collection(name):
            return self._collection
        return None

    def create_collection(self, name: str, meta):
        if not self._match(name):
            raise ValueError(
                f"volcengine backend is bound to collection '{self._collection_name}', got '{name}'"
            )
        payload = dict(meta)
        payload.update(self._meta())
        self._collection = get_or_create_volcengine_collection(
            config=self._config(),
            meta_data=payload,
        )
        return self._collection

    def drop_collection(self, name: str) -> None:
        if not self._match(name):
            return
        coll = self.get_collection(name)
        if coll is None:
            return
        coll.drop()
        self._collection = None

    def list_collections(self) -> list[str]:
        return [self._collection_name] if self.has_collection(self._collection_name) else []

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None

    def sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict],
    ) -> list[str]:
        date_time_fields = {
            field.get("FieldName") for field in fields_meta if field.get("FieldType") == "date_time"
        }
        return [field for field in scalar_index_fields if field not in date_time_fields]

    def build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> dict:
        index_type = "hnsw_hybrid" if use_sparse else "hnsw"
        index_meta = {
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

    def normalize_record_for_read(self, record: dict) -> dict:
        for key in ("uri", "parent_uri"):
            value = record.get(key)
            if isinstance(value, str) and not value.startswith("viking://"):
                stripped = value.strip("/")
                if stripped:
                    record[key] = f"viking://{stripped}"
        return record
