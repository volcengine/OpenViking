# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persisted token digests must survive CodeQL false-positive remediation."""

import base64

import pytest

from openviking.server.account_stores.postgres import PostgresAccountStore
from openviking.server.account_stores.sql import _credential_digest

# A structured API-key fixture and its digest as stored by the original SHA-256
# implementation. This is a fixed compatibility vector, not a production key.
API_KEY = (
    "YWNtZQ.YWxpY2U."
    "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWYwMTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZg"
)
LOOKUP_DIGEST = "33b927c05cc7fc8097fedc0c3240c9cc714e2c82c1f3223f058daf8910bb0c94"
LEGACY_MATERIAL = "sha256:v1:" + LOOKUP_DIGEST


def test_existing_digest_and_hash_material_remain_compatible():
    store = PostgresAccountStore(
        service_name="digest-test", params={"user": "test", "database": "test"}
    )
    assert _credential_digest(API_KEY) == LOOKUP_DIGEST
    assert store._encode_credential("acme", "alice", API_KEY) == LEGACY_MATERIAL
    assert store._matches_credential("acme", "alice", LEGACY_MATERIAL, API_KEY)
    assert not store._matches_credential("acme", "alice", LEGACY_MATERIAL, API_KEY + "wrong")


@pytest.mark.parametrize("credential_storage", ["hash", "encrypted"])
def test_both_storage_modes_use_the_existing_lookup_digest(credential_storage):
    store = PostgresAccountStore(
        service_name="digest-test",
        params={
            "user": "test",
            "database": "test",
            "credential_storage": credential_storage,
            "credential_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        },
    )
    credential = store._new_credential("acme", "alice", 1, API_KEY, sequence=1)
    assert credential.lookup_digest == LOOKUP_DIGEST
    assert store._matches_credential("acme", "alice", credential.material, API_KEY)
    assert not store._matches_credential("acme", "alice", credential.material, API_KEY + "wrong")
    if credential_storage == "hash":
        assert credential.material == LEGACY_MATERIAL
    else:
        assert store._decrypt_credential("acme", "alice", credential.material) == API_KEY
