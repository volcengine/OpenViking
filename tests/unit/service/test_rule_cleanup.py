from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from openviking.service.rule_cleanup import RuleCleanupScheduler
from openviking_cli.utils.config.rules_config import RulesConfig


class _AGFS:
    async def ls(self, path):
        if path == "/local":
            return [
                {"name": "_system", "isDir": True},
                {"name": "acme", "isDir": True},
                {"name": "ignored-file", "isDir": False},
            ]
        if path == "/local/acme/user":
            return [
                {"name": "alice", "isDir": True},
                {"name": "bob", "isDir": True},
            ]
        raise AssertionError(path)

    async def stat(self, path):
        return {"isDir": path == "/local/acme/user/alice/memories/rules"}


@pytest.mark.asyncio
async def test_rule_cleanup_scheduler_scans_only_existing_rule_libraries():
    cleanup = AsyncMock(
        return_value={
            "scanned": 2,
            "deleted": ["viking://user/alice/memories/rules/file-operations/old.md"],
            "failed": [],
        }
    )
    scheduler = RuleCleanupScheduler(
        SimpleNamespace(cleanup_archived_rules=cleanup),
        SimpleNamespace(agfs=object()),
        RulesConfig(),
    )
    scheduler._agfs_client = cast(Any, _AGFS())

    await scheduler._scan_once()

    cleanup.assert_awaited_once()
    call = cleanup.await_args
    assert call is not None
    ctx = call.kwargs["ctx"]
    assert ctx.account_id == "acme"
    assert ctx.user.user_id == "alice"
