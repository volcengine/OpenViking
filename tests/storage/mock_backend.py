from typing import Any, Dict, List, Optional
from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.result import AggregateResult, SearchResult
from openviking.storage.vectordb.index.index import IIndex

from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking.storage.vectordb.collection.collection import Collection

class MockCollectionAdapter(CollectionAdapter):
    """
    Mock adapter for testing dynamic loading.
    Inherits from CollectionAdapter and wraps MockCollection.
    """
    def __init__(self, collection_name: str, custom_param1: str = "", custom_param2: int = 0):
        super().__init__(collection_name=collection_name)
        self.mode = "mock"
        self.custom_param1 = custom_param1
        self.custom_param2 = custom_param2

    @classmethod
    def from_config(cls, config: Any) -> "MockCollectionAdapter":
        custom_params = getattr(config, "custom_params", {})
        return cls(
            collection_name=config.name or "mock_collection",
            custom_param1=custom_params.get("custom_param1", ""),
            custom_param2=custom_params.get("custom_param2", 0)
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is None:
             # Create a dummy collection wrapping MockCollection
             self._collection = MockCollection(self.custom_param1, self.custom_param2)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        return MockCollection(self.custom_param1, self.custom_param2)

class MockCollection(ICollection):
    def __init__(self, custom_param1: str, custom_param2: int, meta_data: Optional[Dict[str, Any]] = None, **kwargs):
        super().__init__()
        self.meta_data = meta_data if meta_data is not None else {}
        
        self.custom_param1 = custom_param1
        self.custom_param2 = custom_param2
        
        # Store extra kwargs (including host/headers if passed but not used explicitly)
        self.kwargs = kwargs
        
        # Verify that we can access values passed during initialization
        if self.meta_data and "test_verification" in self.meta_data:
            print(f"MockCollection initialized with custom_param1={self.custom_param1}, custom_param2={self.custom_param2}, kwargs={kwargs}")
        
    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        raise NotImplementedError("MockCollection.update is not supported")

    def get_meta_data(self):
        raise NotImplementedError("MockCollection.get_meta_data is not supported")

    def close(self):
        # No-op for mock
        pass

    def drop(self):
        # No-op for mock
        pass

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        raise NotImplementedError("MockCollection.create_index is not supported")

    def has_index(self, index_name: str) -> bool:
        raise NotImplementedError("MockCollection.has_index is not supported")

    def get_index(self, index_name: str) -> Optional[IIndex]:
        raise NotImplementedError("MockCollection.get_index is not supported")

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
        raise NotImplementedError("MockCollection.search_by_vector is not supported")

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
        raise NotImplementedError("MockCollection.search_by_keywords is not supported")

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError("MockCollection.search_by_id is not supported")

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str],
        image: Optional[Any],
        video: Optional[Any],
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError("MockCollection.search_by_multimodal is not supported")

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError("MockCollection.search_by_random is not supported")

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
        raise NotImplementedError("MockCollection.search_by_scalar is not supported")

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        raise NotImplementedError("MockCollection.update_index is not supported")

    def get_index_meta_data(self, index_name: str):
        raise NotImplementedError("MockCollection.get_index_meta_data is not supported")

    def list_indexes(self):
        raise NotImplementedError("MockCollection.list_indexes is not supported")

    def drop_index(self, index_name: str):
        raise NotImplementedError("MockCollection.drop_index is not supported")

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl=0):
        raise NotImplementedError("MockCollection.upsert_data is not supported")

    def fetch_data(self, primary_keys: List[Any]):
        raise NotImplementedError("MockCollection.fetch_data is not supported")

    def delete_data(self, primary_keys: List[Any]):
        raise NotImplementedError("MockCollection.delete_data is not supported")

    def delete_all_data(self):
        raise NotImplementedError("MockCollection.delete_all_data is not supported")

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        raise NotImplementedError("MockCollection.aggregate_data is not supported")