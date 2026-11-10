# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from unittest.mock import AsyncMock

import pytest
from openviking_sdk import AsyncHTTPClient


@pytest.mark.asyncio
async def test_sdk_conflict_precedes_local_upload(tmp_path):
    source = tmp_path / "docs"
    source.mkdir()
    client = AsyncHTTPClient(url="http://localhost:1933")
    client._upload_temp_file = AsyncMock()
    with pytest.raises(ValueError, match="cannot be combined"):
        await client.add_resource(
            str(source), options={"strict": True, "import_policy": "best_effort"}
        )
    client._upload_temp_file.assert_not_awaited()
    await client.close()
