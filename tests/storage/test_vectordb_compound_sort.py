# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import Mock

import pytest

from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter


def test_compound_scalar_sort_is_rejected_instead_of_window_sorted():
    collection = Mock()
    adapter = LocalCollectionAdapter("contexts", "", "default")
    adapter._collection = collection

    with pytest.raises(ValueError, match="compound order_by is not supported"):
        adapter.query(
            filter={"op": "must", "field": "context_type", "conds": ["memory"]},
            limit=2,
            offset=1,
            output_fields=["updated_at"],
            order_by=["updated_at", "id"],
            order_desc=True,
        )

    collection.search_by_scalar.assert_not_called()
