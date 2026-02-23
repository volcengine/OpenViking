# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, List, Optional, Tuple

from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb.store.data import DeltaRecord


class VikingIndex(IIndex):
    """
    VikingIndex implementation of IIndex interface.
    """

    def __init__(self, index_id: str):
        """
        Initialize VikingIndex with index ID.
        
        Args:
            index_id: Index ID
        """
        self.index_id = index_id

    def upsert_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError('vikingdb client index class not support this')

    def delete_data(self, delta_list: List[DeltaRecord]):
        raise NotImplementedError('vikingdb client index class not support this')

    def search(
        self,
        query_vector: Optional[List[float]],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        raise NotImplementedError('vikingdb client index class not support this')

    def aggregate(
        self,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError('vikingdb client index class not support this')

    def update(
        self, scalar_index: Optional[Any], description: Optional[str]
    ):
        raise NotImplementedError('vikingdb client index class not support this')

    def get_meta_data(self):
        raise NotImplementedError('vikingdb client index class not support this')

    def close(self):
        raise NotImplementedError('vikingdb client index class not support this')

    def drop(self):
        raise NotImplementedError('vikingdb client index class not support this')

    def get_newest_version(self) -> Any:
        raise NotImplementedError('vikingdb client index class not support this')

    def need_rebuild(self) -> bool:
        raise NotImplementedError('vikingdb client index class not support this')