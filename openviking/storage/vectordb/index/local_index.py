# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import openviking.storage.vectordb.engine as engine
from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord
from openviking.storage.vectordb.utils.constants import IndexFileMarkers
from openviking.storage.vectordb.utils.data_processor import DataProcessor
from openviking_cli.utils.logger import default_logger as logger


def normalize_vector(vector: List[float]) -> List[float]:
    """Perform L2 normalization on a vector.

    Args:
        vector: Input vector

    Returns:
        Normalized vector
    """
    if not vector:
        return vector

    # Calculate L2 norm
    norm = math.sqrt(sum(x * x for x in vector))

    # Avoid division by zero
    if norm == 0:
        return vector

    # Normalize
    return [x / norm for x in vector]


class IndexEngineProxy:
    """Proxy wrapper for the underlying index engine with vector normalization support.

    This class wraps the low-level IndexEngine implementation and provides:
    - Optional L2 normalization of vectors before indexing/search
    - Unified interface for search, data manipulation, and persistence operations
    - Conversion between application-level data structures and engine-level requests

    The proxy enables transparent vector normalization when configured, which is
    useful for distance metrics like cosine similarity that require normalized vectors.

    Attributes:
        index_engine: The underlying IndexEngine instance (C++ backend)
        normalize_vector_flag (bool): Whether to apply L2 normalization to vectors
    """

    def __init__(self, index_path_or_json: str, normalize_vector_flag: bool = False):
        """Initialize the index engine proxy.

        Args:
            index_path_or_json (str): Either a file path to load an existing index,
                or a JSON configuration string to create a new index.
            normalize_vector_flag (bool): If True, all vectors will be L2-normalized
                before being added to the index or used for search. Defaults to False.
        """
        self.index_engine: Optional[engine.IndexEngine] = engine.IndexEngine(index_path_or_json)
        self.normalize_vector_flag = normalize_vector_flag

    def search(
        self,
        query_vector: List[float],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        if not self.index_engine:
            raise RuntimeError("Index engine not initialized")

        req = engine.SearchRequest()
        if query_vector:
            # If normalization is enabled, normalize the query vector
            if self.normalize_vector_flag:
                query_vector = normalize_vector(query_vector)
            req.query = query_vector
        req.topk = limit

        if filters is None:
            filters = {}
        req.dsl = json.dumps(filters)

        if sparse_raw_terms and sparse_values:
            req.sparse_raw_terms = sparse_raw_terms
            req.sparse_values = sparse_values

        search_result = self.index_engine.search(req)
        labels = search_result.labels
        scores = search_result.scores
        return labels, scores

    def add_data(self, cands_list: List[CandidateData]):
        if not self.index_engine:
            raise RuntimeError("Index engine not initialized")

        add_req_list = [engine.AddDataRequest() for _ in range(len(cands_list))]
        for i, data in enumerate(cands_list):
            add_req_list[i].label = data.label
            # If normalization is enabled, normalize the vector
            if self.normalize_vector_flag and data.vector:
                add_req_list[i].vector = normalize_vector(data.vector)
            else:
                add_req_list[i].vector = data.vector
            if data.sparse_raw_terms and data.sparse_values:
                add_req_list[i].sparse_raw_terms = data.sparse_raw_terms
                add_req_list[i].sparse_values = data.sparse_values
            add_req_list[i].fields_str = data.fields
        self.index_engine.add_data(add_req_list)

    def upsert_data(self, delta_list: List[DeltaRecord]):
        if not self.index_engine:
            raise RuntimeError("Index engine not initialized")

        add_req_list = [engine.AddDataRequest() for _ in range(len(delta_list))]
        for i, data in enumerate(delta_list):
            add_req_list[i].label = data.label
            # If normalization is enabled, normalize the vector
            if self.normalize_vector_flag and data.vector:
                add_req_list[i].vector = normalize_vector(data.vector)
            else:
                add_req_list[i].vector = data.vector
            if data.sparse_raw_terms and data.sparse_values:
                add_req_list[i].sparse_raw_terms = data.sparse_raw_terms
                add_req_list[i].sparse_values = data.sparse_values
            add_req_list[i].fields_str = data.fields
            add_req_list[i].old_fields_str = data.old_fields
        self.index_engine.add_data(add_req_list)

    def delete_data(self, delta_list: List[DeltaRecord]):
        if not self.index_engine:
            raise RuntimeError("Index engine not initialized")

        del_req_list = [engine.DeleteDataRequest() for _ in range(len(delta_list))]
        for i, data in enumerate(delta_list):
            del_req_list[i].label = data.label
            del_req_list[i].old_fields_str = data.old_fields
        self.index_engine.delete_data(del_req_list)

    def dump(self, path: str) -> int:
        if not self.index_engine:
            return -1
        return self.index_engine.dump(path)

    def get_update_ts(self) -> int:
        """Get the last update timestamp of the index.

        Returns:
            int: Nanosecond timestamp of the last modification to the index.
        """
        if not self.index_engine:
            return 0
        state_result = self.index_engine.get_state()
        return state_result.update_timestamp

    def get_data_count(self) -> int:
        """Get the number of data records currently in the index.

        Returns:
            int: Total count of active (non-deleted) records in the index.
        """
        if not self.index_engine:
            return 0
        state_result = self.index_engine.get_state()
        return state_result.data_count

    def drop(self):
        """Release the index engine resources.

        Sets the engine reference to None, allowing garbage collection
        of the underlying C++ index object.
        """
        self.index_engine = None


class LocalIndex(IIndex):
    """Base class for local (in-process) index implementations.

    LocalIndex provides a Python wrapper around the C++ IndexEngine, handling:
    - Vector normalization based on index configuration
    - Metadata management and updates
    - Search operations with filtering and aggregation
    - Data lifecycle (upsert, delete, close, drop)

    This class serves as the base for both VolatileIndex (in-memory) and
    PersistentIndex (disk-backed with versioning).

    Attributes:
        engine_proxy (IndexEngineProxy): Proxy to the underlying index engine
        meta: Index metadata including configuration and schema
    """

    def __init__(self, index_path_or_json: str, meta: Any):
        """Initialize a local index instance.

        Args:
            index_path_or_json (str): Path to index files or JSON configuration
            meta: Index metadata object containing configuration
        """
        # Get the vector normalization flag from meta
        normalize_vector_flag = meta.inner_meta.get("VectorIndex", {}).get("NormalizeVector", False)
        self.engine_proxy: Optional[IndexEngineProxy] = IndexEngineProxy(
            index_path_or_json, normalize_vector_flag
        )
        self.meta = meta
        self.field_type_converter = DataProcessor(self.meta.collection_meta.fields_dict)
        pass

    def update(
        self,
        scalar_index: Optional[Union[List[str], Dict[str, Any]]],
        description: Optional[str],
    ):
        meta_data: Dict[str, Any] = {}
        if scalar_index:
            meta_data["ScalarIndex"] = scalar_index
        if description:
            meta_data["Description"] = description
        if not meta_data:
            return
        self.meta.update(meta_data)

    def get_meta_data(self):
        return self.meta.get_meta_data()

    def upsert_data(self, delta_list: List[DeltaRecord]):
        if self.engine_proxy:
            self.engine_proxy.upsert_data(self._convert_delta_list_for_index(delta_list))

    def delete_data(self, delta_list: List[DeltaRecord]):
        if self.engine_proxy:
            self.engine_proxy.delete_data(self._convert_delta_list_for_index(delta_list))

    def search(
        self,
        query_vector: Optional[List[float]],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        if self.engine_proxy and query_vector is not None:
            # Handle default values
            if filters is None:
                filters = {}
            if sparse_raw_terms is None:
                sparse_raw_terms = []
            if sparse_values is None:
                sparse_values = []

            if self.field_type_converter and filters is not None:
                filters = self.field_type_converter.convert_filter_for_index(filters)
            return self.engine_proxy.search(
                query_vector, limit, filters, sparse_raw_terms, sparse_values
            )
        return [], []

    def aggregate(
        self,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.engine_proxy or not self.engine_proxy.index_engine:
            return {}

        extra_json = ""
        try:
            req = engine.SearchRequest()
            # CounterOp doesn't need a query vector
            req.topk = 1
            if filters is None:
                filters = {}
            if self.field_type_converter and filters is not None:
                filters = self.field_type_converter.convert_filter_for_index(filters)
            req.dsl = json.dumps(filters)

            logger.debug(f"aggregate DSL: {filters}")
            search_result = self.engine_proxy.index_engine.search(req)
            extra_json = search_result.extra_json
            logger.debug(f"aggregate extra_json: {extra_json}")
        except Exception as e:
            logger.error(f"Aggregation operation failed: {e}")
            return {}

        # Parse extra_json to get aggregation results
        agg_data = {}
        if extra_json:
            try:
                agg_data = json.loads(extra_json)
                logger.debug(f"aggregate parsed agg_data: {agg_data}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse aggregation results: {e}")
                return {}
        else:
            logger.warning("Aggregation results not available: extra_json is empty")
            return {}

        return agg_data

    def close(self):
        pass

    def drop(self):
        if self.engine_proxy:
            self.engine_proxy.drop()
        self.meta = None

    def get_newest_version(self) -> Union[int, str, Any]:
        return 0

    def need_rebuild(self) -> bool:
        """Determine if the index needs rebuilding.

        When delete operations reach a certain proportion, the index needs to be rebuilt to reclaim space.

        Returns:
            bool: True indicates rebuild is needed
        """
        return False

    def get_data_count(self) -> int:
        """Get the number of data entries in the index."""
        if self.engine_proxy:
            return self.engine_proxy.get_data_count()
        return 0

    def _convert_delta_list_for_index(self, delta_list: List[DeltaRecord]) -> List[DeltaRecord]:
        if not self.field_type_converter:
            return delta_list
        converted: List[DeltaRecord] = []
        for data in delta_list:
            item = DeltaRecord(type=data.type)
            item.label = data.label
            item.vector = list(data.vector) if data.vector else []
            item.sparse_raw_terms = list(data.sparse_raw_terms) if data.sparse_raw_terms else []
            item.sparse_values = list(data.sparse_values) if data.sparse_values else []
            item.fields = (
                self.field_type_converter.convert_fields_for_index(data.fields)
                if data.fields
                else data.fields
            )
            item.old_fields = (
                self.field_type_converter.convert_fields_for_index(data.old_fields)
                if data.old_fields
                else data.old_fields
            )
            converted.append(item)
        return converted

    def _convert_candidate_list_for_index(
        self, cands_list: List[CandidateData]
    ) -> List[CandidateData]:
        if not self.field_type_converter:
            return cands_list
        converted: List[CandidateData] = []
        for data in cands_list:
            item = CandidateData()
            item.label = data.label
            item.vector = list(data.vector) if data.vector else []
            item.sparse_raw_terms = list(data.sparse_raw_terms) if data.sparse_raw_terms else []
            item.sparse_values = list(data.sparse_values) if data.sparse_values else []
            item.fields = (
                self.field_type_converter.convert_fields_for_index(data.fields)
                if data.fields
                else data.fields
            )
            item.expire_ns_ts = data.expire_ns_ts
            converted.append(item)
        return converted


class VolatileIndex(LocalIndex):
    """In-memory index implementation without persistence.

    VolatileIndex stores all index data in memory for maximum performance.
    It does not persist data to disk, so all data is lost when the process terminates.

    Characteristics:
    - Fastest search performance (no disk I/O)
    - No persistence overhead
    - Data lost on process restart
    - Always requires rebuild from scratch on startup
    - Suitable for temporary indexes, testing, or when persistence is handled externally

    The index is created from an initial dataset and can be updated incrementally,
    but all changes exist only in memory.

    Attributes:
        engine_proxy (IndexEngineProxy): Proxy to the in-memory index engine
        meta: Index metadata and configuration
    """

    def __init__(self, name: str, meta: Any, cands_list: Optional[List[CandidateData]] = None):
        """Initialize a volatile (in-memory) index.

        Creates a new in-memory index and populates it with the initial dataset.

        Args:
            name (str): Name identifier for the index
            meta: Index metadata containing configuration (dimensions, distance metric, etc.)
            cands_list (list): Initial list of CandidateData records to populate the index.
                Defaults to None (empty index).

        Note:
            The index is immediately built in memory with the provided data.
            The element count limits are set based on the initial data size.
        """
        if cands_list is None:
            cands_list = []

        index_config_dict = meta.get_build_index_dict()
        version_int = int(time.time_ns())
        index_config_dict["VectorIndex"]["ElementCount"] = len(cands_list)
        index_config_dict["VectorIndex"]["MaxElementCount"] = len(cands_list)
        index_config_dict["UpdateTimeStamp"] = version_int
        index_config_json = json.dumps(index_config_dict)

        # Get the vector normalization flag from meta
        normalize_vector_flag = meta.inner_meta.get("VectorIndex", {}).get("NormalizeVector", False)

        # Directly initialize engine_proxy without calling parent __init__
        self.engine_proxy = IndexEngineProxy(index_config_json, normalize_vector_flag)
        self.meta = meta
        self.field_type_converter = DataProcessor(self.meta.collection_meta.fields_dict)
        self.engine_proxy.add_data(self._convert_candidate_list_for_index(cands_list))

    def need_rebuild(self) -> bool:
        """Determine if rebuild is needed.

        For volatile indexes, always returns True since rebuilding is cheap
        (all data is in memory) and can compact deleted records.

        When the amount of deleted data exceeds a threshold relative to current data,
        the index benefits from rebuilding to reclaim memory.

        Returns:
            bool: True indicates rebuild is recommended (always True for volatile indexes)
        """
        return True

    def get_newest_version(self) -> int:
        """Get the current update timestamp of the index.

        Returns:
            int: Nanosecond timestamp of the last modification.
        """
        if self.engine_proxy:
            return self.engine_proxy.get_update_ts()
        return 0


class PersistentIndex(LocalIndex):
    """Disk-backed index implementation with versioning and persistence.

    PersistentIndex maintains index data on disk with support for:
    - Multi-version snapshots (versioning by timestamp)
    - Incremental updates with delta tracking
    - Crash recovery through versioned checkpoints
    - Background persistence without blocking operations
    - Old version cleanup to manage disk space

    The index maintains multiple versions on disk, each identified by a timestamp.
    New versions are created during persist() operations when the index has been modified.

    Directory Structure:
        index_dir/
            versions/
                {timestamp1}/           # Immutable index snapshot
                {timestamp1}.write_done # Marker indicating snapshot is complete
                {timestamp2}/
                {timestamp2}.write_done
                ...

    Attributes:
        index_dir (str): Root directory for this index
        version_dir (str): Directory containing all version snapshots
        now_version (str): Current active version identifier
        engine_proxy (IndexEngineProxy): Proxy to the persistent index engine
        meta: Index metadata and configuration
    """

    def __init__(
        self,
        name: str,
        meta: Any,
        path: str,
        cands_list: Optional[List[CandidateData]] = None,
        force_rebuild: bool = False,
        initial_timestamp: Optional[int] = None,
    ):
        """Initialize a persistent index with versioning support.

        Either loads an existing index from disk or creates a new one.
        Handles version management and recovery.

        Args:
            name (str): Name identifier for the index (used as subdirectory name)
            meta: Index metadata containing configuration
            path (str): Parent directory path where index data will be stored
            cands_list (list): Initial data for creating a new index. Defaults to None.
            force_rebuild (bool): If True, rebuilds the index even if it exists.
                Defaults to False.
            initial_timestamp (Optional[int]): Timestamp to use if creating a new index
                from scratch. If None, uses current time. Useful for recovery scenarios.

        Process:
            1. Create directory structure if not exists
            2. Check for existing versions
            3. If no version exists or force_rebuild is True:
               - Build new index from cands_list
               - Persist as new version
            4. If version exists:
               - Load the latest version
               - Apply any pending delta updates from collection
        """
        if cands_list is None:
            cands_list = []

        self.index_dir = os.path.join(path, name)
        os.makedirs(self.index_dir, exist_ok=True)
        self.version_dir = os.path.join(self.index_dir, "versions")
        os.makedirs(self.version_dir, exist_ok=True)

        newest_version = self.get_newest_version()

        # At this point, there is no index, need to create a new one
        if not newest_version or force_rebuild:
            self._create_new_index(name, meta, cands_list, initial_timestamp)
        else:
            self.now_version = str(newest_version)

        index_path = os.path.join(self.version_dir, self.now_version)
        super().__init__(index_path, meta)
        # Remove scheduling logic, unified scheduling by collection layer

    def _create_new_index(
        self,
        name: str,
        meta: Any,
        cands_list: List[CandidateData],
        initial_timestamp: Optional[int] = None,
    ):
        """Create a new index from scratch."""
        self.field_type_converter = DataProcessor(meta.collection_meta.fields_dict)
        # Get the vector normalization flag from meta
        normalize_vector_flag = meta.inner_meta.get("VectorIndex", {}).get("NormalizeVector", False)

        version_int = initial_timestamp if initial_timestamp is not None else int(time.time_ns())
        version_str = str(version_int)
        index_config_dict = meta.get_build_index_dict()
        index_config_dict["VectorIndex"]["ElementCount"] = len(cands_list)
        index_config_dict["VectorIndex"]["MaxElementCount"] = len(cands_list)
        index_config_dict["UpdateTimeStamp"] = version_int
        index_config_json = json.dumps(index_config_dict)

        builder = IndexEngineProxy(index_config_json, normalize_vector_flag)
        build_index_path = os.path.join(self.version_dir, version_str)
        builder.add_data(self._convert_candidate_list_for_index(cands_list))

        dump_version_int = builder.dump(build_index_path)
        if dump_version_int > 0:
            dump_version_str = str(dump_version_int)
            new_index_path = os.path.join(self.version_dir, dump_version_str)
            shutil.move(build_index_path, new_index_path)
            Path(new_index_path + IndexFileMarkers.WRITE_DONE.value).touch()
            self.now_version = dump_version_str
        else:
            raise Exception("create {} index failed".format(name))

    def close(self):
        """Close the index and persist final state.

        Performs a graceful shutdown of the persistent index:
        1. Persists any uncommitted changes to disk
        2. Releases the index engine resources
        3. Cleans up old version files, keeping only the latest

        This ensures data durability and proper resource cleanup.
        After close(), the index cannot be used for further operations.
        """
        # 1. Persist latest data first
        self.persist()

        # 2. Release engine_proxy
        if self.engine_proxy:
            self.engine_proxy.drop()
            self.engine_proxy = None

        # 3. After engine is released, clean redundant index files, keeping only the latest version
        try:
            newest_version = self.get_newest_version()
            if newest_version > 0:
                self._clean_index([str(newest_version)])
        except Exception as e:
            logger.error(f"Failed to clean index files during close: {e}")

        super().close()

    def persist(self) -> int:
        """Persist index data to disk as a new version.

        Creates a new versioned snapshot of the index if it has been modified
        since the last persistence. This enables:
        - Point-in-time recovery
        - Incremental backups
        - Rolling back to previous states

        Called periodically by the collection layer to persist the index.

        Returns:
            int: Version number (timestamp) after persistence, 0 if no persistence
                was needed (no changes) or if persistence failed.

        Process:
            1. Check if index has been modified (update_ts > newest_version)
            2. If modified:
               - Dump index to new timestamped directory
               - Mark snapshot as complete with .write_done file
               - Clean up old versions (keeps current and new)
            3. If not modified, return 0 (no-op)

        Note:
            This operation is expensive and should not be called too frequently.
            The collection layer schedules periodic persistence.
        """
        if self.engine_proxy:
            newest_version = int(self.get_newest_version())
            update_ts = self.engine_proxy.get_update_ts()
            if update_ts <= newest_version:
                return 0
            now_ns_ts = str(int(time.time_ns()))
            index_path = os.path.join(self.version_dir, now_ns_ts)
            os.makedirs(index_path, exist_ok=True)
            dump_version = self.engine_proxy.dump(index_path)
            if dump_version < 0:
                return 0
            # todo get dump timestamp
            dump_index_path = os.path.join(self.version_dir, str(dump_version))
            shutil.move(index_path, dump_index_path)
            Path(dump_index_path + ".write_done").touch()
            self._clean_index([self.now_version, str(dump_version)])
            return dump_version
        return 0

    def _clean_index(self, not_clean: List[str]):
        """Remove old index version files from disk.

        Cleans up obsolete index versions to reclaim disk space while preserving
        versions specified in not_clean.

        Args:
            not_clean (list): List of version identifiers (as strings) to preserve.
                Typically includes the current version and the newly created version.

        Process:
            1. Build a set of files/directories to preserve (versions + .write_done markers)
            2. Scan version_dir and remove anything not in the preserve set
            3. Handle both directories (index data) and files (markers)
        """
        not_clean_set = set()
        for file_name in not_clean:
            not_clean_set.add(file_name)
            not_clean_set.add(file_name + ".write_done")
        for file_name in os.listdir(self.version_dir):
            if file_name not in not_clean_set:
                path = os.path.join(self.version_dir, file_name)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)

    def get_newest_version(self) -> int:
        """Find the latest valid index version on disk.

        Scans the version directory for completed index snapshots and returns
        the most recent one based on timestamp.

        Returns:
            int: Timestamp of the newest valid version, or 0 if no valid versions exist.

        A version is considered valid if:
        - It has a corresponding .write_done marker file
        - The version directory exists
        - The version number is a valid integer timestamp

        Invalid or incomplete versions (without .write_done) are ignored.
        """
        if not os.path.exists(self.version_dir):
            return 0

        valid_versions = []
        for name in os.listdir(self.version_dir):
            version_path = os.path.join(self.version_dir, name)
            # Must be a directory
            if not os.path.isdir(version_path):
                continue

            # Must be an integer (timestamp)
            if not name.isdigit():
                continue

            # Must have corresponding .write_done file
            marker_path = version_path + IndexFileMarkers.WRITE_DONE.value
            if not os.path.exists(marker_path):
                continue

            valid_versions.append(int(name))

        if not valid_versions:
            return 0

        return max(valid_versions)

    def drop(self):
        """Permanently delete the index and all its versions.

        Removes the entire index directory tree from disk, including all
        versioned snapshots and metadata files.

        Warning:
            This operation is irreversible. All index data will be permanently lost.
        """
        # Remove scheduling deletion logic
        LocalIndex.drop(self)
        shutil.rmtree(self.index_dir)

    def need_rebuild(self) -> bool:
        """Determine if the index needs rebuilding.

        For persistent indexes, rebuilding is typically not needed as
        persistence handles compaction. Returns False to avoid unnecessary rebuilds.

        Returns:
            bool: False (persistent indexes don't require periodic rebuilds)

        Note:
            Subclasses could override this to implement deletion-ratio-based
            rebuild triggers if needed for space reclamation.
        """
        return False
