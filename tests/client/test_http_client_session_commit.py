import json
from typing import Any

import httpx
import pytest

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.client.sync_http import SyncHTTPClient


@pytest.mark.parametrize("sync", [False, True], ids=["async-positional", "sync-compression"])
async def test_legacy_commit_preserves_positional_telemetry_and_optional_budgets(
    sync, monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    requests = []
    result = {"task_id": "task-1"}

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok", "result": result})

    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if isinstance(client, SyncHTTPClient) else client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    expected: dict[str, object] = {"telemetry": True, "keep_recent_count": 0}
    if isinstance(client, SyncHTTPClient):
        budgets: dict[str, Any] = {
            "enable_working_memory": True,
            "retention_mode": "turn_budget",
            "keep_recent_turn_count": 3,
            "retained_message_token_budget": 12_000,
            "min_raw_tail_steps": 1,
            "session_window_context": 8192,
            "summary_token_ratio": 0.25,
            "retained_token_ratio": 0.05,
            "reasoning_effort": "low",
        }
        expected.update(budgets)
        try:
            assert client.commit_session("session-1", True, **budgets) == result
        finally:
            client.close()
    else:
        try:
            assert await client.commit_session("session-1", True) == result
        finally:
            await client.close()

    (request,) = requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/sessions/session-1/commit"
    assert json.loads(request.content) == expected
