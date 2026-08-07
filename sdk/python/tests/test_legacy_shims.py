import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _purge_legacy_modules() -> None:
    for name in list(sys.modules):
        if (
            name == "openviking_sdk"
            or name.startswith("openviking_sdk.")
            or name == "openviking_cli"
            or name.startswith("openviking_cli.")
        ):
            sys.modules.pop(name, None)


def test_legacy_async_http_client_shim_points_to_sdk():
    _purge_legacy_modules()
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")
    assert client._url == "http://localhost:1933"
    try:
        client._raise_exception({"code": "CONFLICT", "message": "boom"})
    except Exception as exc:
        assert getattr(exc, "code", None) == "CONFLICT"


def test_legacy_async_http_client_shim_imports_from_repo_checkout(monkeypatch):
    _purge_legacy_modules()
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if path != str(SDK_ROOT)],
    )

    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")
    assert client._url == "http://localhost:1933"


def test_legacy_async_http_client_shim_raises_legacy_exceptions():
    _purge_legacy_modules()
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient
    from openviking_cli.exceptions import ConflictError

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")

    with pytest.raises(ConflictError) as exc_info:
        client._raise_exception({"code": "CONFLICT", "message": "boom"})

    assert exc_info.value.code == "CONFLICT"


def test_legacy_sync_http_client_shim_points_to_sdk():
    _purge_legacy_modules()
    from openviking_cli.client.sync_http import SyncHTTPClient as LegacySyncHTTPClient

    client = LegacySyncHTTPClient(url="http://localhost:1933")
    assert client._async_client._url == "http://localhost:1933"


@pytest.mark.asyncio
async def test_legacy_async_http_client_commit_preserves_explicit_empty_event_tags():
    _purge_legacy_modules()
    from openviking_cli.client.http import AsyncHTTPClient as LegacyAsyncHTTPClient

    client = LegacyAsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(post=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response_data = lambda _response: {"result": {"status": "accepted"}}

    await client.session("s1").commit(event_tags=[])

    fake_http.post.assert_awaited_once_with(
        "/api/v1/sessions/s1/commit",
        json={
            "keep_recent_count": 0,
            "telemetry": False,
            "extraction_metadata": {"event": {"tags": []}},
        },
    )


def test_legacy_sync_http_client_commit_forwards_event_tags():
    _purge_legacy_modules()
    from openviking_cli.client.sync_http import SyncHTTPClient as LegacySyncHTTPClient

    client = LegacySyncHTTPClient(url="http://localhost:1933")
    commit = AsyncMock(return_value={"status": "accepted"})
    client._async_client.commit_session = commit

    client.session("s1").commit(event_tags=["team=search"])

    commit.assert_called_once_with(
        "s1",
        telemetry=False,
        keep_recent_count=0,
        retention_mode=None,
        keep_recent_turn_count=None,
        retained_message_token_budget=None,
        min_raw_tail_steps=None,
        event_tags=["team=search"],
    )
