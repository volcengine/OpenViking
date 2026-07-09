# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Skill target resolution tests."""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext
from openviking.server.routers.skills import _require_skill
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError


class _FakeFS:
    def __init__(self, existing: set[str]):
        self.existing = existing

    async def stat(self, uri: str, ctx=None):
        if uri in self.existing:
            return {"uri": uri, "isDir": True}
        raise NotFoundError(uri, "skill")


def _service_with_skills(*uris: str):
    return SimpleNamespace(fs=_FakeFS(set(uris)))


@pytest.mark.asyncio
async def test_require_skill_can_fallback_from_missing_target_uri_to_user_skill():
    service = _service_with_skills("viking://user/default/skills/demo")
    ctx = RequestContext(user_id="default", account_id="default")

    root_uri = await _require_skill(service, ctx, "demo", "viking://agent/skills")

    assert root_uri == "viking://user/default/skills/demo"


@pytest.mark.asyncio
async def test_require_skill_rejects_explicit_exact_target_when_fallback_disabled():
    service = _service_with_skills("viking://user/default/skills/demo")
    ctx = RequestContext(user_id="default", account_id="default")

    with pytest.raises(NotFoundError):
        await _require_skill(
            service,
            ctx,
            "demo",
            "viking://agent/skills",
            allow_fallback=False,
        )


@pytest.mark.asyncio
async def test_require_skill_rejects_empty_target_uri_without_fallback():
    service = _service_with_skills("viking://user/default/skills/demo")
    ctx = RequestContext(user_id="default", account_id="default")

    with pytest.raises(InvalidArgumentError) as exc_info:
        await _require_skill(service, ctx, "demo", "")

    assert "target URI cannot be empty" in str(exc_info.value)
