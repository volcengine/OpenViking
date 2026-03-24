"""Image generation tool temporarily disabled because it depends on LiteLLM."""

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from vikingbot.agent.tools.base import Tool
from vikingbot.bus.events import OutboundMessage

if TYPE_CHECKING:
    from vikingbot.agent.tools.base import ToolContext

LITELLM_DISABLED_MESSAGE = "Image generation is temporarily unavailable because LiteLLM has been disabled for security reasons"


class ImageGenerationTool(Tool):
    """Stub image tool kept for compatibility while LiteLLM is disabled."""

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return LITELLM_DISABLED_MESSAGE

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def __init__(
        self,
        gen_image_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self.gen_image_model = gen_image_model
        self.api_key = api_key
        self.api_base = api_base
        self._send_callback = send_callback

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    async def execute(self, tool_context: "ToolContext", **kwargs: Any) -> str:
        return f"Error: {LITELLM_DISABLED_MESSAGE}"
