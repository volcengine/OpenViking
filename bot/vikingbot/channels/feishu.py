"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import io
import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from vikingbot.config import load_config
from vikingbot.utils import detect_image_format, get_data_path

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
from vikingbot.config.schema import BotMode, Config, FeishuChannelConfig
from vikingbot.utils.image_format import sniff_image_format
from vikingbot.utils.session_paths import workspace_name

try:
    import lark_oapi as lark
    from lark_oapi.api.contact.v3 import (
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

# Message type display mapping
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}

# Pre-compiled regex patterns
OPEN_ID_MENTION_PATTERN = re.compile(r"@ou_[a-f0-9]+")
# 终止标签（写法对齐 @ 标签）：<stop></stop> / <stop/> 表示全局静默；
# <stop name="A"></stop> 表示仅对名为 A 的对象静默。兼容自闭合、任意属性、大小写。
STOP_TAG_PATTERN = re.compile(r"<\s*stop\b[^>]*?(?:/\s*>|>)", re.IGNORECASE)
# 从终止标签中提取 name 属性值（允许带引号或不带引号）。
STOP_NAME_ATTR_PATTERN = re.compile(r"""name\s*=\s*["']?\s*([^"'\s>]+)""", re.IGNORECASE)
# 模型输出里的「按显示名 @」占位：<at name=成员名></at>，用于替换成真实 open_id。
AT_NAME_MENTION_PATTERN = re.compile(r"<at\s+name=[\"']?([^\"'>\s]+)[\"']?\s*></at>")


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
    # 「成员 -> open_id」映射落在该机器人自己的记忆空间，便于人工查看与审计。
    # 目录结构：viking://user/{user}/memories/entities/feishu/{app_id}/{chat_id}.md
    # user 取该机器人自身的用户（见 _mention_bot_user_id：连接 user_id > 配置
    # admin_user_id > /health 解析），与 agent 记忆同命名空间，避免所有机器人共写到
    # ~（共享用户）下。仅用 config.ov_server.admin_user_id 不够：studio/api_key 模式下
    # 该字段是占位符 "default"，真实身份在按机器人下发的连接里。
    # open_id 按「应用(app_id) + 群(chat_id)」双重隔离：同一成员在不同应用、不同群
    # 拿到的 open_id 都可能不同，因此必须按群分片存储与缓存，否则会跨群串味。
    MENTION_MEMORY_ROOT_TEMPLATE = "viking://user/{user}/memories/entities/feishu/{app_id}/"
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

    def __init__(
        self,
        config: FeishuChannelConfig,
        bus: MessageBus,
        *,
        bot_config: Config | None = None,
        **kwargs,
    ):
        super().__init__(config, bus, **kwargs)
        self.config: FeishuChannelConfig = config
        self._bot_config = bot_config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # Ordered dedup cache
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tenant_access_token: str | None = None
        self._token_expire_time: float = 0
        self._chat_mode_cache: dict[str, str] = {}  # 缓存群类型：group(普通群)/thread(话题群)
        self._user_name_cache: OrderedDict[str, str] = OrderedDict()  # LRU缓存用户ID到姓名的映射
        self._chat_member_cache: OrderedDict[str, dict[str, Any]] = (
            OrderedDict()
        )  # chat_id -> {members, expires_at, last_error_at}
        # 「群(chat_id) -> {成员显示名(lower) -> open_id}」缓存。
        # 飞书 open_id 按应用隔离，且同一成员在不同群的 open_id 也可能不同；
        # 群成员接口又不返回机器人成员，只能从入站事件的 mentions 学习。
        # 因此缓存必须按 chat_id 分片，避免跨群串味。
        self._mention_name_cache: dict[str, dict[str, str]] = {}
        self._mention_cache_loaded: set[str] = set()  # 已从本地 JSON 加载过的 chat_id
        self._mention_memory_loaded: dict[str, float] = {}  # chat_id -> 记忆回填过期时间
        self._mention_memory_persisted: set[str] = set()  # 本进程内已成功镜像到 viking 的 chat_id
        self._chat_name_cache: dict[str, str] = {}  # chat_id -> 群名称
        # 本机器人自身的 OpenViking 用户名（记忆命名空间），首次解析后缓存。
        self._bot_user_id: str | None = None
        self._bot_user_source: str = "unresolved"  # 用户名来源，便于排查
        # 诊断日志：当前用户在 OpenViking 中的身份，首次打印后缓存避免重复登录。
        self._current_user_log: str | None = None
        self._MENTION_MEMORY_CACHE_TTL_SEC = 60  # 记忆回填缓存时长
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

        url = f"{self.config.domain}/open-apis/auth/v3/tenant_access_token/internal"
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

        if not image_data:
            raise ValueError("Feishu image upload requires a non-empty image")

        token = await self._get_tenant_access_token()
        url = f"{self.config.domain}/open-apis/im/v1/images"

        headers = {"Authorization": f"Bearer {token}"}
        data = {"image_type": "message"}

        async def post_image(client: httpx.AsyncClient, upload_data: bytes) -> httpx.Response:
            image_format = detect_image_format(upload_data)
            files = {
                "image": (
                    f"image.{image_format.extension}",
                    io.BytesIO(upload_data),
                    image_format.mime_type,
                )
            }
            return await client.post(url, headers=headers, data=data, files=files)

        async with httpx.AsyncClient(timeout=60.0) as client:
            upload_data = image_data
            resp = await post_image(client, upload_data)

            if resp.status_code == 400:
                normalized = self._normalize_image_for_feishu(image_data)
                if normalized != image_data:
                    logger.warning(
                        "Retrying Feishu image upload after re-encoding image "
                        f"({len(image_data)} -> {len(normalized)} bytes)"
                    )
                    upload_data = normalized
                    resp = await post_image(client, upload_data)

            if resp.is_error:
                raise Exception(
                    "Failed to upload image to Feishu: "
                    f"status={resp.status_code}, body={resp.text}, "
                    f"image_size={len(upload_data)}"
                )

            result = resp.json()
            if result.get("code") != 0:
                raise Exception(
                    f"Failed to upload image to Feishu: response={result}, "
                    f"image_size={len(upload_data)}"
                )
            return result["data"]["image_key"]

    @staticmethod
    def _normalize_image_for_feishu(image_data: bytes) -> bytes:
        """Re-encode an image to strip metadata that Feishu may reject."""
        try:
            from PIL import Image, ImageOps
        except ImportError:
            logger.warning("Pillow is not installed; cannot normalize image for Feishu upload")
            return image_data

        try:
            with Image.open(io.BytesIO(image_data)) as source:
                image = ImageOps.exif_transpose(source)
                if image.mode in ("RGBA", "LA") or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    image = Image.alpha_composite(background, rgba).convert("RGB")
                elif image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                else:
                    image = image.copy()

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
            normalized = output.getvalue()
            return normalized if normalized else image_data
        except Exception as exc:
            logger.warning(f"Failed to normalize image for Feishu upload: {exc}")
            return image_data

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
            chat_name = str(getattr(data, "name", "") or "")
            if chat_name:
                self._chat_name_cache[chat_id] = chat_name
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

    def _is_bot_mention(self, mention) -> bool:
        bot_id = getattr(self, "bot_open_id", None)
        if bot_id:
            return getattr(getattr(mention, "id", None), "open_id", None) == bot_id
        return bool(self.config.bot_name and getattr(mention, "name", "") == self.config.bot_name)

    def _reply_bot_mention_enabled(self) -> bool:
        """是否允许处理「机器人 @ 本机器人」的消息。

        默认 False：保留原逻辑，机器人消息一律跳过，避免机器人互刷死循环；
        仅当 bot.reply_bot_mention=true 且本条消息 @ 了本机器人时，才放行。
        """
        return bool(getattr(self._bot_config, "reply_bot_mention", False))

    def _openviking_connection(self) -> dict[str, Any] | None:
        """本通道对应的 OpenViking 连接（含真实 user_id / api_key）。

        基类无连接，返回 None；按机器人安装的子类（如 StudioFeishuChannel）会返回各自
        的连接，用于把记忆写到「本机器人自身」的用户空间，而不是回退到全局配置或共享用户。
        """
        return None

    def _stop_targets_self(self, content: str) -> bool:
        """入站内容里是否含「针对本 bot」的终止标签。

        - `<stop></stop>` / `<stop/>`：无 name，全局静默，命中即停止；
        - `<stop name="X"></stop>`：仅当 X 是本 bot 名称或本 bot open_id 时命中。
        """
        if not content:
            return False
        self_names = {
            str(getattr(self.config, "bot_name", "") or "").strip().lower(),
            str(getattr(self, "bot_open_id", "") or "").strip().lower(),
        }
        self_names.discard("")
        for match in STOP_TAG_PATTERN.finditer(content):
            name_match = STOP_NAME_ATTR_PATTERN.search(match.group(0))
            if not name_match:
                return True  # 无 name → 全局静默
            if name_match.group(1).strip().lower() in self_names:
                return True
        return False

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.exception(
                "Feishu SDK not installed. Install with: uv pip install lark-oapi>=1.0.0"
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
            .domain(self.config.domain)
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
            domain=self.config.domain,
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

    async def send(self, msg: OutboundMessage) -> bool:
        """Send a message through Feishu."""
        # 先调用基类处理通用动作
        if await super().send(msg):
            return False

        if not self._client:
            logger.warning("Feishu client not initialized")
            return False

        # Only send normal response messages, skip thinking/tool_call/etc.
        if not msg.is_normal_message:
            return False

        try:
            # logger.info(f"Sending message {msg}")
            # Determine receive_id_type based on chat_id format
            # open_id starts with "ou_", chat_id starts with "oc_"
            reply_to = msg.metadata.get("reply_to")
            if not isinstance(reply_to, str) or not reply_to:
                logger.warning(
                    f"Skipping Feishu message without reply_to metadata: session={msg.session_key}"
                )
                return False
            if reply_to.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"

            # Process images and get cleaned content
            cleaned_content, images = await self._extract_and_upload_images(msg.content, msg)

            # 终止标签：仅当回复除 <stop> 标签外没有其它内容（自我静默）时整条不发送；
            # 带 name 的标签表示「仅指定对象静默」，需随消息发出，供接收方判断是否自我静默。
            if STOP_TAG_PATTERN.search(cleaned_content):
                if not STOP_TAG_PATTERN.sub("", cleaned_content).strip() and not images:
                    logger.info(
                        f"[FEISHU_STOP] self-silence reply (only stop tag), skip sending: "
                        f"session={msg.session_key}"
                    )
                    return False

            # 把模型输出的 <at name=成员名></at> 换成本应用维度的真实 open_id
            content_with_mentions = await self._resolve_mention_names(cleaned_content, reply_to)

            # --- Build interactive card with markdown rendering ---
            original_sender_id = None
            chat_type = "group"
            reply_to_message_id = self._reply_to_message_id_from_metadata(msg.metadata)
            if msg.metadata:
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
                should_reply_in_thread = self._should_reply_in_thread(
                    msg.metadata, reply_to_message_id, msg.session_key.chat_id
                )

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

            return response.success()

        except Exception as e:
            logger.exception(f"Error sending Feishu message: {e}")
            return False

    @staticmethod
    def _reply_to_message_id_from_metadata(metadata: dict[str, Any] | None) -> str | None:
        """Resolve the Feishu message id to reply to, including scheduled thread delivery."""
        if not metadata:
            return None

        for key in ("reply_to_message_id", "message_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value

        root_id = metadata.get("root_id")
        if metadata.get("chat_mode") == "thread" and isinstance(root_id, str) and root_id:
            return root_id

        return None

    @staticmethod
    def _should_reply_in_thread(
        metadata: dict[str, Any] | None,
        reply_to_message_id: str,
        session_chat_id: str | None = None,
    ) -> bool:
        """Return whether Feishu reply API should create a topic-thread reply."""
        if not metadata:
            return False

        if metadata.get("chat_type") != "group":
            return False

        # Normal group quoted replies can also carry root_id/parent_id. Only topic groups
        # should use reply_in_thread, otherwise Feishu turns a normal quoted reply into a thread.
        is_thread_chat = metadata.get("chat_mode") == "thread" or (
            bool(session_chat_id) and "#" in session_chat_id
        )
        if not is_thread_chat:
            return False

        root_id = metadata.get("root_id")
        return bool(root_id and root_id != reply_to_message_id)

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
        elif msg_type == "interactive":
            content = message.content
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

    def _save_chat_member_cache(
        self, chat_id: str, members: dict[str, str], last_error_at: float = 0
    ) -> None:
        if chat_id in self._chat_member_cache:
            self._chat_member_cache.pop(chat_id)
        elif len(self._chat_member_cache) >= self._CHAT_MEMBER_CACHE_MAX_CHATS:
            self._chat_member_cache.popitem(last=False)

        ttl = (
            self._CHAT_MEMBER_FETCH_COOLDOWN_SEC
            if last_error_at
            else self._CHAT_MEMBER_CACHE_TTL_SEC
        )
        self._chat_member_cache[chat_id] = {
            "members": members,
            "expires_at": time.time() + ttl,
            "last_error_at": last_error_at,
        }

    def _mention_cache_file(self, chat_id: str) -> Path:
        """每个「应用 + 群」一个本地缓存文件，目录结构同记忆存储：feishu/{app_id}/chat_{chat_id}.json。"""
        return get_data_path() / "feishu" / self.config.app_id / f"chat_{chat_id}.json"

    async def _mention_bot_user_id(self) -> str:
        """该机器人自身的 OpenViking 用户名，用作记忆命名空间。

        取值顺序：
        1. 本通道携带的 OpenViking 连接里的 user_id——按机器人安装（studio）时连接由
           调用方下发，是最权威的身份来源，也是 agent 记忆实际使用的 user_id。
        2. 显式配置 config.ov_server.admin_user_id（非空且非 "default"）——trusted/root
           模式下由调用方指定，直接采用。
        3. 用连接/配置里的 user API key 调 /health 解析真实 user_id——api_key 模式下
           服务端按 key 识别用户，config 里的 admin_user_id 只是占位符 "default"，
           硬拼 viking://user/default/ 会被拒。
        4. 都拿不到时返回空串，由 _mention_memory_dir 回退到 viking://~/。
        """
        if self._bot_user_id is not None:
            return self._bot_user_id
        config = self._bot_config or load_config()
        ov_cfg = getattr(config, "ov_server", None)
        connection = self._openviking_connection() or {}
        conn_user = str(connection.get("user_id") or "").strip()
        if conn_user:
            self._bot_user_id = conn_user
            self._bot_user_source = "connection"
            return self._bot_user_id
        configured = str(getattr(ov_cfg, "admin_user_id", "") or "").strip()
        if configured and configured != "default":
            self._bot_user_id = configured
            self._bot_user_source = "config"
            return self._bot_user_id
        # /health 与 VikingClient 保持一致：server_url 取配置，api_key 优先用连接里的。
        server_url = str(
            getattr(ov_cfg, "server_url", "") or connection.get("server_url") or ""
        ).strip()
        api_key = str(
            connection.get("api_key") or getattr(ov_cfg, "api_key", "") or ""
        ).strip()
        resolved = ""
        if server_url and api_key:
            try:
                async with httpx.AsyncClient(timeout=2.0, trust_env=False) as http:
                    resp = await http.get(
                        f"{server_url.rstrip('/')}/health",
                        headers={"X-API-Key": api_key},
                    )
                if resp.status_code < 300:
                    resolved = str((resp.json() or {}).get("user_id") or "").strip()
            except Exception as e:
                logger.warning(f"[FEISHU_MENTION] resolve bot user via /health failed: {e}")
        if resolved:
            self._bot_user_source = "health"
            logger.info(f"[FEISHU_MENTION] resolved bot user id from /health: {resolved}")
        else:
            self._bot_user_source = "unresolved"
            logger.warning(
                "[FEISHU_MENTION] bot user id unresolved; memory will fall back to viking://~/"
            )
        self._bot_user_id = resolved
        return self._bot_user_id

    async def _log_current_user(self) -> None:
        """诊断用：收到消息时打印本机器人在 OpenViking 中的真实身份。

        直接复用 _mention_bot_user_id 的解析结果，避免打印 VikingClient 的中间态
        （api_key 模式下 client.user_id=None / admin_user_id=default）而误导排查。
        """
        if self._current_user_log is not None:
            logger.info(f"[FEISHU_USER] {self._current_user_log}")
            return
        config = self._bot_config or load_config()
        ov_cfg = getattr(config, "ov_server", None)
        connection = self._openviking_connection() or {}
        bot_user = await self._mention_bot_user_id()
        api_key = connection.get("api_key") or getattr(ov_cfg, "api_key", "")
        self._current_user_log = " ".join(
            [
                f"app_id={self.config.app_id}",
                f"conn.user_id={connection.get('user_id') or '<none>'}",
                f"cfg.admin_user_id={getattr(ov_cfg, 'admin_user_id', '') or '<empty>'}",
                f"server_url={getattr(ov_cfg, 'server_url', '') or connection.get('server_url') or ''}",
                f"api_key={'<set>' if api_key else '<empty>'}",
                f"resolved_bot_user={bot_user or '<unresolved>'}",
                f"source={self._bot_user_source}",
            ]
        )
        logger.info(f"[FEISHU_USER] {self._current_user_log}")

    def _mention_memory_dir(self, admin_user_id: str) -> str:
        """机器人自身用户下的记忆目录；用户名缺失或为 dev 占位符时回退到 ~ 并告警。"""
        user = str(admin_user_id or "").strip()
        if not user or user == "default":
            logger.warning(
                f"[FEISHU_MENTION] admin_user_id={user or '<empty>'}, "
                "fallback to viking://~/ for memory"
            )
            return f"viking://~/memories/entities/feishu/{self.config.app_id}/"
        return self.MENTION_MEMORY_ROOT_TEMPLATE.format(
            user=user, app_id=self.config.app_id
        )

    def _mention_memory_uri(self, admin_user_id: str, chat_id: str) -> str:
        """每个群聊一个记忆文件，文件名用 chat_id，便于按群查看与审计。"""
        return f"{self._mention_memory_dir(admin_user_id)}{chat_id}.md"

    async def _get_chat_name(self, chat_id: str) -> str:
        """获取并缓存群名称（复用 _get_chat_mode 的 im.v1.chat.aget 响应）。"""
        if chat_id in self._chat_name_cache:
            return self._chat_name_cache[chat_id]
        if chat_id.startswith("oc_"):
            await self._get_chat_mode(chat_id)
        return self._chat_name_cache.get(chat_id, "")

    def _load_mention_name_cache(self, chat_id: str | None = None) -> None:
        """惰性加载该群本地持久化的 {成员名 -> open_id} 缓存（跨重启不丢）。"""
        if not chat_id or chat_id in self._mention_cache_loaded:
            return
        self._mention_cache_loaded.add(chat_id)
        try:
            path = self._mention_cache_file(chat_id)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                bucket = self._mention_name_cache.setdefault(chat_id, {})
                loaded = 0
                if isinstance(data, dict):
                    for name, open_id in data.items():
                        # 只接受扁平 {name: open_id}；非字符串值（历史嵌套格式）直接忽略。
                        if isinstance(name, str) and isinstance(open_id, str) and name and open_id:
                            bucket[name.lower()] = open_id
                            loaded += 1
                logger.debug(f"[FEISHU_MENTION] loaded {loaded} cached mention ids from {path}")
        except Exception as e:
            logger.warning(f"[FEISHU_MENTION] load mention cache failed: {e}")

    def _save_mention_name_cache(self, chat_id: str | None = None) -> None:
        if not chat_id:
            return
        try:
            path = self._mention_cache_file(chat_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._mention_name_cache.get(chat_id, {}), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[FEISHU_MENTION] save mention cache failed: {e}")

    @staticmethod
    def _render_chat_mention_memory(
        chat_id: str, chat_name: str, mapping: dict[str, str]
    ) -> str:
        """群记忆文件内容：纯文本 key-value（非 JSON），一个群一个文件。"""
        title = f"{chat_name} ({chat_id})" if chat_name else chat_id
        lines = [
            f"# 飞书成员提及映射 - {title}",
            "",
            "本文件由机器人运行时自动维护，记录本飞书应用+本群维度下「成员显示名 -> open_id」的映射，",
            "用于把回复里的 `<at name=成员名></at>` 解析成真实可通知的 @。",
            "",
            "注意：open_id 与飞书应用、群都绑定，不同应用/群可能不同；",
            "该文件仅供系统解析使用，严禁在回复正文、解释或举例中输出 ou_ 开头的 id。",
            "",
            f"chat_id: {chat_id}",
            f"chat_name: {chat_name}",
            "",
        ]
        for name in sorted(mapping):
            lines.append(f"name: {name}")
            lines.append("member_id_type: open_id")
            lines.append(f"member_id: {mapping[name]}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _parse_chat_mention_memory(content: str) -> dict[str, str]:
        """解析群记忆文件，返回 {成员名(lower): member_id}。"""
        mapping: dict[str, str] = {}
        current_name = ""
        for line in content.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            key = key.strip().lower()
            value = value.strip()
            if key == "name":
                current_name = value
            elif key == "member_id" and current_name and value:
                mapping[current_name.lower()] = value
                current_name = ""
        return mapping

    async def _persist_chat_mention_memory(self, chat_id: str) -> bool:
        """把该群的「成员名 -> open_id」全量写入一个记忆文件；失败只告警，不影响收发。

        返回是否写入成功，供调用方决定是否需要重试镜像。
        """
        if not chat_id or not chat_id.startswith("oc_"):
            return False
        mapping = self._mention_name_cache.get(chat_id) or {}
        if not mapping:
            return False
        try:
            from vikingbot.openviking_mount.ov_server import VikingClient

            config = self._bot_config or load_config()
            chat_name = await self._get_chat_name(chat_id)
            # 记忆写在机器人自身用户名下（与 agent 记忆同命名空间）。
            bot_user_id = await self._mention_bot_user_id()
            root_uri = self._mention_memory_dir(bot_user_id)
            uri = self._mention_memory_uri(bot_user_id, chat_id)
            client = await VikingClient.create(
                connection=self._openviking_connection(), config=config
            )
            try:
                logger.debug(f"[FEISHU_MENTION] mkdir {root_uri}")
                # batch_write 不会自动建父目录，需先确保目录存在（重复创建无害）。
                # 服务端 mkdir 会递归补齐父目录；失败必须可见，否则 batch_write 也会连带失败。
                try:
                    await client.mkdir(root_uri)
                except Exception as e:
                    logger.warning(
                        f"[FEISHU_MENTION] mkdir {root_uri} failed: {e}"
                    )
                # memories 不支持二进制写入，必须走纯文本 content（不能用 content_base64）。
                await client.batch_write(
                    root_uri=root_uri,
                    operations=[
                        {
                            "uri": uri,
                            "content": self._render_chat_mention_memory(
                                chat_id, chat_name, mapping
                            ),
                            "mode": "upsert",
                        }
                    ],
                    wait=False,
                )
                logger.info(
                    f"[FEISHU_MENTION] persisted {len(mapping)} members to {uri}"
                )
            finally:
                await client.close()
            return True
        except Exception as e:
            logger.warning(f"[FEISHU_MENTION] persist chat memory failed: {e}")
            return False

    async def _load_mention_cache_from_memory(self, chat_id: str | None = None) -> None:
        """从该群的记忆文件回填「成员名 -> open_id」缓存（带 TTL，避免频繁读取）。"""
        if not chat_id or not chat_id.startswith("oc_"):
            return
        if self._mention_memory_loaded.get(chat_id, 0) > time.time():
            return
        self._mention_memory_loaded[chat_id] = time.time() + self._MENTION_MEMORY_CACHE_TTL_SEC
        try:
            from vikingbot.openviking_mount.ov_server import VikingClient

            config = self._bot_config or load_config()
            uri = self._mention_memory_uri(await self._mention_bot_user_id(), chat_id)
            client = await VikingClient.create(
                connection=self._openviking_connection(), config=config
            )
            try:
                content = await client.read_content(uri=uri, level="read")
            finally:
                await client.close()
            if not content:
                return
            bucket = self._mention_name_cache.setdefault(chat_id, {})
            added = 0
            for name, member_id in self._parse_chat_mention_memory(content).items():
                # 本地 JSON 中已有的值优先，记忆文件只补缺。
                if name not in bucket:
                    bucket[name] = member_id
                    added += 1
            logger.debug(
                f"[FEISHU_MENTION] memory cache loaded for {chat_id}: +{added} names "
                f"(total {len(bucket)})"
            )
        except Exception as e:
            # 回填失败不阻塞收发，并允许下次重试。
            self._mention_memory_loaded.pop(chat_id, None)
            logger.warning(f"[FEISHU_MENTION] load mention memory failed: {e}")

    async def _learn_mentions(self, mentions: Any, chat_id: str | None = None) -> None:
        """从入站事件的 mentions 学习「成员名 -> 本应用+本群维度 open_id」。

        飞书 open_id 按应用隔离，且同一成员在不同群的 open_id 也可能不同；
        群成员接口又不返回机器人成员。因此要 @ 别的机器人，只能从本应用收到的
        mentions[].name + mentions[].id.open_id 反推映射，并按群分片缓存/落盘。
        """
        if not chat_id or not chat_id.startswith("oc_"):
            return
        self._load_mention_name_cache(chat_id)
        bucket = self._mention_name_cache.setdefault(chat_id, {})
        changed = False
        for mention in mentions or []:
            name = str(getattr(mention, "name", "") or "").strip()
            mention_id = getattr(mention, "id", None)
            open_id = str(getattr(mention_id, "open_id", "") or "") if mention_id else ""
            if not name or not open_id:
                continue
            key = name.lower()
            if bucket.get(key) != open_id:
                bucket[key] = open_id
                changed = True
                logger.debug(f"[FEISHU_MENTION] learned {name} -> {open_id} in {chat_id}")
        if changed:
            self._save_mention_name_cache(chat_id)
        # 映射有变化时全量重写该群记忆文件；此外每个进程内每个群至少镜像一次，
        # 避免本地 JSON 已预热（changed=False）时 viking 记忆文件从未生成。
        if not changed and chat_id in self._mention_memory_persisted:
            logger.debug(f"[FEISHU_MENTION] mirror already persisted for {chat_id}, skip")
            return
        if not bucket:
            return
        if await self._persist_chat_mention_memory(chat_id):
            self._mention_memory_persisted.add(chat_id)

    async def _resolve_mention_names(self, content: str, chat_id: str | None = None) -> str:
        """把模型输出的 <at name=成员名></at> 换成本应用+本群维度的 <at id=open_id></at>。"""
        pattern = AT_NAME_MENTION_PATTERN
        if not pattern.search(content):
            return content
        self._load_mention_name_cache(chat_id)
        if chat_id and chat_id.startswith("oc_"):
            try:
                await asyncio.wait_for(
                    self._load_mention_cache_from_memory(chat_id), timeout=5
                )
            except Exception as e:
                logger.warning(f"[FEISHU_MENTION] memory cache load skipped: {e}")

        def _substitute(text: str) -> str:
            bucket = self._mention_name_cache.get(chat_id or "", {})

            def _replace(match: re.Match[str]) -> str:
                open_id = bucket.get(match.group(1).strip().lower())
                if not open_id:
                    return match.group(0)
                return f'<at id="{open_id}"></at>'

            return pattern.sub(_replace, text)

        resolved = _substitute(content)
        if not pattern.search(resolved):
            return resolved

        # 缓存缺失时兜底拉一次群成员（仅能补全人类成员），再重试
        if chat_id and chat_id.startswith("oc_"):
            try:
                members = await self._get_chat_members_cached(chat_id)
                bucket = self._mention_name_cache.setdefault(chat_id, {})
                for open_id, name in members.items():
                    if name:
                        bucket.setdefault(str(name).strip().lower(), open_id)
            except Exception as e:
                logger.warning(f"[FEISHU_MENTION] fallback fetch chat members failed: {e}")
            resolved = _substitute(resolved)

        unresolved = [match.group(1) for match in pattern.finditer(resolved)]
        if unresolved:
            logger.warning(
                f"[FEISHU_MENTION] unresolved member names: {unresolved}; "
                f"known names={sorted(self._mention_name_cache.get(chat_id or '', {}))}"
            )
        return resolved

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
            logger.debug(
                f"[FEISHU_MENTION] raw chat members for {chat_id}: "
                f"count={len(items)} page_token={bool(page_token)}"
            )
            for item in items:
                member_id = getattr(item, "member_id", "")
                name = getattr(item, "name", "")
                logger.debug(
                    f"[FEISHU_MENTION] raw member: id={member_id} name={name!r} "
                    f"type={getattr(item, 'member_type', '')!r}"
                )
                if member_id and name:
                    members[member_id] = name

            has_more = bool(getattr(data, "has_more", False)) if data else False
            next_page_token = getattr(data, "page_token", "") if data else ""
            if not has_more or not next_page_token:
                break
            page_token = next_page_token

        return members

    async def _get_chat_members_cached(self, chat_id: str) -> dict[str, str]:
        """带缓存与失败冷却的群成员拉取（注意：接口只返回人类成员）。"""
        now = time.time()
        entry = self._chat_member_cache.get(chat_id)

        if entry:
            self._chat_member_cache.move_to_end(chat_id)
            members = entry.get("members", {})
            if entry.get("expires_at", 0) > now:
                return members
            if (
                now - float(entry.get("last_error_at", 0) or 0)
                < self._CHAT_MEMBER_FETCH_COOLDOWN_SEC
            ):
                return members

        try:
            members = await self._fetch_chat_members(chat_id)
            self._save_chat_member_cache(chat_id, members)
            return members
        except Exception as e:
            logger.warning(f"Failed to get chat members for {chat_id}: {e}")
            stale_members: dict[str, str] = entry.get("members", {}) if entry else {}
            self._save_chat_member_cache(chat_id, stale_members, last_error_at=now)
            return stale_members

    async def _get_group_member_name(self, chat_id: str, open_id: str) -> str | None:
        members = await self._get_chat_members_cached(chat_id)
        return members.get(open_id)

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

            # 诊断：收到消息时先打印本机器人在 OpenViking 中的当前用户身份。
            await self._log_current_user()

            # 定期清理去重缓存（每100条清理一次，减少开销）
            if (
                len(self._processed_message_ids) % 100 == 0
                and len(self._processed_message_ids) > 1000
            ):
                while len(self._processed_message_ids) > 500:
                    self._processed_message_ids.popitem(last=False)

            logger.debug(
                f"[FEISHU_INBOUND] sender_type={getattr(sender, 'sender_type', None)} "
                f"sender_id={getattr(getattr(sender, 'sender_id', None), 'open_id', None)} "
                f"msg_type={message.message_type} chat_type={message.chat_type} "
                f"mentions={[{'name': getattr(m, 'name', None), 'open_id': getattr(getattr(m, 'id', None), 'open_id', None)} for m in (getattr(message, 'mentions', None) or [])]}"
            )

            # 2. 检查是否被@（机器人消息只有被 @ 时才处理，避免机器人互刷死循环）
            is_mentioned = False
            if hasattr(message, "mentions") and message.mentions:
                for mention in message.mentions:
                    if self._is_bot_mention(mention):
                        is_mentioned = True
                        break

            # 3. 机器人消息处理开关（保留原逻辑：默认机器人消息直接跳过）
            #    默认 bot.reply_bot_mention=false 时，机器人消息一律 return；
            #    仅当启用开关且本条消息 @ 了本机器人时，才继续处理（允许机器人互 @）。
            if sender.sender_type == "bot":
                if not (self._reply_bot_mention_enabled() and is_mentioned):
                    logger.info(
                        f"[FEISHU_INBOUND] skip bot message {message_id}, "
                        f"bot.reply_bot_mention: {str(self._reply_bot_mention_enabled()).lower()}, "
                        f"is_mentioned: {str(is_mentioned).lower()}"
                    )
                    return

            # 从入站 mentions 学习「成员名 -> 本应用维度 open_id」，供发送侧 @ 解析使用
            await self._learn_mentions(
                getattr(message, "mentions", None), getattr(message, "chat_id", None)
            )

            # 4. 基础信息提取
            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            if sender_id == "unknown":
                logger.warning(f"Received message from unknown sender: {message_id}")
                return

            chat_id = message.chat_id
            chat_type = message.chat_type  # "p2p" or "group"
            msg_type = message.message_type

            # 5. 解析消息内容和媒体
            content, media = await self._parse_message_content(message, msg_type, message_id)
            if not content:
                return

            # 5.5 终止标签：入站消息若含针对本 bot（或无 name 的全局）<stop>，直接不处理、不回复。
            if self._stop_targets_self(content):
                logger.info(
                    f"[FEISHU_STOP] inbound stop tag for self, skip message: {message_id}"
                )
                return

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
                    if self._is_bot_mention(mention):
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
            chat_mode = None

            if chat_type == "group":
                chat_mode = await self._get_chat_mode(chat_id)
                if chat_mode == "thread":
                    # 话题首条消息设置root_id
                    if not message.root_id:
                        message.root_id = message.message_id
                    final_chat_id = f"{reply_to}#{message.root_id}"

            topic_title = ""
            if chat_mode == "thread" and message.root_id == message_id and msg_type == "post":
                try:
                    topic_title = json.loads(message.content).get("title", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

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
                    "chat_mode": chat_mode,
                    "topic_title": topic_title if isinstance(topic_title, str) else "",
                    "sender_id": sender_id,
                },
            )

        except Exception:
            logger.exception("Error processing Feishu message")

    async def _download_viking_image(self, uri: str, msg: OutboundMessage | None) -> bytes:
        """Read an image with the same sender scope used by OpenViking tools."""
        from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
        from vikingbot.openviking_mount.ov_server import VikingClient

        sender_id = msg.metadata.get("sender_id") if msg else None
        if not isinstance(sender_id, str) or not sender_id.strip():
            raise ValueError("OpenViking image delivery requires the original sender identity")

        config = self._bot_config or load_config()
        client = await VikingClient.create(
            workspace_name(msg.session_key, config.sandbox.mode, portable=False),
            actor_peer_id=sender_id,
            config=config,
        )
        try:
            stat = await client.stat(uri)
            size = stat.get("size")
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or not 0 < size <= MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
            ):
                raise ValueError(
                    "OpenViking image size is unavailable or exceeds the delivery limit"
                )
            data = await client.download_bytes(uri)
            if len(data) > size or sniff_image_format(data) is None:
                raise ValueError("OpenViking image changed size or has an unsupported format")
            return data
        finally:
            await client.close()

    async def _extract_and_upload_images(
        self, content: str, msg: OutboundMessage | None = None
    ) -> tuple[str, list[dict]]:
        """Upload explicit images, preserving URI citations and Markdown examples."""
        # Viking URIs also appear in listings and citations: only image Markdown
        # requests delivery. Keep bare send:// support for the image generation tool.
        # Consume code spans/blocks first so syntax examples never send an image.
        path = r"(?:[^\s()<>]|\([^()\n]*\))"
        uri = rf"send://{path}+?\.(?:png|jpe?g|gif|bmp|webp)(?:[?#][^\s<>)]*)?"
        image_uri = rf"(?:<(?:send|viking)://[^<>\n]+>|(?:send|viking)://{path}+?)"
        pattern = re.compile(
            r"(?P<literal>^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\n]*(?:\n|$)"
            r"[\s\S]*?(?:^[ \t]{0,3}(?P=fence)[`~]*[ \t]*(?:\n|$)|\Z)"
            r"|(?P<ticks>`+)(?!`)[\s\S]*?(?<!`)(?P=ticks)(?!`)"
            r"|\\.)"
            rf"|!\[[^\]]*\]\(\s*(?P<image>{image_uri})"
            r"(?:\s+[\"'][^\n]*?[\"'])?\s*\)"
            rf"|(?P<bare>{uri})(?=$|[\s<>`\[\](){{}},;.!?。，；！：])",
            re.IGNORECASE | re.MULTILINE,
        )
        images: list[dict] = []
        replacements: dict[str, str] = {}
        parts: list[str] = []
        last_end = 0
        for match in pattern.finditer(content):
            if match.group("literal") is not None:
                continue
            img_url = match.group("image") or match.group("bare")
            img_url = img_url.removeprefix("<").removesuffix(">")
            scheme, location = img_url.split("://", 1)
            img_url = f"{scheme.lower()}://{location}"
            if img_url not in replacements:
                try:
                    if img_url.lower().startswith("viking://"):
                        result = await asyncio.wait_for(
                            self._download_viking_image(img_url, msg), timeout=60.0
                        )
                    else:
                        is_content, result = await self._parse_data_uri(img_url)
                        if is_content or not isinstance(result, bytes):
                            raise ValueError("Image reference did not resolve to image bytes")
                    image_key = await self._upload_image_to_feishu(result)
                    images.append({"image_key": image_key})
                    replacements[img_url] = ""
                except Exception as exc:
                    logger.warning(f"Failed to send image {img_url[:100]}: {exc}")
                    replacements[img_url] = "[图片发送失败]"
            parts.append(content[last_end : match.start()])
            parts.append(replacements[img_url])
            last_end = match.end()
        parts.append(content[last_end:])
        return "".join(parts).strip(), images
