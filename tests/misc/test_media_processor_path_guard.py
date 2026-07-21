# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import PermissionDeniedError


@pytest.mark.asyncio
async def test_process_rejects_local_path_before_raw_content_fallback(monkeypatch, tmp_path):
    parse = AsyncMock()
    monkeypatch.setattr("openviking.utils.media_processor.parse", parse)
    local_file = tmp_path / "secret.txt"
    local_file.write_text("secret")
    monkeypatch.chdir(tmp_path)

    for source in (str(local_file), local_file.name):
        with pytest.raises(PermissionDeniedError, match="direct host filesystem paths"):
            await UnifiedResourceProcessor().process(
                source,
                allow_local_path_resolution=False,
            )

    parse.assert_not_awaited()
