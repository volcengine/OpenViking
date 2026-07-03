import asyncio
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import openviking_cli.client.http as http_module
import openviking_cli.utils.async_utils as async_utils
from openviking import AsyncOpenViking, SyncOpenViking
from openviking.client.local import LocalClient
from openviking.message import ImagePart, TextPart
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.client.sync_http import SyncHTTPClient
from openviking_cli.utils.config import OPENVIKING_CLI_CONFIG_ENV


@pytest.fixture(autouse=True)
def clear_ovcli_config(monkeypatch):
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)
    monkeypatch.setattr(http_module, "load_ovcli_config", lambda: None, raising=False)


def test_async_http_client_zip_directory_skips_symlinked_entries(tmp_path):
    root = tmp_path / "upload"
    root.mkdir()
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "nested.txt").write_text("nested", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    try:
        (root / "inside-link.txt").symlink_to(root / "keep.txt")
        (root / "outside-link.txt").symlink_to(outside)
        (root / "dir-link").symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")

    client = AsyncHTTPClient(url="http://localhost:1933")
    zip_path = Path(client._zip_directory(str(root)))
    try:
        with zipfile.ZipFile(zip_path) as zipf:
            names = sorted(zipf.namelist())
    finally:
        zip_path.unlink(missing_ok=True)

    assert names == ["keep.txt", "nested/nested.txt"]


def test_async_http_client_zip_directory_warns_when_archive_is_empty(tmp_path):
    root = tmp_path / "upload"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    try:
        (root / "outside-link.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")

    client = AsyncHTTPClient(url="http://localhost:1933")
    with patch.object(http_module.logger, "warning") as mock_warning:
        zip_path = Path(client._zip_directory(str(root)))
    try:
        with zipfile.ZipFile(zip_path) as zipf:
            names = sorted(zipf.namelist())
    finally:
        zip_path.unlink(missing_ok=True)

    assert names == []
    mock_warning.assert_called_once_with(
        "Created empty directory upload archive for %s",
        root,
    )


async def test_async_openviking_reindex_forwards_to_local_client(tmp_path):
    client = AsyncOpenViking(path=str(tmp_path))
    with patch.object(client, "_ensure_initialized", new_callable=AsyncMock) as mock_init:
        with patch.object(client._client, "reindex", new_callable=AsyncMock) as mock_reindex:
            mock_reindex.return_value = {"status": "completed"}

            result = await client.reindex(
                "viking://resources/demo",
                mode="prune_orphans",
                wait=False,
                dry_run=True,
            )

    assert result == {"status": "completed"}
    mock_init.assert_awaited_once()
    mock_reindex.assert_awaited_once_with(
        uri="viking://resources/demo",
        mode="prune_orphans",
        wait=False,
        dry_run=True,
    )


def test_sync_openviking_reindex_forwards_to_async_client():
    client = SyncOpenViking()
    with patch.object(
        client._async_client,
        "reindex",
        new_callable=Mock,
        return_value={"status": "completed"},
    ) as mock_reindex:
        with patch(
            "openviking.sync_client.run_async", return_value={"status": "completed"}
        ) as mock_run:
            result = client.reindex(
                "viking://resources/demo",
                mode="prune_orphans",
                wait=True,
                dry_run=True,
            )

    assert result == {"status": "completed"}
    assert mock_run.called
    mock_reindex.assert_called_once_with(
        uri="viking://resources/demo",
        mode="prune_orphans",
        wait=True,
        dry_run=True,
    )


async def test_local_client_reindex_forwards_to_service():
    client = LocalClient.__new__(LocalClient)
    client._service = SimpleNamespace(reindex=AsyncMock(return_value={"status": "completed"}))

    result = await LocalClient.reindex(
        client,
        uri="viking://resources/demo",
        mode="prune_orphans",
        wait=False,
        dry_run=True,
    )

    assert result == {"status": "completed"}
    client._service.reindex.assert_awaited_once_with(
        uri="viking://resources/demo",
        mode="prune_orphans",
        wait=False,
        dry_run=True,
    )


async def test_local_client_batch_add_messages_forwards_to_session():
    class FakeSession:
        def __init__(self):
            self.messages = []

        def add_messages(self, specs):
            self.messages.extend(specs)
            return specs

    fake_session = FakeSession()

    class FakeSessions:
        async def get(self, session_id, ctx, auto_create=False):
            assert session_id == "batch-session"
            assert ctx is client._ctx
            assert auto_create is True
            return fake_session

    client = LocalClient.__new__(LocalClient)
    client._service = SimpleNamespace(sessions=FakeSessions())
    client._ctx = SimpleNamespace(user=SimpleNamespace(user_id="user-1"))
    client._legacy_agent_id = None

    result = await LocalClient.batch_add_messages(
        client,
        "batch-session",
        [
            {
                "role": "user",
                "content": "hello",
                "peer_id": "explicit-user",
                "created_at": "2026-05-28T00:00:00+00:00",
            },
            {"role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
        ],
    )

    assert result == {"session_id": "batch-session", "message_count": 2, "added": 2}
    assert fake_session.messages[0]["role"] == "user"
    assert fake_session.messages[0]["peer_id"] == "explicit-user"
    assert fake_session.messages[0]["created_at"] == "2026-05-28T00:00:00+00:00"
    assert fake_session.messages[0]["parts"][0].text == "hello"
    assert fake_session.messages[1]["role"] == "assistant"
    assert fake_session.messages[1]["peer_id"] is None
    assert fake_session.messages[1]["parts"][0].text == "hi"


async def test_local_client_add_message_accepts_image_parts():
    class FakeSession:
        def __init__(self):
            self.messages = []

        def add_message(self, role, parts, peer_id=None, created_at=None):
            self.messages.append(
                {
                    "role": role,
                    "parts": parts,
                    "peer_id": peer_id,
                    "created_at": created_at,
                }
            )

    fake_session = FakeSession()

    class FakeSessions:
        async def get(self, session_id, ctx, auto_create=False):
            assert session_id == "image-session"
            assert ctx is client._ctx
            assert auto_create is True
            return fake_session

    client = LocalClient.__new__(LocalClient)
    client._service = SimpleNamespace(sessions=FakeSessions())
    client._ctx = SimpleNamespace(user=SimpleNamespace(user_id="user-1"))
    client._legacy_agent_id = None

    result = await LocalClient.add_message(
        client,
        "image-session",
        "user",
        parts=[
            {"type": "text", "text": "Look at this"},
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
        ],
    )

    assert result == {"session_id": "image-session", "message_count": 1}
    assert isinstance(fake_session.messages[0]["parts"][0], TextPart)
    assert isinstance(fake_session.messages[0]["parts"][1], ImagePart)
    assert fake_session.messages[0]["parts"][1].url == "https://example.com/image.png"


async def test_async_http_client_batch_add_messages_posts_batch_payload():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"session_id": "batch-session", "message_count": 2, "added": 2}
    }

    messages = [
        {
            "role": "user",
            "content": "hello",
            "peer_id": "explicit-user",
            "created_at": "2026-05-28T00:00:00+00:00",
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
    ]

    result = await client.batch_add_messages("batch-session", messages)

    assert result == {"session_id": "batch-session", "message_count": 2, "added": 2}
    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/batch-session/messages/batch",
        json={"messages": messages},
    )


