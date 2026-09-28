"""Cancellable Feishu transport, using the SDK only for its wire protocol.

The SDK's public start() owns a module-global event loop and cannot run multiple
managed connections or be stopped independently. Keep its protocol calls here.
"""

import asyncio
from contextlib import suppress
from urllib.parse import parse_qs, urlsplit

import httpx
import lark_oapi as lark
import websockets
from lark_oapi.ws.model import ClientConfig


async def run_connection(channel):
    channel._running = True
    channel._loop = asyncio.get_running_loop()
    channel._client = (
        lark.Client.builder()
        .app_id(channel.config.app_id)
        .app_secret(channel.config.app_secret)
        .build()
    )
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(channel._on_message_sync)
        .build()
    )
    client = lark.ws.Client(
        channel.config.app_id,
        channel.config.app_secret,
        event_handler=handler,
        auto_reconnect=False,
    )
    channel._ws_client = client
    while channel._running:
        ping = None
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                response = await http.post(
                    "https://open.feishu.cn/callback/ws/endpoint",
                    json={"AppID": channel.config.app_id, "AppSecret": channel.config.app_secret},
                )
                response.raise_for_status()
                endpoint = response.json()
            if endpoint.get("code") != 0:
                raise ValueError("Feishu rejected the long connection")
            data = endpoint["data"]
            url = data["URL"]
            query = parse_qs(urlsplit(url).query)
            client._service_id = query["service_id"][0]
            if data.get("ClientConfig"):
                client._configure(ClientConfig(data["ClientConfig"]))
            async with websockets.connect(url) as socket:
                client._conn = socket
                channel.last_error = None
                ping = asyncio.create_task(client._ping_loop())
                async for frame in socket:
                    if not channel._running:
                        break
                    await client._handle_message(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # No endpoint URL or credentials in public diagnostics.
            channel.last_error = type(exc).__name__
        finally:
            client._conn = None
            if ping:
                ping.cancel()
                with suppress(asyncio.CancelledError):
                    await ping
        if channel._running:
            await asyncio.sleep(5)
