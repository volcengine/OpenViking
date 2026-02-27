"""Base channel interface for chat platforms."""

import base64
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Tuple

import httpx
from loguru import logger

from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import SessionKey, BaseChannelConfig
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


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the vikingbot message bus.
    """

    name: str = "base"

    def __init__(
        self, config: BaseChannelConfig, bus: MessageBus, workspace_path: Path | None = None
    ):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
            channel_id: Unique identifier for this channel (for multi-channel support).
            workspace_path: Path to the user's workspace directory.
        """
        self.config = config
        self.bus = bus
        self._running = False
        self.channel_type = config.type
        self.channel_id = config.channel_id()
        self.workspace_path = workspace_path

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.
        """
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """
        Check if a sender is allowed to use this bot.

        Args:
            sender_id: The sender's identifier.

        Returns:
            True if allowed, False otherwise.
        """
        allow_list = getattr(self.config, "allow_from", [])

        # If no allow list, allow everyone
        if not allow_list:
            return True

        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                f"Access denied for sender {sender_id} on channel {self.name}. "
                f"Add them to allowFrom list in config to grant access."
            )
            return

        msg = InboundMessage(
            session_key=SessionKey(
                type=str(self.channel_type.value), channel_id=self.channel_id, chat_id=chat_id
            ),
            sender_id=str(sender_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
        )

        await self.bus.publish_inbound(msg)

    async def _parse_data_uri(self, data_uri: str) -> Tuple[bool, Any]:
        """
        Parse data URI. Returns (is_content, result) where:
        - is_content = False, result = bytes (image data)
        - is_content = True, result = str (markdown content)
        """
        if data_uri.startswith("data:"):
            # Split header and data
            header, data = data_uri.split(",", 1)
            # Decode base64
            if ";base64" in header:
                return False, base64.b64decode(data)
            else:
                return False, data.encode("utf-8")
        # If it's a URL, download it
        elif data_uri.startswith("http://") or data_uri.startswith("https://"):
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(data_uri)
                resp.raise_for_status()
                content = resp.content

                # Check if it's HTML or image
                is_html, result = self._process_html_content(content, data_uri)
                if is_html:
                    return True, result

                # It's an image - validate
                content_type = resp.headers.get("content-type", "")
                if not content_type.startswith("image/") and not self._is_image_data(content):
                    logger.warning(
                        f"URL returned non-image content: {data_uri}, "
                        f"Content-Type: {content_type}, "
                        f"First 50 bytes: {content[:50]}"
                    )
                    # Try to process as HTML anyway
                    is_html, result = self._process_html_content(content, data_uri)
                    if is_html:
                        return True, result
                    raise ValueError(
                        f"URL did not return an image or HTML: {data_uri}. "
                        f"Content-Type: {content_type}"
                    )

                return False, content
        elif data_uri.startswith("send://"):
            path_obj = get_data_path() / "images" / data_uri.split("send://", 1)[1]
            return False, path_obj.read_bytes()
        else:
            # Try to resolve as local file path
            candidate_paths = []

            # 1. Check if it's already an absolute path that exists
            path_obj = Path(data_uri)
            if path_obj.is_absolute():
                candidate_paths.append(path_obj)

            return False, path_obj.read_bytes()

    def _extract_images(self, content: str) -> tuple[list[str], str]:
        """Extract image data URIs, URLs and local paths from content (support Markdown image syntax)."""
        images = []
        # 新增 Markdown 图片语法匹配 + 原有 Data URI/网络URL 匹配
        # 匹配规则：
        # 1. ![xxx](路径) 中的路径
        # 2. data: 开头的 Data URI
        # 3. http/https 开头的网络链接
        pattern = r"!\[.*?\]\(([^)]+)\)|(data:[^,]+,[^\s]+|https?://[^\s]+)"
        parts = []
        last_end = 0
        trailing_punctuation = ")].,!?:;'\">}`"

        for m in re.finditer(pattern, content):
            before = content[last_end : m.start()]
            if before.strip():
                parts.append(before)

            # 优先取 Markdown 图片里的路径（第一个分组），再取 Data URI/URL（第二个分组）
            uri = m.group(1) if m.group(1) else m.group(2)
            if not uri:
                last_end = m.end()
                continue

            # 清理末尾标点
            while uri and uri[-1] in trailing_punctuation:
                uri = uri[:-1]

            images.append(uri)
            last_end = m.end()

        remaining = content[last_end:]
        if remaining.strip():
            parts.append(remaining)

        return images, "\n".join(parts)

    def _is_image_data(self, data: bytes) -> bool:
        """Check if bytes represent a valid image by magic numbers."""
        # Common image magic numbers
        image_magics = [
            b"\xff\xd8\xff",  # JPEG
            b"\x89PNG\r\n\x1a\n",  # PNG
            b"GIF87a",  # GIF87
            b"GIF89a",  # GIF89
            b"RIFF" and b"WEBP",  # WebP (simplified check)
            b"<svg",  # SVG (text-based)
            b"<?xml",  # SVG with XML header
            b"BM",  # BMP
            b"II*\x00",  # TIFF (little-endian)
            b"MM\x00*",  # TIFF (big-endian)
        ]

        for magic in image_magics:
            if data.startswith(magic):
                return True

        # Special check for WebP (more precise)
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return True

        return False

    def _html_to_markdown(self, html_content: str, url: str = "") -> str:
        """Convert HTML content to Markdown, extracting main article content."""
        if not HTML_PROCESSING_AVAILABLE:
            logger.warning("HTML processing libraries not available, returning raw link")
            return url if url else html_content[:500]

        try:
            # First try: Use readability to extract main content
            doc = Document(html_content)
            main_html = doc.summary()
            title = doc.title()

            # Then convert to Markdown
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = False
            h.body_width = 0  # No line wrapping
            h.unicode_snob = True

            markdown = h.handle(main_html)

            # Combine title + content
            result = ""
            if title:
                result += f"# {title}\n\n"
            result += markdown

            # Add source link if available
            if url:
                result += f"\n\n---\n\nSource: {url}"

            return result.strip()

        except Exception as e:
            logger.warning(f"HTML to Markdown conversion failed: {e}")
            # Fallback: just return a link if we have it
            return url if url else html_content[:1000]

    def _process_html_content(self, data: bytes, url: str = "") -> Tuple[bool, Any]:
        """
        Process content that might be HTML.
        Returns (is_html, result) where result is either:
        - (bytes) if it's an image
        - (str) markdown if it's HTML content
        """
        # First check if it's an image
        if self._is_image_data(data):
            return False, data

        # Check if it's HTML
        try:
            text_content = data.decode("utf-8", errors="ignore")
            if "<!doctype html" in text_content.lower() or "<html" in text_content.lower():
                # It's HTML - convert to Markdown
                markdown = self._html_to_markdown(text_content, url)
                return True, markdown
        except UnicodeDecodeError:
            pass

        # Not HTML or image - return as-is
        return False, data

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
