from asyncio import Event, wait_for
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import Role
from openviking.service.rule_cleanup import RuleCleanupScheduler
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils.config.rules_config import RulesConfig
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.mark.asyncio
async def test_rule_cleanup_scheduler_scans_only_existing_rule_libraries(tmp_path):
    agfs = MockLocalAGFS(root_path=tmp_path)
    for path in (
        "/local/acme/user/alice/memories/rules",
        "/local/acme/user/bob/memories",
        "/local/_system/user/ignored/memories/rules",
    ):
        agfs.mkdir(path)
    agfs.write("/local/ignored-file", b"not an account")
    cleanup = AsyncMock(return_value={"scanned": 0, "deleted": [], "failed": []})

    scan_finished = Event()

    async def sleep(_delay):
        scan_finished.set()
        await Event().wait()

    scheduler = RuleCleanupScheduler(
        SimpleNamespace(cleanup_archived_rules=cleanup),
        VikingFS(agfs=agfs),
        RulesConfig(),
        sleep=sleep,
    )
    await scheduler.start()
    try:
        await wait_for(scan_finished.wait(), 2)
    finally:
        await wait_for(scheduler.stop(), 2)

    cleanup.assert_awaited_once()
    call = cleanup.await_args
    assert call is not None
    ctx = call.kwargs["ctx"]
    assert (ctx.account_id, ctx.user.user_id, ctx.role) == ("acme", "alice", Role.USER)