async def test_async_http_client_batch_add_messages_url_encodes_session_id():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {
        "result": {"session_id": "encoded-session", "message_count": 1, "added": 1}
    }

    session_id = (
        "feishu__cli_a938e530eb7c9bd9__"
        "oc_aa9e08fddf5727f9c53400a07ff505cd#om_x100b6ff6c3df48ace10030ac68d3eb4"
    )

    await client.batch_add_messages(session_id, [{"role": "user", "content": "hello"}])

    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/"
        "feishu__cli_a938e530eb7c9bd9__"
        "oc_aa9e08fddf5727f9c53400a07ff505cd%23om_x100b6ff6c3df48ace10030ac68d3eb4"
        "/messages/batch",
        json={"messages": [{"role": "user", "content": "hello"}]},
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
            mode="prune_orphans",
            wait=False,
            dry_run=True,
        )

    assert result == {"status": "completed"}
    fake_http.post.assert_awaited_once_with(
        "/api/v1/content/reindex",
        json={
            "uri": "viking://resources/demo",
            "mode": "prune_orphans",
            "wait": False,
            "dry_run": True,
        },
    )
    assert mock_handle.called


def test_sync_http_client_reindex_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    with patch.object(
        client._async_client,
        "reindex",
        new_callable=Mock,
        return_value={"status": "accepted"},
    ) as mock_reindex:
        with patch(
            "openviking_sdk.client.run_async", return_value={"status": "accepted"}
        ) as mock_run:
            result = client.reindex(
                "viking://resources/demo",
                mode="prune_orphans",
                wait=False,
                dry_run=True,
            )

    assert result == {"status": "accepted"}
    assert mock_run.called
    mock_reindex.assert_called_once_with(
        uri="viking://resources/demo",
        mode="prune_orphans",
        wait=False,
        dry_run=True,
    )


def test_sync_http_client_batch_add_messages_forwards_to_async_client():
    client = SyncHTTPClient(url="http://localhost:1933")
    messages = [
        {
            "role": "user",
            "content": "hello",
            "peer_id": "explicit-user",
            "created_at": "2026-05-28T00:00:00+00:00",
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
    ]

    with patch.object(
        client._async_client,
        "batch_add_messages",
        return_value={"session_id": "batch-session", "message_count": 2, "added": 2},
    ) as mock_batch:
        with patch(
            "openviking_cli.client.sync_http.run_async",
            return_value={"session_id": "batch-session", "message_count": 2, "added": 2},
        ) as mock_run:
            result = client.batch_add_messages("batch-session", messages)

    assert result == {"session_id": "batch-session", "message_count": 2, "added": 2}
    assert mock_run.called
    mock_batch.assert_called_once_with("batch-session", messages, False)


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
