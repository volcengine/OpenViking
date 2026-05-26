from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import openviking_cli.client.http as http_module
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


def test_sync_openviking_reindex_forwards_to_async_client():
    client = SyncOpenViking()
    with patch.object(
        client._async_client,
        "reindex",
        return_value={"status": "completed"},
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
    assert mock_run.called
    assert mock_reindex.called


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


def test_sync_http_client_reindex_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    with patch.object(
        client._async_client,
        "reindex",
        return_value={"status": "accepted"},
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
    assert mock_run.called
    assert mock_reindex.called
