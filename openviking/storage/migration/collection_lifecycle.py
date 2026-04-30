# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Collection lifecycle management for embedding migration.

Provides utilities to create, name, and drop vector collections
during embedding model migration, reusing source schema but with
a different vector dimension.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from openviking.storage.vectordb_adapters.base import CollectionAdapter


class CollectionLifecycle:
    """Manages target collection lifecycle during embedding migration.

    All methods are static — the class acts as a namespace for
    collection lifecycle operations.
    """

    @staticmethod
    def generate_target_name(source_name: str, target_embedder_name: str) -> str:
        """Generate a target collection name from source and embedder names.

        The naming convention is ``{source_name}_{target_embedder_name}``.
        The embedder name is sanitised to be safe for collection naming
        (non-alphanumeric characters other than ``_`` and ``-`` are replaced
        with ``_``).

        Args:
            source_name: Name of the source collection.
            target_embedder_name: Name/identifier of the target embedder.

        Returns:
            Sanitised target collection name.
        """
        sanitized = "".join(
            c if c.isalnum() or c in ("_", "-") else "_" for c in target_embedder_name
        )
        return f"{source_name}_{sanitized}"

    @staticmethod
    def create_target_collection(
        source_adapter: CollectionAdapter,
        target_adapter: CollectionAdapter,
        target_dimension: int,
    ) -> bool:
        """Create a target collection reusing the source schema.

        The source collection's schema is read via ``get_collection_info()``.
        The vector field's dimension is replaced with ``target_dimension``
        while all other fields are kept as-is.

        Args:
            source_adapter: Adapter for the existing source collection.
            target_adapter: Adapter for the (to-be-created) target collection.
            target_dimension: Desired vector dimension for the target.

        Returns:
            ``True`` if the collection was created successfully.

        Raises:
            ValueError: If the target collection already exists.
        """
        if target_adapter.collection_exists():
            raise ValueError(
                f"Target collection '{target_adapter.collection_name}' already exists"
            )

        info = source_adapter.get_collection_info()
        schema: Dict[str, Any] = copy.deepcopy(info) if info else {}

        # Update the vector field dimension to the target dimension.
        fields = schema.get("Fields", [])
        for field in fields:
            if field.get("FieldName") == "vector" and field.get("FieldType") == "vector":
                field["Dim"] = target_dimension

        return target_adapter.create_collection(
            name=target_adapter.collection_name,
            schema=schema,
            distance="COSINE",
            sparse_weight=0.0,
            index_name=target_adapter.index_name,
        )

    @staticmethod
    def drop_target_collection(
        target_adapter: CollectionAdapter,
        dual_write_active: bool = False,
    ) -> bool:
        """Drop the target collection.

        Args:
            target_adapter: Adapter for the target collection to drop.
            dual_write_active: When ``True``, the drop is rejected because
                dual-write mode must be disabled first.

        Returns:
            ``True`` if the collection was dropped successfully.

        Raises:
            RuntimeError: If ``dual_write_active`` is ``True``.
        """
        if dual_write_active:
            raise RuntimeError(
                "Cannot drop target collection while dual-write is active"
            )
        return target_adapter.drop_collection()
