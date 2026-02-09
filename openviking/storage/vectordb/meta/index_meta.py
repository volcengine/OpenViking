# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import copy
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.meta.collection_meta import CollectionMeta
from openviking.storage.vectordb.meta.dict import IDict
from openviking.storage.vectordb.meta.local_dict import PersistentDict, VolatileDict
from openviking.storage.vectordb.utils import validation
from openviking.storage.vectordb.utils.data_processor import DataProcessor


def create_index_meta(
    collection_meta: CollectionMeta,
    path: Optional[str] = None,
    user_meta: Optional[Dict[str, Any]] = None,
) -> "IndexMeta":
    """Create an IndexMeta instance.

    Args:
        collection_meta (CollectionMeta): The metadata of the collection this index belongs to.
        path (Optional[str]): The path to store metadata. If None or empty, creates a volatile dictionary.
        user_meta (Optional[Dict[str, Any]]): The initial metadata provided by the user.

    Returns:
        IndexMeta: The created IndexMeta instance.

    Raises:
        ValueError: If the user_meta is invalid.
    """
    inner_meta = {}
    if user_meta:
        if not validation.is_valid_index_meta_data(user_meta, collection_meta.fields_dict):
            raise ValueError(
                "invalid index_meta {} fields_dict {}".format(
                    user_meta, collection_meta.fields_dict
                )
            )
        else:
            inner_meta = IndexMeta._build_inner_meta(user_meta, collection_meta)
    idict = PersistentDict(path, inner_meta) if path else VolatileDict(inner_meta)
    return IndexMeta(collection_meta, idict)


class IndexMeta:
    """Manages index metadata.

    Wraps an IDict instance to provide structured access and modification of index metadata.
    """

    def __init__(self, collection_meta: CollectionMeta, idict: IDict):
        """Initialize IndexMeta.

        Args:
            collection_meta (CollectionMeta): The metadata of the collection this index belongs to.
            idict (IDict): The underlying dictionary storage interface.
        """
        assert isinstance(idict, IDict), "idict must be a IDict"
        self.__idict = idict
        self.collection_meta = collection_meta
        self.inner_meta = self.__idict.get_raw()

    @staticmethod
    def _build_inner_meta(
        user_meta: Dict[str, Any], collection_meta: CollectionMeta
    ) -> Dict[str, Any]:
        """Build the internal metadata structure from user provided metadata.

        Args:
            user_meta (Dict[str, Any]): User provided metadata.
            collection_meta (CollectionMeta): The collection metadata.

        Returns:
            Dict[str, Any]: The internal metadata structure.
        """
        inner_meta = copy.deepcopy(user_meta)
        fields_dict = collection_meta.fields_dict
        scalar_index: List[Dict[str, str]] = []
        if "ScalarIndex" in inner_meta:
            converter = DataProcessor(fields_dict)
            scalar_index = converter.build_scalar_index_meta(inner_meta["ScalarIndex"])
        inner_meta["ScalarIndex"] = scalar_index
        if "VectorIndex" in inner_meta:
            vector_index = {
                "IndexType": inner_meta["VectorIndex"]["IndexType"],
            }
            vector_index["Dimension"] = collection_meta.vector_dim
            user_distance = inner_meta["VectorIndex"].get("Distance", "ip").lower()
            # Cosine distance is implemented via normalization + IP distance
            if user_distance == "cosine":
                vector_index["Distance"] = "ip"  # Underlying usage of IP distance
                vector_index["NormalizeVector"] = True  # Enable vector normalization
            else:
                vector_index["Distance"] = user_distance
                vector_index["NormalizeVector"] = False
            vector_index["Quant"] = inner_meta["VectorIndex"].get("Quant", "float")
            if "hybrid" in inner_meta["VectorIndex"]["IndexType"]:
                vector_index["EnableSparse"] = True
                vector_index["SearchWithSparseLogitAlpha"] = inner_meta["VectorIndex"].get(
                    "SearchWithSparseLogitAlpha", 0.5
                )
            if "flat" in inner_meta["VectorIndex"]["IndexType"]:
                vector_index["IndexType"] = "flat"
                if "EnableSparse" in inner_meta["VectorIndex"]:
                    vector_index["EnableSparse"] = inner_meta["VectorIndex"]["EnableSparse"]
                if "SearchWithSparseLogitAlpha" in inner_meta["VectorIndex"]:
                    vector_index["SearchWithSparseLogitAlpha"] = inner_meta["VectorIndex"][
                        "SearchWithSparseLogitAlpha"
                    ]

            inner_meta["VectorIndex"] = vector_index
        inner_meta["CollectionName"] = collection_meta.collection_name
        return inner_meta

    @staticmethod
    def _get_user_meta(
        inner_meta: Dict[str, Any], collection_meta: CollectionMeta
    ) -> Dict[str, Any]:
        """Convert internal metadata back to user facing metadata structure.

        Args:
            inner_meta (Dict[str, Any]): Internal metadata structure.

        Returns:
            Dict[str, Any]: User facing metadata structure.
        """
        user_meta = copy.deepcopy(inner_meta)
        user_meta["VectorIndex"].pop("Dimension", None)
        # If vector normalization is enabled, it means the user is using cosine distance
        if user_meta["VectorIndex"].pop("NormalizeVector", False):
            user_meta["VectorIndex"]["Distance"] = "cosine"
        if "ScalarIndex" in user_meta:
            converter = DataProcessor(collection_meta.fields_dict)
            user_meta["ScalarIndex"] = converter.user_scalar_fields_from_engine(
                user_meta["ScalarIndex"]
            )
        return user_meta

    def update(self, additional_user_meta: Dict[str, Any]) -> bool:
        """Update index metadata.

        Args:
            additional_user_meta (Dict[str, Any]): New metadata to merge.

        Returns:
            bool: True if update was successful, False if validation failed.
        """
        if not validation.is_valid_index_meta_data_for_update(
            additional_user_meta, self.collection_meta.fields_dict
        ):
            return False
        user_meta = IndexMeta._get_user_meta(self.inner_meta, self.collection_meta)

        # Only update fields that are present in additional_user_meta
        if "ScalarIndex" in additional_user_meta:
            user_meta["ScalarIndex"] = additional_user_meta["ScalarIndex"]
        if "Description" in additional_user_meta:
            user_meta["Description"] = additional_user_meta["Description"]

        new_inner_meta = IndexMeta._build_inner_meta(user_meta, self.collection_meta)
        self.inner_meta = new_inner_meta
        self.__idict.override(new_inner_meta)
        return True

    def get_build_index_dict(self) -> Dict[str, Any]:
        """Get the dictionary for building the index.

        Returns:
            Dict[str, Any]: A copy of the raw metadata.
        """
        new_meta_data = self.__idict.get_raw_copy()
        return new_meta_data

    def get_meta_data(self) -> Dict[str, Any]:
        """Get the user facing metadata.

        Returns:
            Dict[str, Any]: The user facing metadata.
        """
        return IndexMeta._get_user_meta(self.inner_meta, self.collection_meta)

    def has_sparse(self) -> bool:
        """Check if sparse vector is enabled in the index.

        Returns:
            bool: True if sparse vector is enabled, False otherwise.
        """
        return self.inner_meta["VectorIndex"].get("EnableSparse", False)
