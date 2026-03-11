# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OceanBase vector database adapter (via pyobvector)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Optional import: pyobvector is required only when backend is "oceanbase"
try:
    from pyobvector.client.milvus_like_client import MilvusLikeClient
    from pyobvector.client.collection_schema import CollectionSchema, FieldSchema
    from pyobvector.client.schema_type import DataType
    from pyobvector.client.index_param import IndexParam, IndexParams, VecIndexType

    _PYOBVECTOR_AVAILABLE = True
except ImportError:
    _PYOBVECTOR_AVAILABLE = False
    MilvusLikeClient = None  # type: ignore
    CollectionSchema = None  # type: ignore
    FieldSchema = None  # type: ignore
    DataType = None  # type: ignore
    IndexParam = None  # type: ignore
    IndexParams = None  # type: ignore
    VecIndexType = None  # type: ignore


def _openviking_field_to_field_schema(field: Dict[str, Any], vector_dim: int) -> FieldSchema:
    """Convert OpenViking Fields entry to pyobvector FieldSchema."""
    name = field.get("FieldName", "")
    typ = (field.get("FieldType") or "").lower()
    is_primary = bool(field.get("IsPrimaryKey", False))

    if typ == "string" or typ == "path":
        return FieldSchema(name, DataType.VARCHAR, is_primary=is_primary, max_length=4096)
    if typ == "int64":
        return FieldSchema(name, DataType.INT64, is_primary=is_primary)
    if typ == "vector":
        dim = field.get("Dim") or vector_dim
        return FieldSchema(name, DataType.FLOAT_VECTOR, is_primary=False, dim=dim)
    if typ == "sparse_vector":
        return FieldSchema(name, DataType.SPARSE_FLOAT_VECTOR, is_primary=False)
    if typ == "date_time":
        return FieldSchema(name, DataType.INT64, is_primary=False)
    # default
    return FieldSchema(name, DataType.VARCHAR, is_primary=is_primary, max_length=4096)


def _build_oceanbase_schema(meta: Dict[str, Any], vector_dim: int) -> CollectionSchema:
    """Build pyobvector CollectionSchema from OpenViking collection meta."""
    if not _PYOBVECTOR_AVAILABLE or CollectionSchema is None or FieldSchema is None:
        raise RuntimeError("pyobvector is required for OceanBase backend. Install with: pip install pyobvector")

    fields_meta = meta.get("Fields", [])
    vector_dim = meta.get("Dimension") or vector_dim
    fields = []
    for f in fields_meta:
        if f.get("FieldName") == "AUTO_ID":
            continue
        fs = _openviking_field_to_field_schema(f, vector_dim)
        fields.append(fs)
    return CollectionSchema(fields=fields)


def _distance_to_metric(distance: str) -> str:
    """Map OpenViking distance_metric to pyobvector metric_type."""
    d = (distance or "cosine").lower()
    if d in ("l2", "ip", "cosine", "neg_ip"):
        return "neg_ip" if d == "cosine" else d  # OceanBase cosine often as neg_ip
    return "l2"


