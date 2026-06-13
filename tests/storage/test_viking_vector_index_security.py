# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.mark.asyncio
async def test_bound_account_backend_rejects_cross_account_upsert(tmp_path):
    config = VectorDBBackendConfig(
        backend="local",
        name="security_ctx",
        path=str(tmp_path),
        dimension=4,
    )
    store = VikingVectorIndexBackend(config=config)
    ctx = RequestContext(
        user=UserIdentifier("acct_alpha", "user", "agent"),
        role=Role.USER,
    )

    try:
        rejected = await store.upsert(
            {
                "id": "shared-record-id",
                "account_id": "acct_beta",
                "uri": "viking://resources/b.md",
                "context_type": "resource",
                "owner_space": "",
                "vector": [0.0, 1.0, 0.0, 0.0],
                "sparse_vector": {},
            },
            ctx=ctx,
        )

        assert rejected == ""
        assert await store.get(["shared-record-id"], ctx=ctx) == []
    finally:
        await store.close()
