# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Volcengine backend collection adapter."""

from __future__ import annotations

from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
    VolcengineApiKeyCollection,
)
from openviking.storage.vectordb.collection.volcengine_collection import (
    VolcengineCollection,
    get_or_create_volcengine_collection,
)

from .base import CollectionAdapter


class VolcengineCollectionAdapter(CollectionAdapter):
    """Adapter for Volcengine-hosted VikingDB."""

    def __init__(
        self,
        *,
        ak: str | None,
        sk: str | None,
        region: str | None,
        session_token: str | None,
        api_key: str | None,
        host: str | None,
        project_name: str,
        collection_name: str,
        index_name: str,
    ):
        super().__init__(collection_name=collection_name, index_name=index_name)
        self._collection: Collection | None = None
        self.mode = "volcengine"
        self._ak = ak
        self._sk = sk
        self._region = region
        self._session_token = session_token
        self._api_key = api_key
        self._host = host
        self._project_name = project_name

    @classmethod
    def from_config(cls, config: Any):
        cfg = getattr(config, "volcengine", None)
        if not cfg:
            raise ValueError("Volcengine backend requires volcengine configuration")
        if cfg.api_key and not (cfg.host or cfg.region):
            raise ValueError("Volcengine api_key mode requires host or region configuration")
        if not cfg.api_key and not (cfg.ak and cfg.sk and cfg.region):
            raise ValueError(
                "Volcengine backend requires AK, SK, and Region configuration "
                "when api_key is not set"
            )
        return cls(
            ak=cfg.ak,
            sk=cfg.sk,
            region=cfg.region,
            session_token=cfg.session_token,
            api_key=cfg.api_key,
            host=cfg.host,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
            index_name=config.index_name or "default",
        )

    def _meta(self) -> Dict[str, Any]:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }

    def _data_plane_meta(self) -> Dict[str, Any]:
        meta = self._meta()
        meta["IndexName"] = self._index_name
        return meta

    def _config(self) -> Dict[str, Any]:
        return {
            "AK": self._ak,
            "SK": self._sk,
            "Region": self._region,
            "SessionToken": self._session_token,
            "ApiKey": self._api_key,
            "Host": self._host,
        }

    def _uses_api_key_auth(self) -> bool:
        return bool(self._api_key)

    def _new_collection_handle(self) -> Collection:
        if self._uses_api_key_auth():
            return Collection(
                VolcengineApiKeyCollection(
                    api_key=self._api_key or "",
                    host=self._host,
                    region=self._region,
                    meta_data=self._data_plane_meta(),
                )
            )
        if self._ak is None or self._sk is None or self._region is None:
            raise ValueError("AK/SK mode requires ak, sk, and region")
        return Collection(
            VolcengineCollection(
                ak=self._ak,
                sk=self._sk,
                region=self._region,
                session_token=self._session_token,
                meta_data=self._meta(),
            )
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        if self._uses_api_key_auth():
            self._collection = self._new_collection_handle()
            return
        candidate = self._new_collection_handle()
        meta = candidate.get_meta_data() or {}
        if meta and meta.get("CollectionName"):
            self._collection = candidate

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        if self._uses_api_key_auth():
            raise NotImplementedError(
                "volcengine backend with api_key does not support create_collection; "
                "pre-create collection/index/schema out of band"
            )
        payload = dict(meta)
        payload.update(self._meta())
        return get_or_create_volcengine_collection(
            config=self._config(),
            meta_data=payload,
        )

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
        return super()._normalize_record_for_read(record)
