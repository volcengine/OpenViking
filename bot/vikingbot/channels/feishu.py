"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import io
import json
import re
import tempfile
import threading
import time
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
from vikingbot.config.schema import BotMode, FeishuChannelConfig

try:
    import lark_oapi as lark
    from lark_oapi.api.contact.v3 import (
        BatchGetIdUserRequest,
        BatchGetIdUserRequestBody,
        GetUserRequest,
    )
    from lark_oapi.api.im.v1 import (
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
        Emoji,
        GetChatMembersRequest,
        GetChatRequest,
        GetImageRequest,
        GetMessageResourceRequest,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None
    GetImageRequest = None
    GetUserRequest = None
    GetChatMembersRequest = None
    BatchGetIdUserRequest = None
    BatchGetIdUserRequestBody = None

# Message type display mapping
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}

# Pre-compiled regex patterns
OPEN_ID_MENTION_PATTERN = re.compile(r"@ou_[a-f0-9]+")


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
        self._user_name_cache: OrderedDict[str, str] = OrderedDict()  # LRU缓存用户ID到姓名的映射
        self._bot_name_cache: dict[str, str] = {}  # 缓存机器人open_id到名称的映射
        self._chat_member_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()  # chat_id -> {members, expires_at, last_error_at}
        self._MAX_USER_CACHE_SIZE = 1000  # 最大缓存1000个用户
        self._CHAT_MEMBER_CACHE_TTL_SEC = 300
        self._CHAT_MEMBER_CACHE_MAX_CHATS = 30
        self._CHAT_MEMBER_FETCH_COOLDOWN_SEC = 60
        self._CHAT_MEMBER_FETCH_PAGE_SIZE = 100
        self._CHAT_MEMBER_FETCH_MAX_PAGES = 500

    async def _get_tenant_access_token(self) -> str:
        """Get tenant access token for Feishu API."""
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
            raw_detail = getattr(getattr(response, "raw", None), "content", response.msg)
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
            request: GetChatRequest = (
                GetChatRequest.builder().chat_id(chat_id).user_id_type("open_id").build()
            )
            response = await self._client.im.v1.chat.aget(request)
            # 处理失败返回
            if not response.success():
                logger.warning(
                    f"client.im.v1.chat.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
                )
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
            elements.append(
                {
                    "tag": "img",
                    "img_key": img["img_key"],
                    "alt": {"tag": "plain_text", "content": ""},
                }
            )

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

            # --- Build interactive card with markdown rendering ---
            reply_to_message_id = None
            original_sender_id = None
            chat_type = "group"
            if msg.metadata:
                reply_to_message_id = msg.metadata.get("reply_to_message_id") or msg.metadata.get(
                    "message_id"
                )
                original_sender_id = msg.metadata.get("sender_id")
                chat_type = msg.metadata.get("chat_type", "group")

            # Build card elements using markdown for proper formatting
            card_elements: list[dict] = []

            # @mention prefix only when replying in group chats
            mention_prefix = ""
            if reply_to_message_id and original_sender_id and chat_type == "group":
                mention_prefix = f'<at id="{original_sender_id}"></at>'

            if content_with_mentions.strip():
                md_content = (
                    f"{mention_prefix}\n{content_with_mentions}"
                    if mention_prefix
                    else content_with_mentions
                )
                card_elements.extend(self._build_card_elements(md_content))
            elif mention_prefix:
                card_elements.append({"tag": "markdown", "content": mention_prefix})

            # Add images
            for img in images:
                card_elements.append(
                    {
                        "tag": "img",
                        "img_key": img["image_key"],
                        "alt": {"tag": "plain_text", "content": ""},
                    }
                )

            if not card_elements:
                card_elements.append({"tag": "markdown", "content": " "})

            # Build interactive card message
            card_payload = {
                "config": {"wide_screen_mode": True},
                "elements": card_elements,
            }
            card_content = json.dumps(card_payload, ensure_ascii=False)

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
                        .content(card_content)
                        .msg_type("interactive")
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
                        .msg_type("interactive")
                        .content(card_content)
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

    async def _download_and_save_image(self, image_key: str, message_id: str) -> str | None:
        """Download single Feishu image and save to local, return file path or None if failed."""
        try:
            logger.info(
                f"Downloading Feishu image with image_key: {image_key}, message_id: {message_id}"
            )
            image_bytes = await self._download_feishu_image(image_key, message_id)
            if not image_bytes:
                logger.warning(f"Could not download image for image_key: {image_key}")
                return None

            media_dir = get_data_path() / "received"
            media_dir.mkdir(parents=True, exist_ok=True)

            import uuid

            file_path = media_dir / f"feishu_{uuid.uuid4().hex[:16]}.png"
            file_path.write_bytes(image_bytes)

            logger.info(f"Feishu image saved to: {file_path}")
            return str(file_path)
        except Exception as e:
            logger.warning(f"Failed to download Feishu image {image_key}: {e}")
            import traceback

            logger.debug(f"Stack trace: {traceback.format_exc()}")
            return None

    async def _parse_message_content(
        self, message: Any, msg_type: str, message_id: str
    ) -> tuple[str, list[str]]:
        """Parse message content and extract media files."""
        content = ""
        media = []

        if msg_type == "text":
            try:
                content = json.loads(message.content).get("text", "")
            except json.JSONDecodeError:
                content = message.content or ""
        elif msg_type in ("image", "post"):
            content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")
            text_content = ""
            image_keys = []

            try:
                msg_content = json.loads(message.content)

                if msg_type == "image":
                    image_key = msg_content.get("image_key")
                    if image_key:
                        image_keys.append(image_key)
                elif msg_type == "post":
                    # Extract all images and text from post content
                    post_content = msg_content.get("content", [])
                    text_parts = []

                    for block in post_content:
                        for element in block:
                            tag = element.get("tag")
                            if tag == "img":
                                img_key = element.get("image_key")
                                if img_key:
                                    image_keys.append(img_key)
                            elif tag == "text":
                                text_parts.append(element.get("text", ""))

                    text_content = " ".join(text_parts).strip()
                    if text_content:
                        content = text_content

                # Download images in parallel
                if image_keys:
                    download_tasks = [
                        self._download_and_save_image(img_key, message_id) for img_key in image_keys
                    ]
                    results = await asyncio.gather(*download_tasks)
                    media = [path for path in results if path is not None]

            except Exception as e:
                logger.warning(f"Failed to process {msg_type} message: {e}")
        else:
            content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")

        return content, media

    async def _check_should_process(
        self, chat_type: str, chat_id: str, message: Any, is_mentioned: bool
    ) -> bool:
        """Check if message should be processed based on group/thread rules."""
        if chat_type != "group":
            return True

        chat_mode = await self._get_chat_mode(chat_id)

        # 普通群和话题群都根据 thread_require_mention 判断
        if self.config.thread_require_mention:
            # 模式1：所有消息都需要@才处理（普通群和话题群）
            if not is_mentioned:
                return False
        else:
            # 模式2：话题群仅首条消息不需要@，后续回复需要@
            if chat_mode == "thread":
                is_topic_starter = message.root_id == message.message_id or not message.root_id
                config = load_config()
                if not is_topic_starter and not is_mentioned and config.mode != BotMode.DEBUG:
                    return False
            # 普通群不需要@，直接处理

        return True

    def _save_user_name_cache(self, open_id: str, name: str) -> None:
        if open_id in self._user_name_cache:
            self._user_name_cache.pop(open_id)
        elif len(self._user_name_cache) >= self._MAX_USER_CACHE_SIZE:
            self._user_name_cache.popitem(last=False)
        self._user_name_cache[open_id] = name

    def _get_cached_user_name(self, open_id: str) -> str | None:
        if open_id not in self._user_name_cache:
            return None
        name = self._user_name_cache.pop(open_id)
        self._user_name_cache[open_id] = name
        return name

    def _save_chat_member_cache(self, chat_id: str, members: dict[str, str], last_error_at: float = 0) -> None:
        if chat_id in self._chat_member_cache:
            self._chat_member_cache.pop(chat_id)
        elif len(self._chat_member_cache) >= self._CHAT_MEMBER_CACHE_MAX_CHATS:
            self._chat_member_cache.popitem(last=False)

        ttl = self._CHAT_MEMBER_FETCH_COOLDOWN_SEC if last_error_at else self._CHAT_MEMBER_CACHE_TTL_SEC
        self._chat_member_cache[chat_id] = {
            "members": members,
            "expires_at": time.time() + ttl,
            "last_error_at": last_error_at,
        }

    async def _fetch_chat_members(self, chat_id: str) -> dict[str, str]:
        if not self._client or not GetChatMembersRequest:
            return {}

        members: dict[str, str] = {}
        page_token = ""

        for _ in range(self._CHAT_MEMBER_FETCH_MAX_PAGES):
            request_builder = (
                GetChatMembersRequest.builder()
                .chat_id(chat_id)
                .member_id_type("open_id")
                .page_size(self._CHAT_MEMBER_FETCH_PAGE_SIZE)
            )
            if page_token:
                request_builder = request_builder.page_token(page_token)
            request = request_builder.build()
            response = await self._client.im.v1.chat_members.aget(request)
            if not response.success():
                raise RuntimeError(
                    f"client.im.v1.chat_members.get failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}"
                )

            data = response.data
            items = getattr(data, "items", []) if data else []
            for item in items:
                member_id = getattr(item, "member_id", "")
                name = getattr(item, "name", "")
                if member_id and name:
                    members[member_id] = name

            has_more = bool(getattr(data, "has_more", False)) if data else False
            next_page_token = getattr(data, "page_token", "") if data else ""
            if not has_more or not next_page_token:
                break
            page_token = next_page_token

        return members

    async def _get_group_member_name(self, chat_id: str, open_id: str) -> str | None:
        now = time.time()
        entry = self._chat_member_cache.get(chat_id)

        if entry:
            self._chat_member_cache.move_to_end(chat_id)
            members = entry.get("members", {})
            if entry.get("expires_at", 0) > now:
                return members.get(open_id)
            if now - float(entry.get("last_error_at", 0) or 0) < self._CHAT_MEMBER_FETCH_COOLDOWN_SEC:
                return members.get(open_id)

        try:
            members = await self._fetch_chat_members(chat_id)
            self._save_chat_member_cache(chat_id, members)
            return members.get(open_id)
        except Exception as e:
            logger.warning(f"Failed to get chat members for {chat_id}: {e}")
            stale_members: dict[str, str] = {}
            if entry:
                stale_members = entry.get("members", {})
            self._save_chat_member_cache(chat_id, stale_members, last_error_at=now)
            return stale_members.get(open_id)

    async def _get_user_name(self, open_id: str, chat_id: str | None = None) -> str | None:
        """
        Get user name from Feishu API by open_id.
        Returns user name if found, None otherwise.
        Uses LRU cache to avoid memory issues.
        """
        cached_name = self._get_cached_user_name(open_id)
        if cached_name:
            return cached_name

        try:
            if GetUserRequest:
                user_request = (
                    GetUserRequest.builder().user_id(open_id).user_id_type("open_id").build()
                )
                user_response = self._client.contact.v3.user.get(user_request)
                if user_response.success() and user_response.data and user_response.data.user:
                    name = user_response.data.user.name
                    if name:
                        self._save_user_name_cache(open_id, name)
                        return name
        except Exception as e:
            logger.warning(f"Failed to get user name for {open_id}: {e}")

        if chat_id:
            member_name = await self._get_group_member_name(chat_id, open_id)
            if member_name:
                self._save_user_name_cache(open_id, member_name)
                return member_name

        return None

    async def _get_bot_name(self, open_id: str) -> str | None:
        """
        Get bot name by open_id.
        First tries to get from cache, then uses config bot_name or "Bot".
        Returns bot name if found, None otherwise.
        """
        # Check cache first
        if open_id in self._bot_name_cache:
            return self._bot_name_cache[open_id]

        # Use config bot_name if available, otherwise "Bot"
        bot_name = self.config.bot_name or "Bot"
        self._bot_name_cache[open_id] = bot_name
        return bot_name

    async def _batch_get_user_names(self, open_ids: list[str], chat_id: str | None = None) -> dict[str, str]:
        """
        Get user names from Feishu API by open_ids (fetches individually with LRU cache).
        Returns a dict mapping open_id to user name.
        """
        if not open_ids:
            return {}

        result = {}
        missing_ids = []
        for open_id in open_ids:
            cached_name = self._get_cached_user_name(open_id)
            if cached_name:
                result[open_id] = cached_name
            else:
                missing_ids.append(open_id)

        if not missing_ids:
            return result

        try:
            for open_id in missing_ids:
                try:
                    name = await self._get_user_name(open_id, chat_id=chat_id)
                    if name:
                        result[open_id] = name
                except Exception as e:
                    logger.warning(f"Failed to get user name for {open_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to get user names: {e}")

        return result

    async def _process_group_message_content(
        self, content: str, sender_id: str, chat_id: str | None = None
    ) -> tuple[str, str]:
        """
        Process group message content:
        1. Get sender name and prepend to content
        2. Replace @open_id mentions with @name mentions

        Returns:
            tuple of (processed_content, sender_name)
        """
        mentioned_open_ids = OPEN_ID_MENTION_PATTERN.findall(content)
        mentioned_open_ids = [mid[1:] for mid in mentioned_open_ids] if mentioned_open_ids else []

        all_ids_to_fetch = list({sender_id} | set(mentioned_open_ids))
        user_name_map = await self._batch_get_user_names(all_ids_to_fetch, chat_id=chat_id)

        processed_content = content
        if mentioned_open_ids:
            for open_id in mentioned_open_ids:
                name = user_name_map.get(open_id)
                if not name:
                    # If user name not found, try to get bot name
                    name = await self._get_bot_name(open_id)
                if name:
                    processed_content = processed_content.replace(f"@{open_id}", f"@{name}")

        sender_name = user_name_map.get(sender_id, "")
        if not sender_name:
            # If sender name not found, try to get bot name
            sender_name = await self._get_bot_name(sender_id) or ""
        if sender_name:
            processed_content = f"[{sender_name}]: {processed_content}"

        return processed_content, sender_name

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender
            message_id = message.message_id

            # 1. 消息去重
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None

            # 定期清理去重缓存（每100条清理一次，减少开销）
            if (
                len(self._processed_message_ids) % 100 == 0
                and len(self._processed_message_ids) > 1000
            ):
                while len(self._processed_message_ids) > 500:
                    self._processed_message_ids.popitem(last=False)

            # 2. 跳过机器人自身消息
            if sender.sender_type == "bot":
                return

            # 3. 基础信息提取
            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            if sender_id == "unknown":
                logger.warning(f"Received message from unknown sender: {message_id}")
                return

            chat_id = message.chat_id
            chat_type = message.chat_type  # "p2p" or "group"
            msg_type = message.message_type

            # 4. 解析消息内容和媒体
            content, media = await self._parse_message_content(message, msg_type, message_id)
            if not content:
                return

            # 5. 检查是否被@
            is_mentioned = False
            bot_name = self.config.bot_name
            if hasattr(message, "mentions") and message.mentions and bot_name:
                for mention in message.mentions:
                    if hasattr(mention, "name") and mention.name == bot_name:
                        is_mentioned = True
                        break

            # 6. 检查是否需要处理该消息
            should_process = await self._check_should_process(
                chat_type, chat_id, message, is_mentioned
            )

            # 7. 添加已读表情
            if should_process:
                config = load_config()
                if config.mode != BotMode.DEBUG:
                    await self._add_reaction(message_id, "MeMeMe")

            # 8. 处理@占位符：从 message.mentions 中直接获取 name 和 id
            mention_name_map = {}
            if hasattr(message, "mentions") and message.mentions:
                for idx, mention in enumerate(message.mentions):
                    placeholder = f"@_user_{idx + 1}"
                    if placeholder not in content:
                        continue
                    mention_name = getattr(mention, "name", "")
                    if bot_name and mention_name == bot_name:
                        content = content.replace(placeholder, "")
                        continue
                    if hasattr(mention, "id") and mention.id:
                        user_id = mention.id.open_id
                        if mention_name:
                            mention_name_map[user_id] = mention_name
                        content = content.replace(placeholder, f"@{user_id}")

            # 8.5 群聊场景：处理用户姓名
            user_name = ""
            if chat_type == "group":
                user_name = mention_name_map.get(sender_id, "")
                if not user_name:
                    user_name = await self._get_user_name(sender_id, chat_id=chat_id) or ""
                if user_name:
                    content = f"[{user_name}]: {content}"

                for user_id, name in mention_name_map.items():
                    if name and f"@{user_id}" in content:
                        content = content.replace(f"@{user_id}", f"@{name}")
                content = re.sub(r"\s{2,}", " ", content).strip()

            # 9. 构建会话ID（处理话题群）
            reply_to = chat_id if chat_type == "group" else sender_id
            final_chat_id = chat_id

            if chat_type == "group":
                chat_mode = await self._get_chat_mode(chat_id)
                if chat_mode == "thread":
                    # 话题首条消息设置root_id
                    if not message.root_id:
                        message.root_id = message.message_id
                    final_chat_id = f"{reply_to}#{message.root_id}"

            # 10. 转发到消息总线
            logger.info(f"Received message from Feishu: {content}")
            await self._handle_message(
                sender_id=sender_id,
                sender_name=user_name,
                chat_id=final_chat_id,
                content=content,
                media=media if media else None,
                need_reply=should_process,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "reply_to": reply_to,
                    "msg_type": msg_type,
                    "root_id": message.root_id,
                    "sender_id": sender_id,
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
