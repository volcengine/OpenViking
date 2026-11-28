# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources as resources_module
from openviking.server.routers.resources import AddSkillRequest
from openviking.server.temp_upload_store import ResolvedTempUpload
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


async def test_add_skill_validates_telemetry_before_materializing_the_upload(tmp_path, monkeypatch):
    materialized = tmp_path / "ov_shared_upload_skill.zip"
    requests = []

    class _Store:
        async def resolve_for_consume(self, temp_file_id, _ctx):
            requests.append(temp_file_id)
            materialized.write_bytes(b"skill archive")
            return ResolvedTempUpload(
                mode="shared",
                temp_file_id=temp_file_id,
                original_filename="skill.zip",
                local_path=str(materialized),
            )

    monkeypatch.setattr(resources_module.TempUploadStore, "build", lambda _config: _Store())
    install_skills = AsyncMock()
    monkeypatch.setattr(resources_module, "install_skills", install_skills)

    http_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=object())))
    request = AddSkillRequest(temp_file_id="shared_skill", telemetry={"unsupported": True})
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role(Role.USER))

    with pytest.raises(InvalidArgumentError):
        await resources_module.add_skill(http_request, request, ctx)

    install_skills.assert_not_awaited()
    assert requests == []
    assert not materialized.exists()
