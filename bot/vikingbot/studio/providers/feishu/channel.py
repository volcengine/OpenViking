"""Feishu channel with persisted delivery evidence and a dedicated OV identity."""

import asyncio
import hashlib
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.config.schema import SessionKey


def now():
    return datetime.now(timezone.utc).isoformat()


class StudioFeishuChannel(FeishuChannel):
    def __init__(self, config, bus, *, record, store, **kwargs):
        super().__init__(config, bus, **kwargs)
        self.record = record
        self._session_prefix = f"studio:{record['id']}:"
        self.store = store
        self.verification = None
        self.last_received = None
        self.last_sent = None
        self.bot_open_id = record["bot_open_id"]
        self.last_error = None
        self.chat_names = {}

    async def start(self):
        from vikingbot.studio.providers.feishu.transport import run_connection

        await run_connection(self)

    async def _handle_message(
        self,
        sender_id,
        chat_id,
        content,
        sender_name=None,
        need_reply=True,
        media=None,
        metadata=None,
    ):
        if not self._running or not self.is_allowed(sender_id):
            return
        metadata = dict(metadata or {})
        metadata["studio_managed"] = True
        self.last_received = now()
        key = SessionKey(
            type="feishu", channel_id=self.channel_id, chat_id=self._session_prefix + chat_id
        )
        token = self.verification
        if (
            token
            and token["expires_at"] > time.time()
            and not token.get("received")
            and token["code"] in content
            and metadata.get("chat_type") == "group"
            and need_reply
        ):
            token["received"] = True
            token["conversation"] = chat_id
            token["sent"] = bool(
                await self.send(
                    OutboundMessage(
                        session_key=key,
                        content="VikingBot 已连接，可以 @我开始对话。",
                        metadata={**metadata, "studio_verification": True},
                    )
                )
            )
            return
        # Expired/replayed onboarding messages must never invoke the agent.
        if "VikingBot connection test" in content:
            return
        event_id = metadata.get("message_id", str(uuid.uuid4()))
        inserted = self.store.append(
            self.record["id"],
            chat_id,
            event_id,
            {
                "role": "user",
                "content": content,
                "sender": sender_name or "",
                "sender_id": sender_id,
                "time": now(),
                "status": "received",
                "chat_type": metadata.get("chat_type"),
                "topic_title": metadata.get("topic_title", ""),
                "title": await self.chat_title(chat_id, content, metadata),
                "group_name": self.chat_names.get(
                    metadata.get("reply_to", chat_id).split("#")[0], ""
                )
                if metadata.get("chat_type") == "group"
                else "",
            },
        )
        if not inserted:
            return
        connection = dict(self.record["identity"])
        # Partition peer memory by group/topic, never by the installing administrator.
        peer = "feishu-" + hashlib.sha256(chat_id.encode()).hexdigest()[:24]
        await self.bus.publish_inbound(
            InboundMessage(
                sender_id=sender_id,
                sender_name=sender_name,
                session_key=key,
                actor_peer_id=peer,
                content=content,
                need_reply=need_reply,
                media=media or [],
                metadata=metadata,
                openviking_connection=connection,
            )
        )

    async def history_with_names(self, conversation, before=0):
        messages = self.store.history(self.record["id"], conversation, before)
        for message in messages:
            sender_id = message.get("sender_id")
            if message.get("role") != "user" or message.get("sender") or not sender_id:
                continue
            group = message.get("conversation", "").split("#")[0]
            try:
                name = await asyncio.wait_for(
                    self._get_group_member_name(group, sender_id), timeout=3
                )
            except Exception:
                break
            if name:
                message["sender"] = name
                self.store.update_sender(self.record["id"], message["id"], name)
        return messages

    async def chat_title(self, chat_id, content, metadata):
        group_id = metadata.get("reply_to", chat_id).split("#")[0]
        if metadata.get("chat_type") != "group":
            return metadata.get("sender_name") or ""
        if group_id not in self.chat_names and self._client:
            from lark_oapi.api.im.v1 import GetChatRequest

            try:
                request = GetChatRequest.builder().chat_id(group_id).build()
                response = await asyncio.to_thread(self._client.im.v1.chat.get, request)
                if response.success():
                    self.chat_names[group_id] = response.data.name
            except Exception:
                pass
        name = self.chat_names.get(group_id, "")
        if "#" in chat_id and name:
            return name + " / " + content[:40]
        return name

    async def _download_viking_image(self, uri, msg):
        from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
        from vikingbot.openviking_mount.ov_server import VikingClient
        from vikingbot.utils.image_format import sniff_image_format

        if not msg:
            raise ValueError("Missing message scope")
        peer = "feishu-" + hashlib.sha256(msg.session_key.chat_id.encode()).hexdigest()[:24]
        client = await VikingClient.create(
            connection=self.record["identity"],
            actor_peer_id=peer,
            config=self._bot_config,
        )
        try:
            info = await client.stat(uri)
            size = info.get("size")
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or not 0 < size <= MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
            ):
                raise ValueError("Image exceeds delivery limit")
            data = await client.download_bytes(uri)
            if len(data) > size or sniff_image_format(data) is None:
                raise ValueError("Unsupported image")
            return data
        finally:
            await client.close()

    async def send(self, msg):
        # A replaced connection must never deliver an old in-flight response.
        if not msg.session_key.chat_id.startswith(self._session_prefix):
            return False
        # Only the Agent session is scoped; Feishu delivery and captured history
        # continue using the original group/topic identifier.
        msg = replace(
            msg,
            session_key=msg.session_key.model_copy(
                update={"chat_id": msg.session_key.chat_id.removeprefix(self._session_prefix)}
            ),
        )
        accepted = await super().send(msg)
        if msg.is_normal_message:
            if accepted:
                self.last_sent = now()
            if not msg.metadata.get("studio_verification"):
                self.store.append(
                    self.record["id"],
                    msg.session_key.chat_id,
                    msg.response_id or str(uuid.uuid4()),
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "sender": self.config.bot_name,
                        "time": now(),
                        "status": "sent" if accepted else "send_failed",
                    },
                )
        return accepted

    async def stop(self):
        self._running = False
        socket = getattr(self._ws_client, "_conn", None)
        if socket:
            await socket.close()

    def status(self):
        # Socket evidence, never infer connection from the channel's start flag.
        socket = getattr(self._ws_client, "_conn", None)
        connected = socket is not None and not getattr(socket, "closed", False)
        return {
            "state": "connected" if connected and self._running else "connecting",
            "last_received": self.last_received,
            "last_sent": self.last_sent,
            "verification": self.verification,
            "last_error": self.last_error,
        }
