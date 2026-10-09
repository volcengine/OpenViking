# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.storage.errors import LockAcquisitionError


async def test_polled_timeout_reports_the_full_wait_and_target():
    client = object.__new__(AsyncAGFSClient)
    client._acquire_pathlock_once = AsyncMock(
        side_effect=LockAcquisitionError("lock acquire timed out after 0ms")
    )

    with pytest.raises(LockAcquisitionError, match=r"after 100ms \(polled\): /local/s") as info:
        await client._acquire_pathlock("pathlock_acquire_exact", {}, "/local/s", 0.1, None)

    assert "after 0ms" in str(info.value.__cause__)
    assert client._acquire_pathlock_once.await_count > 1
