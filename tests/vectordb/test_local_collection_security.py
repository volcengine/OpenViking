# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from openviking.storage.vectordb.collection.local_collection import LocalCollection
from openviking.storage.vectordb.store.data import CandidateData
from openviking.storage.vectordb.utils.str_to_uint64 import str_to_uint64


class _FakeStoreManager:
    def __init__(self, rows):
        self.rows = rows

    def fetch_cands_data(self, label_list):
        return [self.rows.get(label) for label in label_list]


def test_security_context_rebind_is_rejected_without_native_store():
    label = str_to_uint64("shared-record-id")
    existing = CandidateData(
        label=label,
        fields=json.dumps(
            {
                "id": "shared-record-id",
                "account_id": "acct_alpha",
                "uri": "viking://resources/a.md",
                "context_type": "resource",
                "owner_space": "",
            }
        ),
    )
    collection = object.__new__(LocalCollection)
    collection.store_mgr = _FakeStoreManager({label: existing})

    with pytest.raises(ValueError, match="different security context"):
        collection._guard_security_context_rebind(
            [label],
            [
                {
                    "id": "shared-record-id",
                    "account_id": "acct_beta",
                    "uri": "viking://resources/b.md",
                    "context_type": "resource",
                    "owner_space": "",
                }
            ],
        )


def test_security_context_stable_update_is_allowed_without_native_store():
    label = str_to_uint64("shared-record-id")
    existing = CandidateData(
        label=label,
        fields=json.dumps(
            {
                "id": "shared-record-id",
                "account_id": "acct_alpha",
                "uri": "viking://resources/a.md",
                "context_type": "resource",
                "owner_space": "",
                "description": "old",
            }
        ),
    )
    collection = object.__new__(LocalCollection)
    collection.store_mgr = _FakeStoreManager({label: existing})

    collection._guard_security_context_rebind(
        [label],
        [
            {
                "id": "shared-record-id",
                "account_id": "acct_alpha",
                "uri": "viking://resources/a.md",
                "context_type": "resource",
                "owner_space": "",
                "description": "new",
            }
        ],
    )
