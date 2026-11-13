# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.service.reindex_executor import ReindexExecutor
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.asyncio
async def test_reindex_rejects_file_only_namespace_before_task_creation():
    executor = ReindexExecutor()

    with pytest.raises(InvalidArgumentError, match="reindex is not supported"):
        await executor.execute(
            uri="viking://system/memory/memory_templates/custom/demo",
            mode="full",
            wait=False,
            ctx=None,
        )
