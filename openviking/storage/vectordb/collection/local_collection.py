# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import datetime
import json
import os
import random
import shutil
import time
from itertools import zip_longest
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
    UpsertDataResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb.index.local_index import PersistentIndex, VolatileIndex
from openviking.storage.vectordb.meta.collection_meta import CollectionMeta, create_collection_meta
from openviking.storage.vectordb.meta.index_meta import create_index_meta
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord
from openviking.storage.vectordb.store.store import OpType
from openviking.storage.vectordb.store.store_manager import StoreManager, create_store_manager
from openviking.storage.vectordb.utils import validation
from openviking.storage.vectordb.utils.config_utils import get_config_value
from openviking.storage.vectordb.utils.constants import (
    DEFAULT_INDEX_MAINTENANCE_SECONDS,
    DEFAULT_TTL_CLEANUP_SECONDS,
    ENV_INDEX_MAINTENANCE_SECONDS,
    ENV_TTL_CLEANUP_SECONDS,
    STORAGE_DIR_NAME,
    AggregateKeys,
    SpecialFields,
)
from openviking.storage.vectordb.utils.data_processor import DataProcessor
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking.storage.vectordb.utils.id_generator import generate_auto_id
from openviking.storage.vectordb.utils.str_to_uint64 import str_to_uint64
from openviking.storage.vectordb.vectorize.base import BaseVectorizer
from openviking.storage.vectordb.vectorize.vectorizer import VectorizerAdapter
from openviking.storage.vectordb.vectorize.vectorizer_factory import VectorizerFactory
from openviking_cli.utils.logger import default_logger as logger

# Use imported constants, no longer defined here
AUTO_ID_KEY = SpecialFields.AUTO_ID.value


def get_or_create_local_collection(
    meta_data: Optional[Dict[str, Any]] = None,
    path: str = "",
    vectorizer: Optional[BaseVectorizer] = None,
    config: Optional[Dict[str, Any]] = None,
):
    """Create or retrieve a local Collection.

    Args:
        meta_data: Collection metadata configuration
        path: Persistence path. If empty, creates an in-memory collection
        vectorizer: Vectorizer for embedding generation
        config: Configuration parameters, optional settings include:
            - "ttl_cleanup_seconds": Interval (in seconds) for TTL expiration data cleanup
            - "index_maintenance_seconds": Interval (in seconds) for index maintenance tasks
            If not provided, values will be obtained from environment variables or defaults

    Returns:
        Collection: Collection instance

    Examples:
        >>> # Using default configuration
        >>> collection = get_or_create_local_collection(meta_data={...})

        >>> # Custom configuration
        >>> collection = get_or_create_local_collection(
        ...     meta_data={...},
        ...     config={
        ...         "ttl_cleanup_seconds": 5,
        ...         "index_maintenance_seconds": 60
        ...     }
        ... )

        >>> # Configuration via environment variables
        >>> # export VECTORDB_TTL_CLEANUP_SECONDS=15
        >>> # export VECTORDB_INDEX_MAINTENANCE_SECONDS=45
        >>> collection = get_or_create_local_collection(meta_data={...})
    """
    if meta_data is None:
        meta_data = {}
    if meta_data and not validation.is_valid_collection_meta_data(meta_data):
        raise ValueError("invalid collection_meta")
    collection: ICollection
    if not path:
        meta = create_collection_meta(path, meta_data)
        vectorizer = (
            VectorizerFactory.create(meta.vectorize)
            if meta.vectorize and vectorizer is None
            else vectorizer
        )
        store_mgr = create_store_manager("local")
        collection = VolatileCollection(
            meta=meta, store=store_mgr, vectorizer=vectorizer, config=config
        )
        return Collection(collection)
    else:
        os.makedirs(path, exist_ok=True)
        meta_path = os.path.join(path, "collection_meta.json")
        meta = create_collection_meta(meta_path, meta_data)
        vectorizer = (
            VectorizerFactory.create(meta.vectorize)
            if meta.vectorize and vectorizer is None
            else vectorizer
        )
        storage_path = os.path.join(path, STORAGE_DIR_NAME)
        store_mgr = create_store_manager("local", storage_path)
        collection = PersistCollection(
            path=path, meta=meta, store=store_mgr, vectorizer=vectorizer, config=config
        )
        return Collection(collection)


