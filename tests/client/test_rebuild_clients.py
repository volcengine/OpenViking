import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import openviking_cli.client.http as http_module
import openviking_cli.utils.async_utils as async_utils
from openviking import AsyncOpenViking, SyncOpenViking
from openviking.client.local import LocalClient
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.client.sync_http import SyncHTTPClient
from openviking_cli.utils.config import OPENVIKING_CLI_CONFIG_ENV


@pytest.fixture(autouse=True)
def clear_ovcli_config(monkeypatch):
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)
    monkeypatch.setattr(http_module, "load_ovcli_config", lambda: None)


async def test_async_openviking_reindex_forwards_to_local_client(tmp_path):
    client = AsyncOpenViking(path=str(tmp_path))
    with patch.object(client, "_ensure_initialized", new_callable=AsyncMock) as mock_init:
        with patch.object(client._client, "reindex", new_callable=AsyncMock) as mock_reindex:
            mock_reindex.return_value = {"status": "completed"}

            result = await client.reindex(
                "viking://resources/demo",
                mode="vectors_only",
                wait=False,
            )

    assert result == {"status": "completed"}
    mock_init.assert_awaited_once()
    mock_reindex.assert_awaited_once_with(
        uri="viking://resources/demo",
        mode="vectors_only",
        wait=False,
    )


async def test_async_openviking_memory_graph_health_forwards_to_local_client(tmp_path):
    client = AsyncOpenViking(path=str(tmp_path))
    with patch.object(client, "_ensure_initialized", new_callable=AsyncMock) as mock_init:
        with patch.object(
            client._client,
            "memory_graph_health",
            new_callable=AsyncMock,
        ) as mock_graph_health:
            mock_graph_health.return_value = {"healthy": True}

            result = await client.memory_graph_health(
                "viking://agent/default/memories",
                node_limit=123,
                sample_limit=4,
            )

    assert result == {"healthy": True}
    mock_init.assert_awaited_once()
    mock_graph_health.assert_awaited_once_with(
        "viking://agent/default/memories",
        node_limit=123,
        sample_limit=4,
    )


def test_sync_openviking_reindex_forwards_to_async_client():
    client = SyncOpenViking()
    reindex_coro = object()
    with patch.object(
        client._async_client,
        "reindex",
        new=Mock(return_value=reindex_coro),
    ) as mock_reindex:
        with patch(
            "openviking.sync_client.run_async", return_value={"status": "completed"}
        ) as mock_run:
            result = client.reindex(
                "viking://resources/demo",
                mode="semantic_and_vectors",
                wait=True,
            )

    assert result == {"status": "completed"}
    mock_run.assert_called_once_with(reindex_coro)
    assert mock_reindex.called


def test_sync_openviking_memory_graph_health_forwards_to_async_client():
    client = SyncOpenViking()
    graph_health_coro = object()
    with patch.object(
        client._async_client,
        "memory_graph_health",
        new=Mock(return_value=graph_health_coro),
    ) as mock_graph_health:
        with patch(
            "openviking.sync_client.run_async",
            return_value={"healthy": True},
        ) as mock_run:
            result = client.memory_graph_health(
                "viking://agent/default/memories",
                node_limit=321,
                sample_limit=5,
            )

    assert result == {"healthy": True}
    mock_run.assert_called_once_with(graph_health_coro)
    mock_graph_health.assert_called_once_with(
        "viking://agent/default/memories",
        node_limit=321,
        sample_limit=5,
    )


async def test_local_client_reindex_forwards_to_service():
    client = LocalClient.__new__(LocalClient)
    client._service = SimpleNamespace(reindex=AsyncMock(return_value={"status": "completed"}))

    result = await LocalClient.reindex(
        client,
        uri="viking://resources/demo",
        mode="vectors_only",
        wait=False,
    )

    assert result == {"status": "completed"}
    client._service.reindex.assert_awaited_once()


async def test_local_client_memory_graph_health_uses_service_viking_fs():
    client = LocalClient.__new__(LocalClient)
    client._service = SimpleNamespace(viking_fs=object())
    client._ctx = object()

    with patch(
        "openviking.client.local.inspect_memory_graph_health",
        new_callable=AsyncMock,
    ) as mock_graph_health:
        mock_graph_health.return_value = {"healthy": True}

        result = await LocalClient.memory_graph_health(
            client,
            uri="viking://agent/default/memories",
            node_limit=456,
            sample_limit=6,
        )

    assert result == {"healthy": True}
    mock_graph_health.assert_awaited_once_with(
        client._service.viking_fs,
        "viking://agent/default/memories",
        ctx=client._ctx,
        node_limit=456,
        sample_limit=6,
    )


async def test_async_http_client_reindex_posts_content_reindex():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    with patch.object(
        client, "_handle_response", return_value={"status": "completed"}
    ) as mock_handle:
        result = await client.reindex(
            "viking://resources/demo",
            mode="vectors_only",
            wait=False,
        )

    assert result == {"status": "completed"}
    fake_http.post.assert_awaited_once_with(
        "/api/v1/content/reindex",
        json={
            "uri": "viking://resources/demo",
            "mode": "vectors_only",
            "wait": False,
        },
    )
    assert mock_handle.called


async def test_async_http_client_memory_graph_health_gets_stats_endpoint():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(get=AsyncMock(return_value=object()))
    client._http = fake_http
    with patch.object(client, "_handle_response", return_value={"healthy": True}) as mock_handle:
        result = await client.memory_graph_health(
            "viking://agent/default/memories",
            node_limit=789,
            sample_limit=7,
        )

    assert result == {"healthy": True}
    fake_http.get.assert_awaited_once_with(
        "/api/v1/stats/memory-graph",
        params={
            "uri": "viking://agent/default/memories",
            "node_limit": 789,
            "sample_limit": 7,
        },
    )
    assert mock_handle.called


def test_sync_http_client_reindex_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    reindex_coro = object()
    with patch.object(
        client._async_client,
        "reindex",
        new=Mock(return_value=reindex_coro),
    ) as mock_reindex:
        with patch(
            "openviking_cli.client.sync_http.run_async",
            return_value={"status": "accepted"},
        ) as mock_run:
            result = client.reindex(
                "viking://resources/demo",
                mode="vectors_only",
                wait=False,
            )

    assert result == {"status": "accepted"}
    mock_run.assert_called_once_with(reindex_coro)
    assert mock_reindex.called


def test_sync_http_client_memory_graph_health_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    graph_health_coro = object()
    with patch.object(
        client._async_client,
        "memory_graph_health",
        new=Mock(return_value=graph_health_coro),
    ) as mock_graph_health:
        with patch(
            "openviking_cli.client.sync_http.run_async",
            return_value={"healthy": True},
        ) as mock_run:
            result = client.memory_graph_health(
                "viking://agent/default/memories",
                node_limit=987,
                sample_limit=8,
            )

    assert result == {"healthy": True}
    mock_run.assert_called_once_with(graph_health_coro)
    mock_graph_health.assert_called_once_with(
        "viking://agent/default/memories",
        node_limit=987,
        sample_limit=8,
    )


def test_run_async_from_foreign_event_loop_uses_shared_background_loop():
    async_utils._shutdown_loop()
    seen_threads: list[int] = []

    async def _capture_thread_id():
        seen_threads.append(threading.get_ident())
        return "ok"

    async def _outer():
        return async_utils.run_async(_capture_thread_id())

    try:
        assert asyncio.run(_outer()) == "ok"
        assert async_utils._loop_thread is not None
        assert seen_threads == [async_utils._loop_thread.ident]
    finally:
        async_utils._shutdown_loop()
