"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import base64
import io
import json
import re
import os
import threading
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Tuple
from urllib.parse import urlparse

import httpx
from loguru import logger

from vikingbot.utils import get_data_path
import httpx
from loguru import logger

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
from vikingbot.config.schema import FeishuChannelConfig

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
        GetImageRequest,
        GetMessageResourceRequest,
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
        import time

        token = await self._get_tenant_access_token()
        url = "https://open.feishu.cn/open-apis/im/v1/images"

        headers = {"Authorization": f"Bearer {token}"}

        # Use io.BytesIO properly
        files = {"image": ("image.png", io.BytesIO(image_data), "image/png")}
        data = {"image_type": "message"}

        logger.debug(f"Uploading image to {url} with image_data {image_data[:20]}...")

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
            logger.debug(f"Upload response status: {resp.status_code}")
            logger.debug(f"Upload response content: {resp.text}")
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
            raise Exception(
                f"Failed to download image: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
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

        logger.debug(f"Saved image to temp file: {temp_path}")
        return temp_path

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.exception("Feishu SDK not installed. Run: pip install lark-oapi")
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
            else:
                logger.debug(f"Added {emoji_type} reaction to message {message_id}")
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
        split = lambda l: [c.strip() for c in l.strip("|").split("|")]
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
            level = len(m.group(1))
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
                logger.debug(f"Processing Markdown image: {img_url[:100]}...")
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
                logger.debug(f"Processing Markdown image: {img_url[:100]}...")
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

        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        try:
            # Determine receive_id_type based on chat_id format
            # open_id starts with "ou_", chat_id starts with "oc_"
            if msg.session_key.chat_id.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"
            logger.info(f"[DEBUG] Feishu send() content: {msg.content[:300]}")

            # No images extracted from content, but content might still have Markdown images
            elements = await self._process_content_with_images(
                msg.content, receive_id_type, msg.session_key.chat_id
            )
            card = {
                "config": {"wide_screen_mode": True},
                "elements": elements,
            }
            content = json.dumps(card, ensure_ascii=False)

            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(msg.session_key.chat_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )

            response = self._client.im.v1.message.create(request)

            if not response.success():
                logger.exception(
                    f"Failed to send Feishu message: code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}"
                )
            else:
                logger.debug(f"Feishu message sent to {msg.session_key.chat_id}")

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

            # Add reaction to indicate "seen"
            await self._add_reaction(message_id, "MeMeMe")

            # Parse message content and media
            content = ""
            media = []

            # Log detailed message info for debugging
            logger.info(
                f"Received Feishu message: msg_type={msg_type}, content={message.content[:200]}"
            )

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
                                from pathlib import Path

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
                    else:
                        logger.warning(f"No image_key found in message content: {msg_content}")
                except Exception as e:
                    logger.warning(f"Failed to download Feishu image: {e}")
                    import traceback

                    logger.debug(f"Stack trace: {traceback.format_exc()}")
            else:
                content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")

            if not content:
                return

            # Forward to message bus
            reply_to = chat_id if chat_type == "group" else sender_id
            await self._handle_message(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                media=media if media else None,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                },
            )

        except Exception as e:
            logger.exception(f"Error processing Feishu message")
