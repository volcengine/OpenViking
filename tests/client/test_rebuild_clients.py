import asyncio
import json

import httpx

from openviking_cli.client.sync_http import SyncHTTPClient
from openviking_cli.utils.async_utils import run_async


def test_sync_http_client_reindex_forwards_non_recursive_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        result = {"status": "completed"} if body["wait"] else {"status": "accepted"}
        return httpx.Response(200, json={"status": "ok", "result": result})

    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        assert client.reindex(
            "viking://resources/demo", mode="vectors_only", wait=False, force=True
        ) == {"status": "accepted"}
        assert client.reindex(
            "viking://resources/demo", mode="semantic_and_vectors", recursive=False
        ) == {"status": "completed"}
    finally:
        client.close()

    assert [(r.method, r.url.path, json.loads(r.content)) for r in requests] == [
        (
            "POST",
            "/api/v1/content/reindex",
            {
                "uri": "viking://resources/demo",
                "mode": "vectors_only",
                "wait": False,
                "force": True,
                "recursive": True,
            },
        ),
        (
            "POST",
            "/api/v1/content/reindex",
            {
                "uri": "viking://resources/demo",
                "mode": "semantic_and_vectors",
                "wait": True,
                "recursive": False,
            },
        ),
    ]


def test_run_async_from_foreign_event_loop_uses_shared_background_loop():
    async def active_loop():
        return asyncio.get_running_loop()

    worker = run_async(active_loop())

    async def call_from_running_loop():
        assert asyncio.get_running_loop() is not worker
        assert run_async(active_loop()) is worker

    asyncio.run(call_from_running_loop())
