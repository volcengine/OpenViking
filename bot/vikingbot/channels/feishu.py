"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import io
import json
import re
import tempfile
import threading
from collections import OrderedDict
from typing import Any

import httpx
from loguru import logger

from vikingbot.config import load_config
from vikingbot.utils import get_data_path

# Optional HTML processing libraries
try:
    import html2text
    from bs4 import BeautifulSoup
    from readability import Document

    HTML_PROCESSING_AVAILABLE = True
except ImportError:
    HTML_PROCESSING_AVAILABLE = False
    html2text = None
    BeautifulSoup = None
    Document = None

from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.base import BaseChannel
from vikingbot.config.schema import FeishuChannelConfig, BotMode

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        Emoji,
        GetChatRequest,
        GetImageRequest,
        GetMessageResourceRequest,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody
    )

    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None
    GetImageRequest = None

# Message type display mapping
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


class FeishuChannel(BaseChannel):
    """
    Feishu/Lark channel using WebSocket long connection.

    Uses WebSocket to receive events - no public IP or webhook required.

    Requires:
    - App ID and App Secret from Feishu Open Platform
    - Bot capability enabled
    - Event subscription enabled (im.message.receive_v1)
    """

    name = "feishu"
    # 飞书官方支持的处理中表情列表，按顺序发送
    PROCESSING_EMOJIS = [
        "StatusInFlight",
        "OneSecond",
        "Typing",
        "OnIt",
        "Coffee",
        "OnIt",
        "EatingFood",
    ]

    def __init__(self, config: FeishuChannelConfig, bus: MessageBus, **kwargs):
        super().__init__(config, bus, **kwargs)
        self.config: FeishuChannelConfig = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # Ordered dedup cache
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tenant_access_token: str | None = None
        self._token_expire_time: float = 0
        self._chat_mode_cache: dict[str, str] = {}  # 缓存群类型：group(普通群)/thread(话题群)

    async def _get_tenant_access_token(self) -> str:
        """Get tenant access token for Feishu API."""
        import time

        now = time.time()
        if (
            self._tenant_access_token and now < self._token_expire_time - 60
        ):  # Refresh 1 min before expire
            return self._tenant_access_token

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.config.app_id, "app_secret": self.config.app_secret}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != 0:
                raise Exception(f"Failed to get tenant access token: {result}")

            self._tenant_access_token = result["tenant_access_token"]
            self._token_expire_time = now + result.get("expire", 7200)
            return self._tenant_access_token

    async def _upload_image_to_feishu(self, image_data: bytes) -> str:
        """
        Upload image to Feishu media library and get image_key.
        """

        token = await self._get_tenant_access_token()
        url = "https://open.feishu.cn/open-apis/im/v1/images"

        headers = {"Authorization": f"Bearer {token}"}

        # Use io.BytesIO properly
        files = {"image": ("image.png", io.BytesIO(image_data), "image/png")}
        data = {"image_type": "message"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
            # logger.debug(f"Upload response status: {resp.status_code}")
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != 0:
                raise Exception(f"Failed to upload image: {result}")
            return result["data"]["image_key"]

    async def _download_feishu_image(self, image_key: str, message_id: str | None = None) -> bytes:
        """
        Download an image from Feishu using image_key. If message_id is provided,
        uses GetMessageResourceRequest (for user-sent images), otherwise uses GetImageRequest.
        """
        if not self._client:
            raise Exception("Feishu client not initialized")

        if message_id:
            # Use GetMessageResourceRequest for user-sent images
            request: GetMessageResourceRequest = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(image_key)
                .type("image")
                .build()
            )
            response = await self._client.im.v1.message_resource.aget(request)
        else:
            # Use GetImageRequest for bot-sent/images uploaded via API
            request: GetImageRequest = GetImageRequest.builder().image_key(image_key).build()
            response = await self._client.im.v1.image.aget(request)

        # Handle failed response
        if not response.success():
            raw_detail = getattr(getattr(response, 'raw', None), 'content', response.msg)
            raise Exception(
                f"Failed to download image: code={response.code}, msg={raw_detail}, log_id={response.get_log_id()}"
            )

        # Read the image bytes from the response file
        return response.file.read()

    async def _save_image_to_temp(self, image_bytes: bytes) -> str:
        """
        Save image bytes to a temporary file and return the path.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            temp_path = f.name

        return temp_path

    async def _get_chat_mode(self, chat_id: str) -> str:
        """获取群类型：group(普通群)/thread(话题群)"""
        if chat_id in self._chat_mode_cache:
            return self._chat_mode_cache[chat_id]

        if not self._client:
            return "group"  # 默认普通群

        try:
            request: GetChatRequest = GetChatRequest.builder() \
                .chat_id(chat_id) \
                .user_id_type("open_id") \
                .build()
            response = await self._client.im.v1.chat.aget(request)
            # 处理失败返回
            if not response.success():
                logger.warning(f"client.im.v1.chat.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}")
                return "group"

            # 处理业务结果
            data = response.data
            mode = "group"
            group_message_type = getattr(data, "group_message_type", "")
            if group_message_type and group_message_type == "thread":
                mode = "thread"
            else:
                chat_mode = getattr(data, "chat_mode", "")
                if chat_mode and chat_mode == "topic":
                    mode = "thread"
            self._chat_mode_cache[chat_id] = mode
            return mode
        except Exception as e:
            logger.warning(f"Error getting chat mode: {e}")

        return "group"  # 失败默认普通群

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.exception(
                "Feishu SDK not installed. Install with: uv pip install 'openviking[bot-feishu]' (or uv pip install -e \".[bot-feishu]\" for local dev)"
            )
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.exception("Feishu app_id and app_secret not configured")
            return

        self._running = True
        self._loop = asyncio.get_running_loop()

        # Create Lark client for sending messages
        self._client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        # Create event handler (only register message receive, ignore other events)
        event_handler = (
            lark.EventDispatcherHandler.builder(
                self.config.encrypt_key or "",
                self.config.verification_token or "",
            )
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )

        # Create WebSocket client for long connection
        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # Start WebSocket client in a separate thread with reconnect loop
        def run_ws():
            while self._running:
                try:
                    self._ws_client.start()
                except Exception as e:
                    logger.exception(f"Feishu WebSocket error: {e}")
                if self._running:
                    import time

                    time.sleep(5)

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("Feishu bot started with WebSocket long connection")
        logger.info("No public IP required - using WebSocket to receive events")

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Feishu bot."""
        self._running = False
        if self._ws_client:
            try:
                # Try to close the WebSocket connection gracefully
                if hasattr(self._ws_client, "close"):
                    self._ws_client.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket client: {e}")
        logger.info("Feishu bot stopped")

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """Sync helper for adding reaction (runs in thread pool)."""
        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )

            response = self._client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning(f"Failed to add reaction: code={response.code}, msg={response.msg}")
        except Exception as e:
            logger.warning(f"Error adding reaction: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        Add a reaction emoji to a message (non-blocking).

        Common emoji types: THUMBSUP, OK, EYES, DONE, OnIt, HEART
        """
        if not self._client or not Emoji:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    async def send_processing_reaction(self, message_id: str, emoji: str) -> None:
        """
        Send processing reaction emoji implementation for Feishu.
        """
        await self._add_reaction(message_id, emoji)

    async def handle_processing_tick(self, message_id: str, tick_count: int) -> None:
        """
        Handle processing tick event, send corresponding emoji reaction.
        """
        if 0 <= tick_count < len(self.PROCESSING_EMOJIS):
            emoji = self.PROCESSING_EMOJIS[tick_count]
            await self.send_processing_reaction(message_id, emoji)

    # Regex to match markdown tables (header + separator + data rows)
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

    @staticmethod
    def _parse_md_table(table_text: str) -> dict | None:
        """Parse a markdown table into a Feishu table element."""
        lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
        if len(lines) < 3:
            return None

        def split(l: str) -> list[str]:
            return [c.strip() for c in l.strip("|").split("|")]

        headers = split(lines[0])
        rows = [split(l) for l in lines[2:]]
        columns = [
            {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
            for i, h in enumerate(headers)
        ]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [
                {f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows
            ],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        """Split content into div/markdown + table elements for Feishu card."""
        elements, last_end = [], 0
        table_count = 0
        max_tables = 5  # Feishu card table limit

        for m in self._TABLE_RE.finditer(content):
            before = content[last_end : m.start()]
            if before.strip():
                elements.extend(self._split_headings(before))

            if table_count < max_tables:
                elements.append(
                    self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)}
                )
                table_count += 1
            else:
                # Exceeded table limit, render as markdown instead
                elements.append({"tag": "markdown", "content": m.group(1)})

            last_end = m.end()

        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))

        return elements or [{"tag": "markdown", "content": content}]

    def _split_headings(self, content: str) -> list[dict]:
        """Split content by headings, converting headings to div elements."""
        protected = content
        code_blocks = []
        for m in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(m.group(1))
            protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks) - 1}\x00", 1)

        elements = []
        last_end = 0
        for m in self._HEADING_RE.finditer(protected):
            before = protected[last_end : m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            text = m.group(2).strip()
            elements.append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{text}**",
                    },
                }
            )
            last_end = m.end()
        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for i, cb in enumerate(code_blocks):
            for el in elements:
                if el.get("tag") == "markdown":
                    el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

        return elements or [{"tag": "markdown", "content": content}]

    async def _process_content_with_images(
        self, content: str, receive_id_type: str, chat_id: str
    ) -> list[dict]:
        """
        Process content, extract and upload Markdown images, return card elements.

        Returns: list of card elements (markdown + img elements)
        """
        # Extract images from Markdown
        images = []
        markdown_pattern = r"!\[([^\]]*)\]\((send://[^)\s]+\.(png|jpeg|jpg|gif|bmp|webp))\)"
        # Find all images and upload them
        for m in re.finditer(markdown_pattern, content):
            alt_text = m.group(1) or ""
            img_url = m.group(2)
            try:
                is_content, result = await self._parse_data_uri(img_url)

                if not is_content and isinstance(result, bytes):
                    # It's an image - upload
                    image_key = await self._upload_image_to_feishu(result)
                    images.append({"alt": alt_text, "img_key": image_key})
            except Exception as e:
                logger.exception(f"Failed to upload Markdown image {img_url[:100]}: {e}")
        content = re.sub(markdown_pattern, "", content)

        # Pattern: ![alt](url)
        send_pattern = r"(send://[^)\s]+\.(png|jpeg|jpg|gif|bmp|webp))\)?"
        # Find all images and upload them
        for m in re.finditer(send_pattern, content):
            img_url = m.group(1) or ""
            try:
                is_content, result = await self._parse_data_uri(img_url)

                if not is_content and isinstance(result, bytes):
                    # It's an image - upload
                    image_key = await self._upload_image_to_feishu(result)
                    images.append({"img_key": image_key})
            except Exception as e:
                logger.exception(f"Failed to upload Markdown image {img_url[:100]}: {e}")

        # Remove all ![alt](url) from content
        content_no_images = re.sub(send_pattern, "", content)

        elements = []
        if content_no_images.strip():
            elements = self._build_card_elements(content_no_images)

        # Add image elements
        for img in images:
            elements.append({"tag": "img", "img_key": img["img_key"]})

        if not elements:
            elements = [{"tag": "markdown", "content": content_no_images}]

        return elements

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu."""
        # 先调用基类处理通用动作
        if await super().send(msg):
            return

        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        # Only send normal response messages, skip thinking/tool_call/etc.
        if not msg.is_normal_message:
            return

        try:
            # logger.info(f"Sending message {msg}")
            # Determine receive_id_type based on chat_id format
            # open_id starts with "ou_", chat_id starts with "oc_"
            reply_to = msg.metadata.get("reply_to")
            if reply_to.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"

            # Process images and get cleaned content
            cleaned_content, images = await self._extract_and_upload_images(msg.content)

            content_with_mentions = cleaned_content

            # Check if we need to reply to a specific message
            # Get reply message ID from metadata (original incoming message ID)
            reply_to_message_id = None
            if msg.metadata:
                reply_to_message_id = msg.metadata.get("reply_to_message_id") or msg.metadata.get(
                    "message_id"
                )

            # Build post message content
            content_elements = []

            # Add @mention for the original sender when replying
            original_sender_id = None
            chat_type = "group"
            if reply_to_message_id and msg.metadata:
                original_sender_id = msg.metadata.get("sender_id")
                chat_type = msg.metadata.get("chat_type", "group")

            # Build content line: [@mention, text content]
            content_line = []

            # Add @mention element for original sender when replying (only in group chats)
            if original_sender_id and chat_type == "group":
                content_line.append({"tag": "at", "user_id": original_sender_id})

            # Add text content
            if content_with_mentions.strip():
                content_line.append({"tag": "text", "text": content_with_mentions})

            # Add content line if not empty
            if content_line:
                content_elements.append(content_line)

            # Add images
            for img in images:
                content_elements.append([{"tag": "img", "image_key": img["image_key"]}])

            # Ensure we have content
            if not content_elements:
                content_elements.append([{"tag": "text", "text": " "}])

            post_content = {"zh_cn": {"title": "", "content": content_elements}}

            import json

            content = json.dumps(post_content, ensure_ascii=False)

            if reply_to_message_id:
                # Reply to existing message (quotes the original)
                # Only reply in thread if the original message is in a topic (has root_id and is a thread)
                should_reply_in_thread = False
                if msg.metadata:
                    root_id = msg.metadata.get("root_id")
                    # Only use reply_in_thread=True if this is an actual topic group thread
                    # In Feishu, topic groups have root_id set for messages in threads
                    # root_id will be set if the message is already part of a thread
                    should_reply_in_thread = root_id is not None and root_id != reply_to_message_id

                request = (
                    ReplyMessageRequest.builder()
                    .message_id(reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(content)
                        .msg_type("post")
                        # Only reply in topic thread if it's actually a topic thread (not regular group)
                        .reply_in_thread(should_reply_in_thread)
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.message.reply(request)
            else:
                # Send new message
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(reply_to)
                        .msg_type("post")
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.message.create(request)

            if not response.success():
                if response.code == 230011:
                    # Original message was withdrawn, just log warning
                    logger.warning(
                        f"Failed to reply to message: original message was withdrawn, code={response.code}, "
                        f"msg={response.msg}, log_id={response.get_log_id()}"
                    )
                else:
                    logger.exception(
                        f"Failed to send Feishu message: code={response.code}, "
                        f"msg={response.msg}, log_id={response.get_log_id()}"
                    )

        except Exception as e:
            logger.exception(f"Error sending Feishu message: {e}")

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """
        Sync handler for incoming messages (called from WebSocket thread).
        Schedules async handling in the main event loop.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # Deduplication check
            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None

            # Trim cache: keep most recent 500 when exceeds 1000
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            # Skip bot messages
            sender_type = sender.sender_type
            if sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type  # "p2p" or "group"
            msg_type = message.message_type

            # Parse message content and media first to check mentions
            content = ""
            media = []

            if msg_type == "text":
                try:
                    content = json.loads(message.content).get("text", "")
                except json.JSONDecodeError:
                    content = message.content or ""
            elif msg_type == "image" or msg_type == "post":
                # Handle both image and post types
                content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
                text_content = ""
                try:
                    # Parse message content to get image_key
                    msg_content = json.loads(message.content)
                    image_keys = []

                    # Try to get image_key from different possible locations
                    if msg_type == "image":
                        image_key = msg_content.get("image_key")
                        if image_key:
                            image_keys.append(image_key)
                    elif msg_type == "post":
                        # For post messages, extract content and all images
                        # Post structure: {"title": "", "content": [[{"tag": "img", "image_key": "..."}], [{"tag": "text", "text": "..."}]]}
                        post_content = msg_content.get("content", [])

                        # Extract all images by tag, regardless of position
                        for block in post_content:
                            for element in block:
                                if element.get("tag") == "img":
                                    img_key = element.get("image_key")
                                    if img_key:
                                        image_keys.append(img_key)

                        # Extract text content from the post
                        text_parts = []
                        for block in post_content:
                            for element in block:
                                if element.get("tag") == "text":
                                    text_parts.append(element.get("text", ""))
                        text_content = " ".join(text_parts).strip()
                        if text_content:
                            content = text_content

                    # Process each image key
                    if image_keys:
                        for image_key in image_keys:
                            # Download image using the SDK client
                            logger.info(
                                f"Downloading Feishu image with image_key: {image_key}, message_id: {message_id}"
                            )
                            image_bytes = await self._download_feishu_image(image_key, message_id)
                            if image_bytes:
                                # Save to workspace/media directory

                                media_dir = get_data_path() / "received"

                                media_dir.mkdir(parents=True, exist_ok=True)

                                import uuid

                                file_path = media_dir / f"feishu_{uuid.uuid4().hex[:16]}.png"
                                file_path.write_bytes(image_bytes)

                                media.append(str(file_path))
                                logger.info(f"Feishu image saved to: {file_path}")
                            else:
                                logger.warning(
                                    f"Could not download image for image_key: {image_key}"
                                )
                except Exception as e:
                    logger.warning(f"Failed to download Feishu image: {e}")
                    import traceback

                    logger.debug(f"Stack trace: {traceback.format_exc()}")
            else:
                content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")

            if not content:
                return

            import re

            # 检查是否@了机器人
            is_mentioned = False
            mention_pattern = re.compile(r"@_user_\d+")
            bot_name = self.config.bot_name

            # 优先从message的mentions字段提取@信息（text和post类型都适用）
            if hasattr(message, 'mentions') and message.mentions and bot_name:
                for mention in message.mentions:
                    if hasattr(mention, 'name'):
                        at_name = mention.name
                        if at_name == self.config.bot_name:
                            is_mentioned = True
                            break
                        continue
            # 话题群@检查逻辑
            config = load_config()
            should_process = True
            if chat_type == "group":
                chat_mode = await self._get_chat_mode(chat_id)
                if chat_mode == "thread":
                    # 判断是否是话题的首条消息（root_id等于message_id说明是话题发起消息）
                    is_topic_starter = message.root_id == message.message_id or not message.root_id

                    if self.config.thread_require_mention:
                        # 模式1：默认True，所有消息都需要@才处理
                        if not is_mentioned:
                            logger.info(f"Skipping thread message: thread_require_mention is True and not mentioned")
                            should_process = False
                    else:
                        # 模式2：False，仅话题首条消息不需要@，后续回复需要@
                        if not is_topic_starter and not is_mentioned and config.mode != BotMode.DEBUG:
                            logger.info(f"Skipping thread message: not topic starter and not mentioned")
                            should_process = False

            # 不需要处理的消息直接跳过
            if not should_process:
                return

            # 确认需要处理后再添加"已读"表情
            if config and config.mode != BotMode.DEBUG:
                await self._add_reaction(message_id, "MeMeMe")

            # 替换所有@占位符
            content = mention_pattern.sub(f"@{sender_id}", content)

            # Forward to message bus
            reply_to = chat_id if chat_type == "group" else sender_id
            logger.info(f"Received message from Feishu: {content}")

            # 话题群处理：如果是话题群，首次消息root_id为空时，将当前消息id设为root_id
            if chat_type == "group":
                chat_mode = await self._get_chat_mode(chat_id)
                if chat_mode == "thread" and not message.root_id:
                    message.root_id = message.message_id
                if chat_mode == "thread" and message.root_id:
                    chat_id = f"{reply_to}#{message.root_id}"
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                media=media if media else None,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "reply_to": reply_to,
                    "msg_type": msg_type,
                    "root_id": message.root_id,  # Topic/thread ID for topic groups
                    "sender_id": sender_id,  # Original message sender ID for @mention in replies
                },
            )

        except Exception:
            logger.exception("Error processing Feishu message")

    async def _extract_and_upload_images(self, content: str) -> tuple[str, list[dict]]:
        """Extract images from markdown content, upload to Feishu, and return cleaned content."""
        images = []
        cleaned_content = content

        # Pattern 1: ![alt](send://...)
        markdown_pattern = r"!\[([^\]]*)\]\((send://[^)\s]+\.(png|jpeg|jpg|gif|bmp|webp))\)"
        for m in re.finditer(markdown_pattern, content):
            img_url = m.group(2)
            try:
                is_content, result = await self._parse_data_uri(img_url)

                if not is_content and isinstance(result, bytes):
                    image_key = await self._upload_image_to_feishu(result)
                    images.append({"image_key": image_key})
            except Exception as e:
                logger.exception(f"Failed to upload Markdown image {img_url[:100]}: {e}")

        # Remove markdown image syntax
        cleaned_content = re.sub(markdown_pattern, "", cleaned_content)

        # Pattern 2: send://... (without alt text)
        send_pattern = r"(send://[^)\s]+\.(png|jpeg|jpg|gif|bmp|webp))\)?"
        for m in re.finditer(send_pattern, content):
            img_url = m.group(1) or ""
            try:
                is_content, result = await self._parse_data_uri(img_url)

                if not is_content and isinstance(result, bytes):
                    image_key = await self._upload_image_to_feishu(result)
                    images.append({"image_key": image_key})
            except Exception as e:
                logger.exception(f"Failed to upload Markdown image {img_url[:100]}: {e}")

        # Remove standalone send:// URLs
        cleaned_content = re.sub(send_pattern, "", cleaned_content)

        return cleaned_content.strip(), images
