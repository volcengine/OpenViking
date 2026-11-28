"""Default Feishu channels must keep their WebSocket connections independent."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import lark_oapi as lark
import pytest
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.channels.manager import ChannelManager
from vikingbot.config.schema import Config, FeishuChannelConfig
from vikingbot.studio.providers.feishu import transport


class _Socket:
    def __init__(self, *, close_error=False):
        self.closed = False
        self.close_error = close_error
        self._closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._closed.wait()
        raise StopAsyncIteration

    async def close(self):
        if self.close_error:
            raise OSError("socket close failed")
        self.closed = True
        self._closed.set()


@pytest.mark.parametrize("close_error", [False, True])
@pytest.mark.asyncio
async def test_default_feishu_channels_connect_and_stop_independently(monkeypatch, close_error):
    requests = {}
    sockets = {}
    connected = {app_id: asyncio.Event() for app_id in ("cn_app", "intl_app")}
    handler_keys = []
    original_async_client = httpx.AsyncClient
    original_handler_builder = lark.EventDispatcherHandler.builder

    async def endpoint(request):
        payload = json.loads(request.content)
        app_id = payload["AppID"]
        requests[app_id] = (str(request.url), payload["AppSecret"])
        return httpx.Response(
            200,
            json={"code": 0, "data": {"URL": f"wss://socket.example/ws?service_id={app_id}"}},
        )

    def fake_async_client(*args, **kwargs):
        return original_async_client(*args, transport=httpx.MockTransport(endpoint), **kwargs)

    def fake_connect(url):
        app_id = parse_qs(urlsplit(url).query)["service_id"][0]
        socket = _Socket(close_error=close_error and app_id == "cn_app")
        sockets[app_id] = socket
        connected[app_id].set()
        return socket

    def recording_handler_builder(encrypt_key, verification_token):
        handler_keys.append((encrypt_key, verification_token))
        return original_handler_builder(encrypt_key, verification_token)

    async def quiet_ping(_client):
        await asyncio.Event().wait()

    def forbidden_sdk_start(_client):
        raise RuntimeError("SDK start uses a process-global event loop")

    monkeypatch.setattr(transport.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(transport.websockets, "connect", fake_connect)
    monkeypatch.setattr(lark.EventDispatcherHandler, "builder", recording_handler_builder)
    monkeypatch.setattr(lark.ws.Client, "_ping_loop", quiet_ping)
    monkeypatch.setattr(lark.ws.Client, "start", forbidden_sdk_start)

    first_config = FeishuChannelConfig(
        app_id="cn_app",
        app_secret="cn_secret",
        encrypt_key="cn_encrypt",
        verification_token="cn_verify",
    )
    second_config = FeishuChannelConfig(
        app_id="intl_app",
        app_secret="intl_secret",
        domain="https://open.larksuite.com",
        encrypt_key="intl_encrypt",
        verification_token="intl_verify",
    )
    manager = ChannelManager(MessageBus())
    manager.load_channels_from_config(
        Config.model_validate({"channels": [first_config, second_config]})
    )
    first = manager.channels["feishu__cn_app"]
    second = manager.channels["feishu__intl_app"]
    assert isinstance(first, FeishuChannel)
    assert isinstance(second, FeishuChannel)
    manager_task = asyncio.create_task(manager.start_all())
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in connected.values())), timeout=1
        )
        assert requests == {
            "cn_app": ("https://open.feishu.cn/callback/ws/endpoint", "cn_secret"),
            "intl_app": ("https://open.larksuite.com/callback/ws/endpoint", "intl_secret"),
        }
        assert set(handler_keys) == {
            ("cn_encrypt", "cn_verify"),
            ("intl_encrypt", "intl_verify"),
        }
        assert first._client._config.domain == "https://open.feishu.cn"
        assert second._client._config.domain == "https://open.larksuite.com"
        assert first._ws_client._domain == "https://open.feishu.cn"
        assert second._ws_client._domain == "https://open.larksuite.com"

        first_task = first._connection_task
        assert first_task is not None
        await first.stop()
        await asyncio.wait_for(first_task, timeout=1)
        assert first._ws_client._conn is None
        assert sockets["cn_app"].closed == (not close_error)
        assert not sockets["intl_app"].closed
        assert not manager_task.done()

        await second.stop()
        await asyncio.wait_for(manager_task, timeout=1)
        assert sockets["intl_app"].closed
    finally:
        if "cn_app" in sockets:
            sockets["cn_app"].close_error = False
        await first.stop()
        await second.stop()
        manager_task.cancel()
        tasks_to_join = [manager_task]
        if manager._dispatch_task is not None:
            manager._dispatch_task.cancel()
            tasks_to_join.append(manager._dispatch_task)
        await asyncio.gather(*tasks_to_join, return_exceptions=True)


@pytest.mark.asyncio
async def test_default_feishu_stop_cancels_pending_endpoint_request(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    original_async_client = httpx.AsyncClient

    async def endpoint(_request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    def fake_async_client(*args, **kwargs):
        return original_async_client(*args, transport=httpx.MockTransport(endpoint), **kwargs)

    monkeypatch.setattr(transport.httpx, "AsyncClient", fake_async_client)
    channel = FeishuChannel(
        FeishuChannelConfig(app_id="pending_app", app_secret="secret"), MessageBus()
    )
    task = asyncio.create_task(channel.start())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await channel.stop()
        await asyncio.wait_for(task, timeout=1)
        assert cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