class OceanBaseCollection(ICollection):
    """ICollection implementation backed by OceanBase via pyobvector MilvusLikeClient."""

    def __init__(
        self,
        client: Any,
        collection_name: str,
        meta_data: Dict[str, Any],
        distance_metric: str = "cosine",
    ):
        if not _PYOBVECTOR_AVAILABLE:
            raise RuntimeError("pyobvector is required for OceanBase backend. Install with: pip install pyobvector")
        self._client = client
        self._collection_name = collection_name
        self._meta_data = dict(meta_data)
        self._distance_metric = _distance_to_metric(distance_metric)
        self._vector_dim = self._meta_data.get("Dimension", 0)

    def _table(self):
        return self._client.load_table(self._collection_name)

    def _filter_to_where(self, filters: Optional[Dict[str, Any]]):
        """Convert OpenViking filter dict to SQLAlchemy where clause list for pyobvector."""
        if not filters:
            return None
        from sqlalchemy import and_, or_

        table = self._table()

        def walk(expr):
            if not isinstance(expr, dict):
                return None
            op = expr.get("op")
            if op == "must":
                field = expr.get("field")
                conds = expr.get("conds", [])
                if field and conds is not None and field in table.c:
                    return table.c[field].in_(conds)
            elif op == "range":
                field = expr.get("field")
                if field not in table.c:
                    return None
                col = table.c[field]
                parts = []
                if "gte" in expr:
                    parts.append(col >= expr["gte"])
                if "gt" in expr:
                    parts.append(col > expr["gt"])
                if "lte" in expr:
                    parts.append(col <= expr["lte"])
                if "lt" in expr:
                    parts.append(col < expr["lt"])
                return and_(*parts) if parts else None
            elif op == "and":
                sub = [walk(c) for c in expr.get("conds", [])]
                sub = [s for s in sub if s is not None]
                return and_(*sub) if sub else None
            elif op == "or":
                sub = [walk(c) for c in expr.get("conds", [])]
                sub = [s for s in sub if s is not None]
                return or_(*sub) if sub else None
            return None

        clause = walk(filters)
        return [clause] if clause is not None else None

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        if fields:
            self._meta_data.update(fields)

    def get_meta_data(self) -> Dict[str, Any]:
        return dict(self._meta_data)

    def close(self):
        try:
            if hasattr(self._client, "engine") and self._client.engine:
                self._client.engine.dispose()
        except Exception as e:
            logger.warning("OceanBaseCollection close: %s", e)

    def drop(self):
        self._client.drop_collection(self._collection_name)

    # "default" is reserved in OceanBase; use a safe index name when creating index
    _OB_INDEX_NAME_FOR_DEFAULT = "ov_vector_idx"

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> Any:
        vec_meta = (meta_data.get("VectorIndex") or {})
        distance = vec_meta.get("Distance", "l2")
        metric = _distance_to_metric(distance)
        field_name = "vector"
        ob_index_name = self._OB_INDEX_NAME_FOR_DEFAULT if index_name == "default" else index_name
        index_params = IndexParams()
        index_params.add_index(
            field_name=field_name,
            index_type=VecIndexType.HNSW,
            index_name=ob_index_name,
            metric_type=metric,
        )
        self._client.create_index(self._collection_name, index_params)
        return None

    def has_index(self, index_name: str) -> bool:
        try:
            table = self._table()
            from sqlalchemy import inspect
            insp = inspect(self._client.engine)
            idxs = insp.get_indexes(self._collection_name)
            ob_name = self._OB_INDEX_NAME_FOR_DEFAULT if index_name == "default" else index_name
            return any(idx.get("name") == ob_name for idx in idxs)
        except Exception:
            return False

    def get_index(self, index_name: str) -> Optional[Any]:
        return None if not self.has_index(index_name) else object()

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: Optional[List[float]] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        flter = self._filter_to_where(filters)
        data = dense_vector if dense_vector is not None else sparse_vector
        if data is None:
            return SearchResult(data=[])
        fetch_limit = limit + offset if offset else limit
        search_params = {"metric_type": self._distance_metric}
        try:
            rows = self._client.search(
                self._collection_name,
                data=data,
                anns_field="vector",
                with_dist=True,
                flter=flter,
                limit=fetch_limit,
                output_fields=output_fields,
                search_params=search_params,
            )
        except Exception as e:
            logger.warning("OceanBase search failed: %s", e)
            return SearchResult(data=[])
        items = []
        for i, row in enumerate(rows):
            if offset and i < offset:
                continue
            if len(items) >= limit:
                break
            score = (
                row.get("score")
                or row.get("l2_distance")
                or row.get("inner_product")
                or row.get("cosine_distance")
            )
            if score is None and isinstance(row, dict) and len(row) > 0:
                # pyobvector may put distance as last column
                last_val = list(row.values())[-1]
                if isinstance(last_val, (int, float)):
                    score = last_val
            score = float(score) if score is not None else 0.0
            pk = self._meta_data.get("PrimaryKey", "id")
            row_id = row.get(pk) or row.get("id")
            fields = {
                k: v
                for k, v in row.items()
                if k not in (pk, "id", "score", "l2_distance", "inner_product", "cosine_distance")
                and not (isinstance(k, str) and "distance" in k.lower())
            }
            items.append(SearchItemResult(id=row_id, fields=fields, score=score))
        return SearchResult(data=items)

    def search_by_keywords(
        self,
        index_name: str,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        # Not supported without vectorizer; delegate to random with filter
        return self.search_by_random(index_name, limit, offset, filters, output_fields)

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        ids = [id] if not isinstance(id, (list, tuple)) else list(id)
        rows = self._client.get(self._collection_name, ids=ids, output_fields=output_fields)
        if not rows:
            return SearchResult(data=[])
        row = rows[0]
        vec = row.get("vector")
        if vec is not None:
            return self.search_by_vector(
                index_name, dense_vector=vec, limit=limit, offset=offset,
                filters=filters, output_fields=output_fields,
            )
        return SearchResult(data=[])

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str] = None,
        image: Optional[Any] = None,
        video: Optional[Any] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        return self.search_by_random(index_name, limit, offset, filters, output_fields)

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        flter = self._filter_to_where(filters)
        try:
            rows = self._client.query(
                self._collection_name,
                flter=flter,
                output_fields=output_fields,
            )
        except Exception as e:
            logger.warning("OceanBase query failed: %s", e)
            return SearchResult(data=[])
        pk = self._meta_data.get("PrimaryKey", "id")
        items = []
        for i, row in enumerate(rows):
            if i < offset:
                continue
            if len(items) >= limit:
                break
            row_id = row.get(pk) or row.get("id")
            fields = {k: v for k, v in row.items() if k != pk}
            items.append(SearchItemResult(id=row_id, fields=fields, score=None))
        return SearchResult(data=items)

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        from sqlalchemy import select, text as sql_text

        table = self._table()
        flter = self._filter_to_where(filters)
        if field not in table.c:
            return SearchResult(data=[])
        order_col = table.c[field]
        stmt = select(table).order_by(
            order_col.desc() if (order or "desc").lower() == "desc" else order_col.asc()
        ).limit(limit + offset).offset(offset)
        if flter:
            stmt = stmt.where(*flter)
        try:
            with self._client.engine.connect() as conn:
                res = conn.execute(stmt)
                rows = [dict(zip(res.keys(), row)) for row in res.fetchall()]
        except Exception as e:
            logger.warning("OceanBase search_by_scalar failed: %s", e)
            return SearchResult(data=[])
        pk = self._meta_data.get("PrimaryKey", "id")
        items = []
        for row in rows:
            row_id = row.get(pk) or row.get("id")
            fields = {k: v for k, v in row.items() if k != pk}
            score = row.get(field)
            items.append(SearchItemResult(id=row_id, fields=fields, score=score))
        return SearchResult(data=items)

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        pass

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        return {"IndexName": index_name}

    def list_indexes(self) -> List[str]:
        try:
            from sqlalchemy import inspect
            insp = inspect(self._client.engine)
            idxs = insp.get_indexes(self._collection_name)
            names = [idx.get("name") for idx in idxs if idx.get("name")]
            # Expose "default" to callers to match OpenViking convention
            if self._OB_INDEX_NAME_FOR_DEFAULT in names and "default" not in names:
                names = ["default"] + [n for n in names if n != self._OB_INDEX_NAME_FOR_DEFAULT]
            return names or ["default"]
        except Exception:
            return ["default"]

    def drop_index(self, index_name: str):
        ob_name = self._OB_INDEX_NAME_FOR_DEFAULT if index_name == "default" else index_name
        self._client.drop_index(self._collection_name, ob_name)

    def _default_for_field_type(self, field: Dict[str, Any]) -> Any:
        """Fill default values for required schema fields missing in row (OceanBase has no column default)."""
        typ = (field.get("FieldType") or "").lower()
        if typ == "sparse_vector":
            return {}
        if typ in ("int64", "date_time"):
            return 0
        if typ in ("string", "path"):
            return ""
        return None

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        if not data_list:
            return
        fields_meta = self._meta_data.get("Fields", [])
        for row in data_list:
            if "id" in row and row["id"] is not None:
                row["id"] = str(row["id"])
            # Fill schema fields missing in row to avoid "Field doesn't have a default value"
            for f in fields_meta:
                name = f.get("FieldName")
                if name and name not in row:
                    default = self._default_for_field_type(f)
                    if default is not None:
                        row[name] = default
        self._client.upsert(self._collection_name, data_list)

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        ids = [str(k) for k in primary_keys]
        try:
            rows = self._client.get(self._collection_name, ids=ids)
        except Exception as e:
            logger.warning("OceanBase get failed: %s", e)
            return FetchDataInCollectionResult(items=[], ids_not_exist=ids)
        pk = self._meta_data.get("PrimaryKey", "id")
        items = []
        for row in rows:
            row_id = row.get(pk) or row.get("id")
            fields = {k: v for k, v in row.items() if k != pk}
            items.append(DataItem(id=row_id, fields=fields))
        found = {item.id for item in items}
        ids_not_exist = [k for k in ids if k not in found]
        return FetchDataInCollectionResult(items=items, ids_not_exist=ids_not_exist)

    def delete_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return
        ids = [str(k) for k in primary_keys]
        self._client.delete(self._collection_name, ids=ids)

    def delete_all_data(self):
        self._client.delete(self._collection_name, ids=None, flter=None)

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        from sqlalchemy import select, func

        table = self._table()
        flter = self._filter_to_where(filters)
        stmt = select(func.count()).select_from(table)
        if flter:
            stmt = stmt.where(*flter)
        try:
            with self._client.engine.connect() as conn:
                res = conn.execute(stmt)
                total = res.scalar() or 0
        except Exception as e:
            logger.warning("OceanBase aggregate failed: %s", e)
            return AggregateResult(agg={}, op=op, field=field)
        return AggregateResult(agg={"_total": total}, op=op, field=field)


