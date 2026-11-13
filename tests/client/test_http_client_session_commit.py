from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking_cli.client.sync_http import SyncHTTPClient


@pytest.mark.parametrize("explicit_budgets", [False, True])
def test_sync_commit_forwards_optional_compression_budgets(explicit_budgets):
    commit = AsyncMock(return_value={"task_id": "task-1"})
    client = SyncHTTPClient.__new__(SyncHTTPClient)
    client._async_client = SimpleNamespace(commit_session=commit)
    budgets = (
        {
            "session_window_context": 8192,
            "summary_token_ratio": 0.25,
            "retained_token_ratio": 0.05,
            "reasoning_effort": "low",
        }
        if explicit_budgets
        else {}
    )

    result = client.commit_session("session-1", **budgets)

    assert result == {"task_id": "task-1"}
    commit.assert_awaited_once_with(
        "session-1",
        telemetry=False,
        keep_recent_count=0,
        enable_working_memory=None,
        retention_mode=None,
        keep_recent_turn_count=None,
        retained_message_token_budget=None,
        min_raw_tail_steps=None,
        session_window_context=budgets.get("session_window_context"),
        summary_token_ratio=budgets.get("summary_token_ratio"),
        retained_token_ratio=budgets.get("retained_token_ratio"),
        reasoning_effort=budgets.get("reasoning_effort"),
        event_tags=None,
    )