class LocalCollection(ICollection):
    def __init__(
        self,
        meta: CollectionMeta,
        store_mgr: StoreManager,
        vectorizer: Optional[BaseVectorizer] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.indexes = ThreadSafeDictManager[IIndex]()
        self.meta: CollectionMeta = meta
        self.collection_name = ""

        self.ttl_cleanup_seconds = get_config_value(
            config, "ttl_cleanup_seconds", ENV_TTL_CLEANUP_SECONDS, DEFAULT_TTL_CLEANUP_SECONDS
        )
        self.index_maintenance_seconds = get_config_value(
            config,
            "index_maintenance_seconds",
            ENV_INDEX_MAINTENANCE_SECONDS,
            DEFAULT_INDEX_MAINTENANCE_SECONDS,
        )

        self.store_mgr: Optional[StoreManager] = store_mgr
        self.data_processor = DataProcessor(
            self.meta.fields_dict, collection_name=self.meta.collection_name
        )
        self.vectorizer_adapter = None
        if meta.vectorize and vectorizer:
            self.vectorizer_adapter = VectorizerAdapter(vectorizer, meta.vectorize)
            self.meta.vector_dim = self.vectorizer_adapter.get_dim()

        self.ttl_cleanup_job_id: Optional[str] = None
        self.index_manage_job_id: Optional[str] = None
        self.scheduler = BackgroundScheduler(
            executors={"default": {"type": "threadpool", "max_workers": 1}}
        )
        self.scheduler.start()

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        meta_data: Dict[str, Any] = {}
        if fields is not None:
            meta_data["Fields"] = fields
        if description is not None:
            meta_data["Description"] = description
        if not meta_data:
            return
        self.meta.update(meta_data)
        self.data_processor = DataProcessor(
            self.meta.fields_dict, collection_name=self.meta.collection_name
        )

    def get_meta_data(self):
        return self.meta.get_meta_data()

    def close(self):
        self._delete_scheduler_job()

        # Shutdown scheduler
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

        self.store_mgr = None

        # Close all indexes
        def close_index(name, index):
            try:
                index.close()
            except Exception as e:
                logger.warning(f"Failed to close index {name}: {e}")

        self.indexes.iterate(close_index)
        self.indexes.clear()

    def drop(self):
        self.close()

    # index interface
    def create_index(self, index_name: str, meta_data: Optional[Dict[str, Any]] = None):
        if meta_data is None:
            meta_data = {}
        if not self.store_mgr:
            raise RuntimeError("Store manager is not initialized")
        cands_list: List[CandidateData] = self.store_mgr.get_all_cands_data()
        index = self._new_index(index_name, meta_data, cands_list)
        self.indexes.set(index_name, index)
        self._delete_expire_delta_record()
        return index

    def has_index(self, index_name: str) -> bool:
        return self.indexes.has(index_name)

    def get_index(self, index_name: str) -> Optional[IIndex]:
        return self.indexes.get(index_name)

    def drop_index(self, index_name: str) -> None:
        index = self.indexes.remove(index_name)
        if index:
            index.drop()

    def get_indexes(self) -> Dict[str, IIndex]:
        return self.indexes.get_all()

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> None:
        index = self.indexes.get(index_name)
        if not index:
            return
        index.update(scalar_index, description)

    def get_index_meta_data(self, index_name: str) -> Optional[Dict[str, Any]]:
        index = self.indexes.get(index_name)
        if not index:
            return None
        return index.get_meta_data()

    def list_indexes(self) -> List[str]:
        return self.indexes.list_names()

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
        search_result = SearchResult()
        index = self.indexes.get(index_name)
        if not index:
            return search_result

        sparse_raw_terms = []
        sparse_values = []
        if sparse_vector and isinstance(sparse_vector, dict):
            sparse_raw_terms = list(sparse_vector.keys())
            sparse_values = list(sparse_vector.values())

        # Request more results to handle offset
        actual_limit = limit + offset
        label_list, scores_list = index.search(
            dense_vector or [], actual_limit, filters, sparse_raw_terms, sparse_values
        )

        # Apply offset by slicing the results
        if offset > 0:
            label_list = label_list[offset:]
            scores_list = scores_list[offset:]

        # Limit to requested size
        if len(label_list) > limit:
            label_list = label_list[:limit]
            scores_list = scores_list[:limit]

        pk_list = label_list
        fields_list = []
        if not output_fields:
            output_fields = list(self.meta.fields_dict.keys())
        if self.meta.primary_key or output_fields:
            if not self.store_mgr:
                raise RuntimeError("Store manager is not initialized")
            cands_list = self.store_mgr.fetch_cands_data(label_list)

            valid_indices = []
            for i, cand in enumerate(cands_list):
                if cand is not None:
                    valid_indices.append(i)
                else:
                    logger.warning(
                        f"Candidate data is None for label index {i} (label: {label_list[i] if i < len(label_list) else 'unknown'}), skipping."
                    )

            if len(valid_indices) < len(cands_list):
                cands_list = [cands_list[i] for i in valid_indices]
                pk_list = [pk_list[i] for i in valid_indices]
                scores_list = [scores_list[i] for i in valid_indices]

            cands_fields = [json.loads(cand.fields) for cand in cands_list]

            if self.meta.primary_key:
                pk_list = [
                    cands_field.get(self.meta.primary_key, "") for cands_field in cands_fields
                ]
            fields_list = [
                {field: cands_field.get(field, None) for field in output_fields}
                for cands_field in cands_fields
            ]
            if self.meta.vector_key:
                for i, cands in enumerate(cands_list):
                    fields_list[i][self.meta.vector_key] = cands.vector

        search_result.data = [
            SearchItemResult(id=pk, fields=fields, score=score)
            for pk, score, fields in zip_longest(pk_list, scores_list, fields_list)
        ]
        return search_result

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        if not self.store_mgr:
            raise RuntimeError("Store manager is not initialized")
        
        # Validate input ID
        if id is None:
            return SearchResult()
        
        # Handle empty string IDs
        if isinstance(id, str) and not id.strip():
            return SearchResult()
        
        try:
            pk = self.meta.primary_key
            label = str_to_uint64(str(id)) if pk != AUTO_ID_KEY else int(id)
        except (ValueError, OverflowError) as e:
            # Invalid ID format - return empty result instead of crashing
            return SearchResult()
        
        cands_list: List[CandidateData] = self.store_mgr.fetch_cands_data([label])
        if not cands_list or cands_list[0] is None:
            return SearchResult()
        cands = cands_list[0]
        sparse_vector = (
            dict(zip(cands.sparse_raw_terms, cands.sparse_values)) if cands.sparse_raw_terms else {}
        )

        return self.search_by_vector(
            index_name, cands.vector, limit, offset, filters, sparse_vector, output_fields
        )

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
        """Search using multimodal data by generating vectors and calling search_by_vector.

        Args:
            index_name: Name of the index to search
            text: Text data (optional)
            image: Image data (optional, not yet implemented)
            video: Video data (optional, not yet implemented)
            limit: Number of results to return
            offset: Number of results to skip
            filters: Filter conditions
            output_fields: List of fields to return

        Returns:
            SearchResult: Search results
        """
        if not self.vectorizer_adapter:
            raise ValueError("vectorizer is not initialized")

        # Currently mainly supports text vectorization
        if not text and not image and not video:
            raise ValueError("At least one of text, image, or video must be provided")

        dense_vector, sparse_vector = self.vectorizer_adapter.vectorize_one(
            text=text, image=image, video=video
        )
        return self.search_by_vector(
            index_name, dense_vector, limit, offset, filters, sparse_vector, output_fields
        )

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        dense_vector = [random.uniform(-1, 1) for _ in range(self.meta.vector_dim)]
        return self.search_by_vector(
            index_name, dense_vector, limit, offset, filters, None, output_fields
        )

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
        """Search by keywords by generating vectors and calling search_by_vector.

        Args:
            index_name: Name of the index to search
            keywords: List of keywords (optional)
            query: Query string (optional)
            limit: Number of results to return
            offset: Number of results to skip
            filters: Filter conditions
            output_fields: List of fields to return

        Returns:
            SearchResult: Search results
        """
        if not self.vectorizer_adapter:
            raise ValueError("vectorizer is not initialized")

        if not keywords and not query:
            raise ValueError("At least one of keywords or query must be provided")

        # Construct query text
        if query:
            query_text = query
        elif keywords:
            # Join keyword list into a string
            query_text = " ".join(keywords)
        else:
            raise ValueError("No valid query input provided")

        # Call vectorization interface to generate vectors
        dense_vector, sparse_vector = self.vectorizer_adapter.vectorize_one(text=query_text)

        return self.search_by_vector(
            index_name, dense_vector, limit, offset, filters, sparse_vector, output_fields
        )

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        new_filters = {
            "sorter": {
                "op": "sort",
                "field": field,
                "order": order,
                "topk": limit + offset,  # Request more to handle offset
            }
        }
        if filters:
            new_filters["filter"] = filters

        # Copy output_fields to avoid modifying the original list
        if output_fields is None:
            output_fields_copy = [field]
            remove_field = True
        else:
            output_fields_copy = list(output_fields)
            if field not in output_fields_copy:
                output_fields_copy.append(field)
                remove_field = True
            else:
                remove_field = False

        result = self.search_by_vector(
            index_name, None, limit, offset, new_filters, None, output_fields_copy
        )

        # Set the field value as the score and remove the field if needed
        for item in result.data:
            if item.fields and field in item.fields:
                item.score = item.fields[field]
                if remove_field:
                    item.fields.pop(field)

        return result

    # data interface
    def upsert_data(self, raw_data_list: List[Dict[str, Any]], ttl=0):
        result = UpsertDataResult()
        data_list = []

        for raw_data in raw_data_list:
            if self.data_processor:
                try:
                    data = self.data_processor.validate_and_process(raw_data)
                except ValueError as e:
                    logger.error(f"Data validation failed: {e}, raw_data: {raw_data}")
                    return result
            else:
                # Should not happen given init logic, but for safety
                data = raw_data
            data_list.append(data)

        dense_emb, sparse_emb = (
            self.vectorizer_adapter.vectorize_raw_data(data_list)
            if self.vectorizer_adapter
            else ([], [])
        )

        cands_list = [CandidateData() for _ in range(len(data_list))]
        pk = self.meta.primary_key
        vk = self.meta.vector_key
        svk = self.meta.sparse_vector_key
        for i, data in enumerate(data_list):
            if AUTO_ID_KEY in data:
                label = data[AUTO_ID_KEY]
            elif pk != AUTO_ID_KEY:
                label = str_to_uint64(str(data[pk]))
            else:
                label = generate_auto_id()
                data[AUTO_ID_KEY] = label

            cands_list[i].label = label
            if self.vectorizer_adapter:
                if dense_emb:
                    cands_list[i].vector = dense_emb[i]
                if sparse_emb:
                    cands_list[i].sparse_raw_terms = list(sparse_emb[i].keys())
                    cands_list[i].sparse_values = list(sparse_emb[i].values())
            else:
                cands_list[i].vector = data.pop(vk, None)
                if svk:
                    sparse_dict = data.pop(svk, None)
                    if sparse_dict and isinstance(sparse_dict, dict):
                        cands_list[i].sparse_raw_terms = list(sparse_dict.keys())
                        cands_list[i].sparse_values = list(sparse_dict.values())
            cands_list[i].fields = json.dumps(data)
            cands_list[i].expire_ns_ts = time.time_ns() + ttl * 1000000000 if ttl > 0 else 0

        if not self.store_mgr:
            raise RuntimeError("Store manager is not initialized")
        need_record_delta = True if self.indexes.count() > 0 else False
        delta_list = self.store_mgr.add_cands_data(cands_list, ttl, need_record_delta)

        def upsert_to_index(name, index):
            index.upsert_data(delta_list)

        self.indexes.iterate(upsert_to_index)

        if not self.vectorizer_adapter:
            for i, data in enumerate(data_list):
                data[vk] = list(cands_list[i].vector) if cands_list[i].vector else []

        if pk != AUTO_ID_KEY:
            primary_keys = [data.get(pk) for data in data_list]
        else:
            primary_keys = [data.label for data in cands_list]

        result.ids = primary_keys
        return result

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        result = FetchDataInCollectionResult()
        pk = self.meta.primary_key
        labels_list = (
            [str_to_uint64(str(key)) for key in primary_keys]
            if pk != AUTO_ID_KEY
            else [int(key) for key in primary_keys]
        )
        if not self.store_mgr:
            raise RuntimeError("Store manager is not initialized")
        cands_list: List[CandidateData] = self.store_mgr.fetch_cands_data(labels_list)
        vk = self.meta.vector_key
        svk = self.meta.sparse_vector_key
        raw_data_list: List[Optional[Dict[str, Any]]] = []
        for cand_data in cands_list:
            if not cand_data:
                raw_data_list.append(None)
                continue
            raw_data = json.loads(cand_data.fields)
            if not self.vectorizer_adapter:
                raw_data[vk] = list(cand_data.vector)
                if svk and cand_data.sparse_raw_terms and cand_data.sparse_values:
                    raw_data[svk] = dict(zip(cand_data.sparse_raw_terms, cand_data.sparse_values))
            raw_data = validation.fix_fields_data(raw_data, self.meta.fields_dict)
            raw_data_list.append(raw_data)

        for i, item_data in enumerate(raw_data_list):
            if not item_data:
                result.ids_not_exist.append(primary_keys[i])
                continue
            result.items.append(DataItem(id=primary_keys[i], fields=item_data))
        return result

    def delete_data(self, primary_keys: List[Any]):
        pk = self.meta.primary_key
        labels_list = (
            [str_to_uint64(str(key)) for key in primary_keys]
            if pk != AUTO_ID_KEY
            else [int(key) for key in primary_keys]
        )
        if not self.store_mgr:
            raise RuntimeError("Store manager is not initialized")
        need_record_delta = True if self.indexes.count() > 0 else False
        delta_list = self.store_mgr.delete_data(labels_list, need_record_delta)

        def delete_from_index(name, index):
            index.delete_data(delta_list)

        self.indexes.iterate(delete_from_index)

    def delete_all_data(self):
        """Delete all data and rebuild indexes (thread-safe).

        This method will:
        1. Save metadata for all indexes
        2. Delete all indexes
        3. Clear storage data
        4. Rebuild empty indexes using saved metadata

        Uses locks to ensure no concurrent read/write requests cause errors during the operation.
        """
        # Use get_all_with_lock() to ensure atomicity of the entire operation
        with self.indexes.get_all_with_lock() as indexes_dict:
            # 1. Save metadata and names for all indexes
            indexes_metadata = []
            for index_name, index in indexes_dict.items():
                try:
                    meta_data = index.get_meta_data()
                    indexes_metadata.append((index_name, meta_data))
                    logger.debug(f"Saved metadata for index: {index_name}")
                except Exception as e:
                    logger.error(f"Failed to get metadata for index {index_name}: {e}")

            # 2. Delete all indexes
            index_names = list(indexes_dict.keys())
            for index_name in index_names:
                try:
                    index = indexes_dict.pop(index_name, None)
                    if index:
                        index.drop()
                        logger.debug(f"Dropped index: {index_name}")
                except Exception as e:
                    logger.error(f"Failed to drop index {index_name}: {e}")

            # 3. Clear storage data
            try:
                if self.store_mgr:
                    self.store_mgr.clear()
                    logger.info(
                        "Storage cleared successfully", extra={"collection": self.collection_name}
                    )
            except Exception as e:
                logger.error(f"Failed to clear storage: {e}")
                raise

            # 4. Rebuild empty indexes using saved metadata
            for index_name, meta_data in indexes_metadata:
                try:
                    # Rebuild index with empty data list
                    empty_cands_list: List[CandidateData] = []
                    new_index = self._new_index(index_name, meta_data, empty_cands_list)
                    indexes_dict[index_name] = new_index
                    logger.info(f"Rebuilt index: {index_name}")
                except Exception as e:
                    logger.error(f"Failed to rebuild index {index_name}: {e}")
                    # Continue rebuilding other indexes, don't interrupt the process

            logger.info(f"delete_all_data completed. Rebuilt {len(indexes_dict)} indexes")

    def _delete_expire_delta_record(self):
        oldest_version = 0
        for index in self.indexes.get_all().values():
            index_version = index.get_newest_version()
            if index_version > 0 and (oldest_version == 0 or index_version < oldest_version):
                oldest_version = index_version
        if self.store_mgr:
            self.store_mgr.delete_delta_data_before_ts(oldest_version)

    def _expire_timeout_data(self):
        if not self.store_mgr:
            return
        delta_list = self.store_mgr.expire_data()

        def delete_from_index(name, index):
            index.delete_data(delta_list)

        self.indexes.iterate(delete_from_index)

    def _register_scheduler_job(self):
        if self.ttl_cleanup_seconds > 0:
            self.ttl_cleanup_job_id = str(time.time_ns())
            self.scheduler.add_job(
                self._expire_timeout_data,
                "interval",
                seconds=self.ttl_cleanup_seconds,
                id=self.ttl_cleanup_job_id,
            )
        self._register_index_manage_job()

    def _delete_scheduler_job(self):
        if self.ttl_cleanup_job_id and self.scheduler:
            try:
                self.scheduler.remove_job(self.ttl_cleanup_job_id)
            except Exception as e:
                logger.warning(
                    f"Failed to remove timeout scheduler job {self.ttl_cleanup_job_id}: {e}"
                )
            self.ttl_cleanup_job_id = None

        if self.index_manage_job_id and self.scheduler:
            try:
                self.scheduler.remove_job(self.index_manage_job_id)
                self.index_manage_job_id = None
            except Exception as e:
                logger.warning(
                    f"Failed to remove index_manage scheduler job {self.index_manage_job_id}: {e}"
                )

    def _register_index_manage_job(self):
        """Register scheduled task for index maintenance."""
        if not self.index_manage_job_id:
            self.index_manage_job_id = f"{time.time_ns()}_{self.collection_name}_index_manage"
        next_run_time = datetime.datetime.now() + datetime.timedelta(
            seconds=self.index_maintenance_seconds
        )
        self._rebuild_indexes_if_needed()
        self._persist_all_indexes()
        try:
            self.scheduler.add_job(
                self._register_index_manage_job,
                trigger="date",
                run_date=next_run_time,
                id=self.index_manage_job_id,
            )
        except Exception as e:
            logger.error(f"Failed to register rebuild scheduler job: {e}")

    def _rebuild_indexes_if_needed(self):
        """Check and rebuild indexes that need rebuilding.

        Iterates through all indexes. If index.need_rebuild() returns True, rebuilds that index.
        Rebuild process:
        1. Retrieve all data corresponding to the index
        2. Create a new index
        3. Atomically replace the old index (ThreadSafeDictManager ensures thread safety)
        4. Old index is automatically reclaimed by Python GC (don't manually close to avoid concurrency issues)
        """
        # Get snapshot of all indexes to avoid modification during iteration
        indexes_snapshot = self.indexes.get_all()

        for index_name, index in indexes_snapshot.items():
            try:
                # Check if the index needs rebuilding
                if hasattr(index, "need_rebuild") and callable(index.need_rebuild):
                    if index.need_rebuild():
                        self._rebuild_index(index_name, index)
            except Exception as e:
                logger.error(f"Error checking rebuild status for index {index_name}: {e}")

    def _rebuild_index(self, index_name: str, old_index: IIndex):
        """Rebuild a single index.

        Args:
            index_name: Name of the index
            old_index: Old index object
        """
        try:
            # 1. Retrieve all data
            if not self.store_mgr:
                raise RuntimeError("Store manager is not initialized")
            cands_list: List[CandidateData] = self.store_mgr.get_all_cands_data()

            # 2. Get index metadata
            meta_data = old_index.get_meta_data()

            # 3. Create new index (this process is safe and doesn't affect the old index)
            new_index = self._new_index(index_name, meta_data, cands_list, True)

            # 4. Atomically replace the old index (ThreadSafeDictManager ensures thread safety)
            self.indexes.set(index_name, new_index)

            # 5. Don't manually close the old index, let Python GC automatically reclaim it
            #    This avoids errors for threads currently using old_index
            #    The object will be automatically destructed when all references are released

        except Exception as e:
            logger.error(f"Failed to rebuild index {index_name}: {e}")
            # Rebuild failed, keep the old index unchanged

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        """Aggregate data on the specified index.

        Args:
            index_name: Name of the index
            op: Aggregation operation, currently only supports "count"
            field: Field name for grouping, None means return total count
            filters: Filter conditions before aggregation
            cond: Conditions after aggregation, e.g., {"gt": 10}

        Returns:
            AggregateResult: Object containing aggregation results
        """
        new_filters = {}
        sorter = {
            "op": "count",
        }
        if field:
            sorter["field"] = field
        if cond:
            sorter.update(cond)
        new_filters["sorter"] = sorter
        if filters:
            new_filters["filter"] = filters
        index = self.indexes.get(index_name)
        if not index:
            logger.warning(f"Index '{index_name}' does not exist")
            return AggregateResult(agg={}, op=op, field=field)

        # 2. Call index.aggregate to execute aggregation
        try:
            agg_data = index.aggregate(new_filters)
        except Exception as e:
            logger.error(f"Aggregation operation failed: {e}")
            return AggregateResult(agg={}, op=op, field=field)

        # 3. Convert format: CounterOp returns "__total_count__", documentation requires "_total"
        if not field:
            # Total count scenario
            if AggregateKeys.TOTAL_COUNT_INTERNAL.value in agg_data:
                agg_result = {
                    AggregateKeys.TOTAL_COUNT_EXTERNAL.value: agg_data[
                        AggregateKeys.TOTAL_COUNT_INTERNAL.value
                    ]
                }
            else:
                agg_result = {AggregateKeys.TOTAL_COUNT_EXTERNAL.value: 0}
        else:
            # Group count scenario: use directly
            agg_result = agg_data

        return AggregateResult(agg=agg_result, op=op, field=field)

    def _persist_all_indexes(self):
        """Persist all indexes (abstract method for subclass implementation)."""
        pass

    def _new_index(
        self,
        index_name: str,
        meta_data: Dict[str, Any],
        cands_list: List[CandidateData],
        force_rebuild: bool = False,
    ):
        raise NotImplementedError


class VolatileCollection(LocalCollection):
    def __init__(
        self,
        meta: CollectionMeta,
        store: StoreManager,
        vectorizer: Optional[BaseVectorizer] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(meta, store, vectorizer, config)
        LocalCollection._register_scheduler_job(self)

    def _new_index(
        self,
        index_name: str,
        meta_data: Dict[str, Any],
        cands_list: List[CandidateData],
        force_rebuild: bool = False,
    ):
        meta = create_index_meta(self.meta, "", meta_data)
        index = VolatileIndex(
            name=index_name,
            meta=meta,
            cands_list=cands_list,
        )
        return index

    def _persist_all_indexes(self):
        pass


class PersistCollection(LocalCollection):
    def __init__(
        self,
        path: str,
        meta: CollectionMeta,
        store: StoreManager,
        vectorizer: Optional[BaseVectorizer] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.collection_dir = path
        os.makedirs(self.collection_dir, exist_ok=True)
        self.index_dir = os.path.join(self.collection_dir, "index")
        os.makedirs(self.index_dir, exist_ok=True)
        super().__init__(meta, store, vectorizer, config)
        self._recover()
        LocalCollection._register_scheduler_job(self)  # TTL expiration data cleanup

    def _recover(self):
        index_names = [
            folder
            for folder in os.listdir(self.index_dir)
            if os.path.isdir(os.path.join(self.index_dir, folder))
        ]
        for index_name in index_names:
            meta_path = os.path.join(self.index_dir, index_name, "index_meta.json")
            if not os.path.exists(meta_path):
                logger.warning(
                    f"Index metadata file not found at {meta_path}, skipping recovery for index {index_name}"
                )
                continue
            meta = create_index_meta(self.meta, meta_path)
            # When recovering an existing index, pass initial_timestamp=0.
            # This ensures the index's base version starts at 0, allowing it to ingest
            # all data from the delta log (CandidateData) regardless of when that data was created.
            # If we used the default (current time), the index would ignore older data in the log.
            index = PersistentIndex(
                name=index_name, path=self.index_dir, meta=meta, initial_timestamp=0
            )
            newest_version = index.get_newest_version()
            if not self.store_mgr:
                raise RuntimeError("Store manager is not initialized")
            delta_list = self.store_mgr.get_delta_data_after_ts(newest_version)
            upsert_list: List[DeltaRecord] = []
            delete_list: List[DeltaRecord] = []
            for data in delta_list:
                if data.type == OpType.PUT.value:
                    if delete_list:
                        index.delete_data(delete_list)
                        delete_list = []
                    upsert_list.append(data)
                elif data.type == OpType.DEL.value:
                    if upsert_list:
                        index.upsert_data(upsert_list)
                        upsert_list = []
                    delete_list.append(data)
            if upsert_list:
                index.upsert_data(upsert_list)
            if delete_list:
                index.delete_data(delete_list)
            self.indexes.set(index_name, index)

    def _persist_all_indexes(self):
        """Persist all indexes.

        Iterates through all indexes. If they are PersistentIndex type, calls their persist() method.
        """
        self.flush_all_indexes()

    def close(self):
        """Close the collection and release resources."""
        self.flush_all_indexes()
        super().close()  # Call parent close (includes TTL scheduling deletion)

    def flush_all_indexes(self):
        """Manually trigger persistence of all indexes.

        Called when closing the collection or when immediate persistence is needed.

        Returns:
            int: Number of successfully persisted indexes
        """
        persisted_count = 0

        def persist_index(index_name, index):
            nonlocal persisted_count
            if hasattr(index, "persist") and callable(index.persist):
                try:
                    version = index.persist()
                    if version > 0:
                        persisted_count += 1
                except Exception as e:
                    logger.error(f"Failed to flush index {index_name}: {e}")

        self.indexes.iterate(persist_index)
        return persisted_count

    def _new_index(
        self,
        index_name: str,
        meta_data: Dict[str, Any],
        cands_list: List[CandidateData],
        force_rebuild: bool = False,
    ):
        new_index_dir = os.path.join(self.index_dir, index_name)
        os.makedirs(new_index_dir, exist_ok=True)
        meta_path = os.path.join(new_index_dir, "index_meta.json")
        meta = create_index_meta(self.meta, meta_path, meta_data)
        index = PersistentIndex(
            name=index_name,
            path=self.index_dir,
            meta=meta,
            cands_list=cands_list,
            force_rebuild=force_rebuild,
        )
        return index

    def drop(self):
        super().drop()
        shutil.rmtree(self.collection_dir)