class OceanBaseCollectionAdapter(CollectionAdapter):
    """Adapter for OceanBase vector database (pyobvector)."""

    def __init__(
        self,
        collection_name: str,
        client: Any,
        distance_metric: str = "cosine",
    ):
        super().__init__(collection_name=collection_name)
        self.mode = "oceanbase"
        self._client = client
        self._distance_metric = distance_metric

    @classmethod
    def from_config(cls, config: Any) -> "OceanBaseCollectionAdapter":
        if not _PYOBVECTOR_AVAILABLE:
            raise RuntimeError(
                "OceanBase backend requires pyobvector. Install with: pip install pyobvector"
            )
        ob = config.oceanbase
        if not ob:
            raise ValueError("VectorDB oceanbase backend requires 'oceanbase' config")
        client = MilvusLikeClient(
            uri=ob.uri,
            user=ob.user,
            password=ob.password,
            db_name=ob.db_name,
        )
        return cls(
            collection_name=config.name or "context",
            client=client,
            distance_metric=getattr(config, "distance_metric", "cosine") or "cosine",
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        try:
            if self._client.has_collection(self._collection_name):
                meta = self._build_meta_from_existing()
                self._collection = Collection(
                    OceanBaseCollection(
                        client=self._client,
                        collection_name=self._collection_name,
                        meta_data=meta,
                        distance_metric=self._distance_metric,
                    )
                )
        except Exception as e:
            logger.debug("OceanBase load collection %s: %s", self._collection_name, e)

    def _build_meta_from_existing(self) -> Dict[str, Any]:
        """Build minimal meta from existing table (for read-only bind)."""
        from openviking.storage.collection_schemas import CollectionSchemas

        dim = getattr(self._client, "_vector_dim", None) or 1024
        return CollectionSchemas.context_collection(self._collection_name, dim)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        vector_dim = meta.get("Dimension", 0)
        schema = _build_oceanbase_schema(meta, vector_dim)
        self._client.create_collection(
            self._collection_name,
            schema=schema,
        )
        self._client._vector_dim = vector_dim
        icoll = OceanBaseCollection(
            client=self._client,
            collection_name=self._collection_name,
            meta_data=meta,
            distance_metric=self._distance_metric,
        )
        return Collection(icoll)
