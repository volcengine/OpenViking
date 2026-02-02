# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import copy
from typing import Any, Dict, Optional

from openviking.storage.vectordb.meta.dict import IDict
from openviking.storage.vectordb.meta.local_dict import PersistentDict, VolatileDict
from openviking.storage.vectordb.utils import validation
from openviking.storage.vectordb.utils.dict_utils import recursive_update_dict


def create_collection_meta(
    path: str, user_meta: Optional[Dict[str, Any]] = None
) -> "CollectionMeta":
    """Create a CollectionMeta instance.

    Args:
        path (str): The path to store metadata. If empty, creates a volatile dictionary.
        user_meta (Optional[Dict[str, Any]]): The initial metadata provided by the user.

    Returns:
        CollectionMeta: The created CollectionMeta instance.

    Raises:
        ValueError: If the user_meta is invalid.
    """
    inner_meta = {}
    if user_meta:
        if not validation.is_valid_collection_meta_data(user_meta):
            raise ValueError(f"invalid collection_meta {user_meta}")
        else:
            inner_meta = CollectionMeta._build_inner_meta(user_meta)
    idict = PersistentDict(path, inner_meta) if path else VolatileDict(inner_meta)
    return CollectionMeta(idict)


class CollectionMeta:
    """Manages collection metadata.

    Wraps an IDict instance to provide structured access and modification of collection metadata.
    """

    def __init__(self, idict: IDict):
        """Initialize CollectionMeta.

        Args:
            idict (IDict): The underlying dictionary storage interface.
        """
        assert isinstance(idict, IDict), "meta must be a IDict"
        self.__idict = idict
        self.inner_meta = self.__idict.get_raw()

    @staticmethod
    def _build_inner_meta(user_meta: Dict[str, Any]) -> Dict[str, Any]:
        """Build the internal metadata structure from user provided metadata.

        Args:
            user_meta (Dict[str, Any]): User provided metadata.

        Returns:
            Dict[str, Any]: The internal metadata structure.
        """
        inner_meta = copy.deepcopy(user_meta)
        fields = inner_meta.get("Fields", [])
        has_pk = next(
            (True for item in fields if item.get("IsPrimaryKey", False)),
            False,
        )
        inner_meta["HasPrimaryKey"] = has_pk
        if not has_pk:
            fields.append(
                {
                    "FieldName": "AUTO_ID",
                    "FieldType": "int64",
                    "IsPrimaryKey": True,
                }
            )
            inner_meta["Fields"] = fields
        field_count = 0
        for item in fields:
            if "FieldID" not in item:
                item["FieldID"] = field_count
                field_count += 1
        inner_meta["FieldsCount"] = field_count
        inner_meta["Fields"] = fields
        fields_dict = {item["FieldName"]: item for item in fields}
        inner_meta["FieldsDict"] = fields_dict
        inner_meta["Dimension"] = next(
            (
                item["Dim"]
                for item in inner_meta.get("Fields", {})
                if item.get("FieldType") == "vector"
            ),
            0,
        )
        inner_meta["PrimaryKey"] = next(
            (item["FieldName"] for item in fields if item.get("IsPrimaryKey", False)),
            "",
        )
        inner_meta["VectorKey"] = next(
            (item["FieldName"] for item in fields if item.get("FieldType") == "vector"),
            "",
        )
        inner_meta["SparseVectorKey"] = next(
            (item["FieldName"] for item in fields if item.get("FieldType") == "sparse_vector"),
            "",
        )
        return inner_meta

    @staticmethod
    def _get_user_meta(inner_meta: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal metadata back to user facing metadata structure.

        Args:
            inner_meta (Dict[str, Any]): Internal metadata structure.

        Returns:
            Dict[str, Any]: User facing metadata structure.
        """
        user_meta = copy.deepcopy(inner_meta)
        if not user_meta.get("HasPrimaryKey", False):
            fields = user_meta.get("Fields", [])
            new_list = [item for item in fields if "AUTO_ID" != item.get("FieldName", "")]
            user_meta["Fields"] = new_list
        user_meta.pop("HasPrimaryKey", None)
        user_meta.pop("FieldsCount", None)
        user_meta.pop("FieldsDict", None)
        user_meta.pop("Dimension", None)
        user_meta.pop("PrimaryKey", None)
        user_meta.pop("VectorKey", None)
        user_meta.pop("SparseVectorKey", None)
        for item in user_meta.get("Fields", []):
            item.pop("FieldID", None)
        return user_meta

    def update(self, additional_user_meta: Dict[str, Any]) -> bool:
        """Update collection metadata.

        Args:
            additional_user_meta (Dict[str, Any]): New metadata to merge.

        Returns:
            bool: True if update was successful, False if validation failed.
        """
        if not validation.is_valid_collection_meta_data_for_update(
            additional_user_meta, self.fields_dict
        ):
            return False
        user_meta = CollectionMeta._get_user_meta(self.inner_meta)
        user_meta = recursive_update_dict(user_meta, additional_user_meta)
        new_inner_meta = CollectionMeta._build_inner_meta(user_meta)
        self.inner_meta = new_inner_meta
        self.__idict.override(new_inner_meta)
        return True

    def get_raw_copy(self) -> Dict[str, Any]:
        """Get a deep copy of the raw metadata.

        Returns:
            Dict[str, Any]: A deep copy of the raw metadata.
        """
        return self.__idict.get_raw_copy()

    def get_meta_data(self) -> Dict[str, Any]:
        """Get the user facing metadata.

        Returns:
            Dict[str, Any]: The user facing metadata.
        """
        return CollectionMeta._get_user_meta(self.inner_meta)

    def drop(self):
        """Drop the collection metadata storage."""
        self.__idict.drop()

    @property
    def collection_name(self) -> str:
        """Get the collection name."""
        return self.inner_meta.get("CollectionName", "")

    @property
    def primary_key(self) -> str:
        """Get the primary key field name."""
        return self.inner_meta.get("PrimaryKey", "")

    @property
    def fields_dict(self) -> Dict[str, Any]:
        """Get the dictionary of fields definitions."""
        return self.inner_meta.get("FieldsDict", {})

    @property
    def vectorize(self) -> Dict[str, Any]:
        """Get the vectorization configuration."""
        return self.inner_meta.get("Vectorize", {})

    @property
    def vector_key(self) -> str:
        """Get the vector field name."""
        return self.inner_meta.get("VectorKey", "")

    @property
    def sparse_vector_key(self) -> str:
        """Get the sparse vector field name."""
        return self.inner_meta.get("SparseVectorKey", "")

    @property
    def has_sparse(self) -> bool:
        """Check if sparse vector is enabled."""
        return "Sparse" in self.inner_meta.get("Vectorize", {})

    @property
    def vector_dim(self) -> int:
        """Get or set the vector dimension."""
        return self.inner_meta.get("Dimension", 0)

    @vector_dim.setter
    def vector_dim(self, vector_dim: int):
        self.inner_meta["Dimension"] = vector_dim
