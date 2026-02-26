# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Collection adapter layer for backend-specific storage integration."""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.vector_store.expr import (
    And,
    Contains,
    Eq,
    FilterExpr,
    In,
    Or,
    Range,
    RawDSL,
    TimeRange,
)
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.http_collection import (
    HttpCollection,
    get_or_create_http_collection,
    list_vikingdb_collections,
)
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking.storage.vectordb.collection.result import FetchDataInCollectionResult
from openviking.storage.vectordb.collection.vikingdb_clients import VIKINGDB_APIS, VikingDBClient
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection
from openviking.storage.vectordb.collection.volcengine_collection import (
    VolcengineCollection,
    get_or_create_volcengine_collection,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _parse_url(url: str) -> tuple[str, int]:
    normalized = url
    if not normalized.startswith(("http://", "https://")):
        normalized = f"http://{normalized}"
    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5000
    return host, port


def _normalize_collection_names(raw_collections: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for item in raw_collections:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("CollectionName") or item.get("collection_name") or item.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


class CollectionAdapter(ABC):
    """Backend-specific adapter for single-collection operations."""

    mode: str

    def __init__(self, collection_name: str):
        self._collection_name = collection_name
        self._collection: Optional[Collection] = None

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @classmethod
    @abstractmethod
    def from_config(cls, config: Any) -> "CollectionAdapter":
        """Create an adapter instance from VectorDB backend config."""

    @abstractmethod
    def _load_existing_collection_if_needed(self) -> None:
        """Load existing bound collection handle when possible."""

    @abstractmethod
    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        """Create backend collection handle for bound collection."""

    def collection_exists(self) -> bool:
        self._load_existing_collection_if_needed()
        return self._collection is not None

    def get_collection(self) -> Collection:
        self._load_existing_collection_if_needed()
        if self._collection is None:
            raise CollectionNotFoundError(f"Collection {self._collection_name} does not exist")
        return self._collection

    def create_collection(
        self,
        name: str,
        schema: Dict[str, Any],
        *,
        distance: str,
        sparse_weight: float,
        index_name: str,
    ) -> bool:
        if self.collection_exists():
            return False

        self._collection_name = name
        collection_meta = dict(schema)
        scalar_index_fields = collection_meta.pop("ScalarIndex", [])
        if "CollectionName" not in collection_meta:
            collection_meta["CollectionName"] = name

        self._collection = self._create_backend_collection(collection_meta)

        scalar_index_fields = self.sanitize_scalar_index_fields(
            scalar_index_fields=scalar_index_fields,
            fields_meta=collection_meta.get("Fields", []),
        )
        index_meta = self.build_default_index_meta(
            index_name=index_name,
            distance=distance,
            use_sparse=sparse_weight > 0.0,
            sparse_weight=sparse_weight,
            scalar_index_fields=scalar_index_fields,
        )
        self._collection.create_index(index_name, index_meta)
        return True

    def drop_collection(self) -> bool:
        if not self.collection_exists():
            return False

        coll = self.get_collection()

        # Drop indexes first so index lifecycle remains internal to adapter.
        try:
            for index_name in coll.list_indexes() or []:
                try:
                    coll.drop_index(index_name)
                except Exception as e:
                    logger.warning("Failed to drop index %s: %s", index_name, e)
        except Exception as e:
            logger.warning("Failed to list indexes before dropping collection: %s", e)

        try:
            coll.drop()
        except NotImplementedError:
            logger.warning("Collection drop is not supported by backend mode=%s", self.mode)
            return False
        finally:
            self._collection = None

        return True

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None

    def get_collection_info(self) -> Optional[Dict[str, Any]]:
        if not self.collection_exists():
            return None
        return self.get_collection().get_meta_data()

    def sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        return scalar_index_fields

    def build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        index_type = "flat_hybrid" if use_sparse else "flat"
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

    def normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return record

    def compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        if expr is None:
            return {}
        if isinstance(expr, dict):
            return expr
        if isinstance(expr, RawDSL):
            return expr.payload
        if isinstance(expr, And):
            conds = [self.compile_filter(c) for c in expr.conds if c is not None]
            conds = [c for c in conds if c]
            if not conds:
                return {}
            if len(conds) == 1:
                return conds[0]
            return {"op": "and", "conds": conds}
        if isinstance(expr, Or):
            conds = [self.compile_filter(c) for c in expr.conds if c is not None]
            conds = [c for c in conds if c]
            if not conds:
                return {}
            if len(conds) == 1:
                return conds[0]
            return {"op": "or", "conds": conds}
        if isinstance(expr, Eq):
            return {"op": "must", "field": expr.field, "conds": [expr.value]}
        if isinstance(expr, In):
            return {"op": "must", "field": expr.field, "conds": list(expr.values)}
        if isinstance(expr, Range):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.gte is not None:
                payload["gte"] = expr.gte
            if expr.gt is not None:
                payload["gt"] = expr.gt
            if expr.lte is not None:
                payload["lte"] = expr.lte
            if expr.lt is not None:
                payload["lt"] = expr.lt
            return payload
        if isinstance(expr, Contains):
            return {
                "op": "contains",
                "field": expr.field,
                "substring": expr.substring,
            }
        if isinstance(expr, TimeRange):
            payload: Dict[str, Any] = {"op": "range", "field": expr.field}
            if expr.start is not None:
                payload["gte"] = expr.start
            if expr.end is not None:
                payload["lt"] = expr.end
            return payload
        raise TypeError(f"Unsupported filter expr type: {type(expr)!r}")

    def upsert(self, data: Dict[str, Any] | list[Dict[str, Any]]) -> list[str]:
        coll = self.get_collection()
        records = [data] if isinstance(data, dict) else data
        normalized: list[Dict[str, Any]] = []
        ids: list[str] = []
        for item in records:
            record = dict(item)
            record_id = record.get("id") or str(uuid.uuid4())
            record["id"] = record_id
            ids.append(record_id)
            normalized.append(record)
        coll.upsert_data(normalized)
        return ids

    def get(self, ids: list[str]) -> list[Dict[str, Any]]:
        coll = self.get_collection()
        result = coll.fetch_data(ids)

        records: list[Dict[str, Any]] = []
        if isinstance(result, FetchDataInCollectionResult):
            for item in result.items:
                record = dict(item.fields) if item.fields else {}
                record["id"] = item.id
                records.append(self.normalize_record_for_read(record))
            return records

        if isinstance(result, dict) and "fetch" in result:
            for item in result.get("fetch", []):
                record = dict(item.get("fields", {})) if item.get("fields") else {}
                record_id = item.get("id")
                if record_id:
                    record["id"] = record_id
                    records.append(self.normalize_record_for_read(record))
        return records

    def query(
        self,
        *,
        query_vector: Optional[list[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[list[str]] = None,
        with_vector: bool = False,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> list[Dict[str, Any]]:
        coll = self.get_collection()
        vectordb_filter = self.compile_filter(filter)

        if query_vector or sparse_query_vector:
            result = coll.search_by_vector(
                index_name="default",
                dense_vector=query_vector,
                sparse_vector=sparse_query_vector,
                limit=limit,
                offset=offset,
                filters=vectordb_filter,
                output_fields=output_fields,
            )
        elif order_by:
            result = coll.search_by_scalar(
                index_name="default",
                field=order_by,
                order="desc" if order_desc else "asc",
                limit=limit,
                offset=offset,
                filters=vectordb_filter,
                output_fields=output_fields,
            )
        else:
            result = coll.search_by_random(
                index_name="default",
                limit=limit,
                offset=offset,
                filters=vectordb_filter,
                output_fields=output_fields,
            )

        records: list[Dict[str, Any]] = []
        for item in result.data:
            record = dict(item.fields) if item.fields else {}
            record["id"] = item.id
            record["_score"] = item.score if item.score is not None else 0.0
            record = self.normalize_record_for_read(record)
            if not with_vector:
                record.pop("vector", None)
                record.pop("sparse_vector", None)
            records.append(record)
        return records

    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100000,
    ) -> int:
        coll = self.get_collection()
        delete_ids = list(ids or [])
        if not delete_ids and filter is not None:
            matched = self.query(filter=filter, limit=limit, with_vector=True)
            delete_ids = [record["id"] for record in matched if record.get("id")]

        if not delete_ids:
            return 0

        coll.delete_data(delete_ids)
        return len(delete_ids)

    def count(self, filter: Optional[Dict[str, Any] | FilterExpr] = None) -> int:
        coll = self.get_collection()
        result = coll.aggregate_data(
            index_name="default",
            op="count",
            filters=self.compile_filter(filter),
        )
        return result.agg.get("_total", 0)

    def clear(self) -> bool:
        self.get_collection().delete_all_data()
        return True


class LocalCollectionAdapter(CollectionAdapter):
    """Adapter for local embedded vectordb backend."""

    DEFAULT_LOCAL_PROJECT_NAME = "vectordb"

    def __init__(self, collection_name: str, project_path: str):
        super().__init__(collection_name=collection_name)
        self.mode = "local"
        self._project_path = project_path

    @classmethod
    def from_config(cls, config):
        project_path = (
            str(Path(config.path) / cls.DEFAULT_LOCAL_PROJECT_NAME) if config.path else ""
        )
        return cls(collection_name=config.name or "context", project_path=project_path)

    def _collection_path(self) -> str:
        if not self._project_path:
            return ""
        return str(Path(self._project_path) / self._collection_name)

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        collection_path = self._collection_path()
        if not collection_path:
            return
        meta_path = os.path.join(collection_path, "collection_meta.json")
        if os.path.exists(meta_path):
            self._collection = get_or_create_local_collection(path=collection_path)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        collection_path = self._collection_path()
        if collection_path:
            os.makedirs(collection_path, exist_ok=True)
        return get_or_create_local_collection(meta_data=meta, path=collection_path)


class HttpCollectionAdapter(CollectionAdapter):
    """Adapter for remote HTTP vectordb project."""

    def __init__(self, host: str, port: int, project_name: str, collection_name: str):
        super().__init__(collection_name=collection_name)
        self.mode = "http"
        self._host = host
        self._port = port
        self._project_name = project_name

    @classmethod
    def from_config(cls, config):
        if not config.url:
            raise ValueError("HTTP backend requires a valid URL")
        host, port = _parse_url(config.url)
        return cls(
            host=host,
            port=port,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
        )

    def _meta(self) -> Dict[str, Any]:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }

    def _remote_has_collection(self) -> bool:
        raw = list_vikingdb_collections(
            host=self._host,
            port=self._port,
            project_name=self._project_name,
        )
        return self._collection_name in _normalize_collection_names(raw)

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        if not self._remote_has_collection():
            return
        self._collection = Collection(
            HttpCollection(
                ip=self._host,
                port=self._port,
                meta_data=self._meta(),
            )
        )

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        payload = dict(meta)
        payload.update(self._meta())
        return get_or_create_http_collection(
            host=self._host,
            port=self._port,
            meta_data=payload,
        )


class VolcengineCollectionAdapter(CollectionAdapter):
    """Adapter for Volcengine-hosted VikingDB."""

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
        super().__init__(collection_name=collection_name)
        self.mode = "volcengine"
        self._ak = ak
        self._sk = sk
        self._region = region
        self._host = host
        self._project_name = project_name

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

    def _meta(self) -> Dict[str, Any]:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }

    def _config(self) -> Dict[str, Any]:
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

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        candidate = self._new_collection_handle()
        meta = candidate.get_meta_data() or {}
        if meta and meta.get("CollectionName"):
            self._collection = candidate

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        payload = dict(meta)
        payload.update(self._meta())
        return get_or_create_volcengine_collection(
            config=self._config(),
            meta_data=payload,
        )

    def sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
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

    def normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("uri", "parent_uri"):
            value = record.get(key)
            if isinstance(value, str) and not value.startswith("viking://"):
                stripped = value.strip("/")
                if stripped:
                    record[key] = f"viking://{stripped}"
        return record


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
    def from_config(cls, config):
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

    def sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
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

    def normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("uri", "parent_uri"):
            value = record.get(key)
            if isinstance(value, str) and not value.startswith("viking://"):
                stripped = value.strip("/")
                if stripped:
                    record[key] = f"viking://{stripped}"
        return record


_ADAPTER_REGISTRY: dict[str, type[CollectionAdapter]] = {
    "local": LocalCollectionAdapter,
    "http": HttpCollectionAdapter,
    "volcengine": VolcengineCollectionAdapter,
    "vikingdb": VikingDBPrivateCollectionAdapter,
}


def create_collection_adapter(config) -> CollectionAdapter:
    """Unified factory entrypoint for backend-specific collection adapters."""
    adapter_cls = _ADAPTER_REGISTRY.get(config.backend)
    if adapter_cls is None:
        raise ValueError(
            f"Vector backend {config.backend} is not supported. "
            f"Available backends: {sorted(_ADAPTER_REGISTRY)}"
        )
    return adapter_cls.from_config(config)
