# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import logging
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.bytedviking import BytedVikingClient
from openviking.storage.vectordb.bytedviking.viking_index import VikingIndex
from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.result import SearchResult, SearchItemResult
from openviking.storage.vectordb.index.index import IIndex, Index

logger = logging.getLogger(__name__)


class VikingCollection(ICollection):
    """
    Generic implementation of ICollection using reflection to avoid direct dependency
    on any specific viking client implementation.
    """

    def __init__(self, collection_name: str, config: Dict[str, Any]):
        """
        Initialize VikingCollection with configuration.
        """
        super().__init__()
        # Initialize BytedVikingClient and delegate its attributes to self
        self.byted_client = BytedVikingClient(collection_name, config)
        self.collection_name = collection_name

        # Check if the collection exists
        exists = self.byted_client.meta_client.exist_vikingdb(self.collection_name)
        if not exists:
            raise RuntimeError(f"Collection '{collection_name}' does not exist")


    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        # VikingDB currently doesn't have a direct "update collection metadata" API exposed in this way
        # except maybe through recreating or specific meta ops.
        # Leaving as NotImplemented or pass for now.
        raise NotImplementedError("update collection not supported in VikingCollection yet")

    def get_meta_data(self):
        """
        Get metadata for the collection.
        
        Returns:
            Dict[str, Any]: Collection metadata
        """
        try:
            # Use meta_client to get info
            err_msg, data = self.byted_client.meta_client.get_vikingdb(self.collection_name)
            if err_msg:
                raise RuntimeError(f"Failed to get meta data: {err_msg}")
            return data
        except Exception as e:
            raise RuntimeError(f"Failed to get meta data: {e}")

    def close(self):
        # VikingClient uses requests.Session, we might want to close it if exposed
        if hasattr(self.byted_client.db_client, "session"):
            self.byted_client.db_client.session.close()

    def drop(self):
        # err_msg, _ = self.meta_client.delete_vikingdb(self.collection_name)
        # if err_msg:
        #     raise RuntimeError(f"Failed to drop collection: {err_msg}")
        raise NotImplementedError("drop collection not supported in VikingCollection yet")


    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        """
        Create an index in the collection.
        
        Args:
            index_name: Name of the index to create
            meta_data: Dictionary containing index metadata
                - index_type: Type of index (e.g., "auto_hnsw")
                - distance: Distance metric (e.g., "ip", "l2")
                - shard_count: Number of shards (optional)
                - owner_name: Owner name (optional)
                - scale_up_ratio: Scale up ratio (optional)
                - viking_psm: Viking PSM (optional)
                - policy_type: Policy type (optional)
        
        Returns:
            IIndex: Created index object
        """
        # Extract parameters from meta_data
        index_type = meta_data.get("index_type", "auto_hnsw")
        distance = meta_data.get("distance", "ip")
        shard_count = meta_data.get("shard_count", 1)
        owner_name = meta_data.get("owner_name", "")
        scale_up_ratio = meta_data.get("scale_up_ratio", 0.0)
        viking_psm = meta_data.get("viking_psm", "")
        policy_type = meta_data.get("policy_type", 2)
        
        
        try:
            # Call meta_client.create_index to create the index
            err_msg, index = self.byted_client.meta_client.create_index(
                vikingdb_full_name = self.collection_name,
                index_name=index_name,
                index_type=index_type,
                distance=distance,
                owner_name=owner_name,
                scale_up_ratio=scale_up_ratio,
                shard_count=shard_count,
                viking_psm=viking_psm,
                policy_type=policy_type
            )
            
            if err_msg:
                # Check if index already exists
                if "already exists" in err_msg.lower():
                    # Index already exists, return it instead of raising an error
                    return self.get_index(index_name)
                raise RuntimeError(f"Failed to create index: {err_msg}")
            
            # Return the created index
            return index
        except Exception as e:
            # Check if index already exists
            if "already exists" in str(e).lower():
                # Index already exists, return it instead of raising an error
                return self.get_index(index_name)
            raise RuntimeError(f"Failed to create index: {e}")

    def has_index(self, index_name: str) -> bool:
        try:
            index = self.byted_client.meta_client.exist_index(self.collection_name, index_name)
            return index
        except Exception:
            # If any error occurs (e.g., index not found), return False
            return False

    def get_index(self, index_name: str) -> Optional[IIndex]:
        """
        Get an index by name.
        
        Args:
            index_name: Name of the index to get
            
        Returns:
            Optional[IIndex]: Index object if found, None otherwise
        """
        try:
            err_msg, index_info_str = self.byted_client.meta_client.get_index(
                vikingdb_full_name = self.collection_name,
                index_name = index_name
            )
            if err_msg:
                logger.warning(f"Failed to get index {index_name}: {err_msg}")
                return None
            
            # Parse index_info_str if it's a JSON string
            if isinstance(index_info_str, str):
                try:
                    index_info = json.loads(index_info_str)
                except json.JSONDecodeError as e:
                    logger.warning(f"Error parsing index info JSON: {e}")
                    return None
            else:
                index_info = index_info_str
            
            # Extract index ID
            index_id = index_info.get('id', index_name)
            print(f"get_index {index_name}: {index_id}")
            
            # Create a VikingIndex instance with just the index ID
            viking_index = VikingIndex(index_id)
            
            # Return an Index wrapper
            return viking_index
        except Exception as e:
            logger.warning(f"Error getting index {index_name}: {e}")
            return None

    def list_indexes(self):
        raise NotImplementedError("list indexes not supported in VikingCollection yet")

    def update_index(self, *args, **kwargs):
        raise NotImplementedError("update_index not supported in VikingCollection yet")

    def get_index_meta_data(self, *args, **kwargs):
        raise NotImplementedError("get_index_meta_data not supported in VikingCollection yet")

    def drop_index(self, index_name: str):
        """
        Delete an index from the collection.
        
        Args:
            index_name: Name of the index to delete
        """
        try:
            # Call meta_client.delete_index to delete the index
            err_msg, data = self.byted_client.meta_client.delete_index(
                vikingdb_full_name = self.collection_name,
                index_name = index_name
            )
            
            if err_msg:
                raise RuntimeError(f"Failed to drop index: {err_msg}")
            return data
        except Exception as e:
            raise RuntimeError(f"Failed to drop index: {e}")

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
        dsl_query = filters if filters else {}
        
        try:
            success, result, logid = self.byted_client.db_client.recall(
                vector=dense_vector,
                index=index_name,
                topk=limit,
                dsl_query=dsl_query,
                sparse_vec=sparse_vector,
                # Map other params...
            )
            
            if not success:
                error_info = result if isinstance(result, tuple) else "Unknown error"
                raise RuntimeError(f"Search failed: {error_info}, logid: {logid}")
            
            # Convert result to SearchResult
            # VikingDB result structure needs parsing.
            # Assuming result is list of items.
            # We need to construct SearchResult.
            
            # For this exercise, I'll return a raw SearchResult wrapper with empty data
            # In a real implementation, we map the fields.
            search_items = []
            if result:
                for item in result:
                    # Parse each item based on VikingDB's result structure
                    # Example parsing (adjust based on actual result structure):
                    search_item = SearchItemResult(
                        id=item.get('label_lower64'),
                        score=item.get('score'),
                        fields=item
                    )
                    search_items.append(search_item)
            
            return SearchResult(data=search_items)
        except Exception as e:
            raise RuntimeError(f"Search failed: {e}")

    def search_by_keywords(self, *args, **kwargs):
        raise NotImplementedError

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError("search_by_id not implemented for VikingCollection yet")

    def search_by_multimodal(self, *args, **kwargs):
        raise NotImplementedError("search_by_multimodal not supported")

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        dsl_query = filters if filters else {}
        success, result, logid = self.client.recall(
            vector=[], # Empty vector for random? Or need a dummy?
            # VikingDB might require vector even for random, or just is_random_recall=True
            index=index_name,
            topk=limit,
            dsl_query=dsl_query,
            is_random_recall=True
        )
        if not success:
             raise RuntimeError(f"Random search failed: {result}, logid: {logid}")
        
        # TODO: Parse 'result' into List[SearchItemResult]
        return SearchResult(
            data=[]
        )

    def search_by_scalar(self, *args, **kwargs):
        # Scalar search usually means filtering without vector scoring?
        # VikingDB recall requires vector?
        # If we can pass empty vector and rely on filters?
        raise NotImplementedError("search_by_scalar not supported directly, use filters in search_by_vector")

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        """
        Insert or update data in the collection.
        
        Args:
            data_list: List of data dictionaries to upsert
            ttl: Time-to-live in seconds for the data
            
        Returns:
            List of rowkeys for the upserted data
        """
        
        try:
           # Using simple_add_data from byted_client.db_client
            vikingdb_data_list = []
            for data_dict in data_list:  # data_list 是 List[Dict[str, Any]]
                # 如果需要设置 ttl，在这里添加
                if ttl > 0:
                    data_dict['ttl'] = ttl
                vikingdb_data_list.append(self.byted_client._VikingDbDataClass(data_dict=data_dict))
            
            
            msg, rowkeys = self.byted_client.db_client.simple_add_data(
                vikingdb_data=vikingdb_data_list,
            )
            if msg:
                raise RuntimeError(f"Upsert failed: {msg}")
            return rowkeys
        except Exception as e:
            raise RuntimeError(f"Upsert failed: {e}")


    def fetch_data(self, primary_keys: List[Any]):
        """
        Fetch data from the collection using primary keys.
        
        Args:
            primary_keys: List of primary keys to fetch data for
            
        Returns:
            List of fetched data
        """
        try:
            msg, data = self.byted_client.db_client.simple_get_data(
                datas=primary_keys
            )
            if msg:
                raise RuntimeError(f"Fetch failed: {msg}")
            return data
        except Exception as e:
            raise RuntimeError(f"Fetch failed: {e}")

    def delete_data(self, primary_keys: List[Any]):
        """
        Delete data from the collection using primary keys.
        
        Args:
            primary_keys: List of primary keys to delete data for
            
        Returns:
            List of rowkeys for the deleted data
        """
        try:
            # simple_del_data
            msg, rowkeys = self.byted_client.db_client.simple_del_data(datas=primary_keys)
            if msg:
                raise RuntimeError(f"Delete failed: {msg}")
            return rowkeys
        except Exception as e:
            raise RuntimeError(f"Delete failed: {e}")

    def delete_all_data(self):
        raise NotImplementedError("delete_all_data not supported efficiently")

    def aggregate_data(self, *args, **kwargs):
        raise NotImplementedError("aggregate_data not supported")