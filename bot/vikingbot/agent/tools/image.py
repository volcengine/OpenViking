"""Image generation tool using LiteLLM's image generation capabilities."""

import base64
import logging
import mimetypes
import uuid
from io import BytesIO
from typing import Any, Callable, Awaitable
from pathlib import Path
import httpx
import litellm

from vikingbot.agent.tools.base import Tool
from vikingbot.bus.events import OutboundMessage
from vikingbot.utils import get_data_path


class ImageGenerationTool(Tool):
    """Generate images from text descriptions or edit existing images using the configured image model."""

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return "Generate images from scratch, edit existing images, or create variations. For edit/variation mode, provide a base_image (base64 or URL)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["generate", "edit", "variation"],
                    "description": "Mode: 'generate' (from scratch), 'edit' (edit existing), or 'variation' (create variations)",
                    "default": "generate",
                },
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate or edit (required for generate and edit modes)",
                },
                "base_image": {
                    "type": "string",
                    "description": "Base image for edit/variation mode: base64 data URI or image file path (required for edit and variation modes)",
                },
                "mask": {
                    "type": "string",
                    "description": "Mask image for edit mode: base64 data URI or image URL (optional, transparent areas indicate where to edit)",
                },
                "size": {
                    "type": "string",
                    "enum": ["1920x1920"],
                    "description": "Image size (default: 1920x1920)",
                    "default": "1920x1920",
                },
                "quality": {
                    "type": "string",
                    "enum": ["standard", "hd"],
                    "description": "Image quality (default: standard)",
                    "default": "standard",
                },
                "style": {
                    "type": "string",
                    "enum": ["vivid", "natural"],
                    "description": "Image style (DALL-E 3 only, default: vivid)",
                    "default": "vivid",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of images to generate (1-4)",
                    "minimum": 1,
                    "maximum": 4,
                    "default": 1,
                },
                "send_to_user": {
                    "type": "boolean",
                    "description": "Whether to send the generated image directly to the user (default: true)",
                    "default": True,
                },
            },
            "required": [],
        }

    def __init__(
        self,
        gen_image_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self.gen_image_model = gen_image_model or "openai/doubao-seedream-4-5-251128"
        self.api_key = api_key
        self.api_base = api_base
        self._send_callback = send_callback

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    @property
    def _is_seedream_model(self) -> bool:
        """Check if the current model is a Seedream model."""
        return "seedream" in self.gen_image_model.lower()

    async def _parse_image_data(self, image_str: str) -> tuple[str, str]:
        """
        Parse image from base64 data URI, URL, or file path.
        Returns: (image_data, format_type) where format_type is "data" or "url"
        """
        if image_str.startswith("data:"):
            return image_str, "data"
        elif image_str.startswith("http://") or image_str.startswith("https://"):
            return image_str, "url"
        else:
            mime_type, _ = mimetypes.guess_type(image_str)
            if not mime_type:
                mime_type = "application/octet-stream"
            base64_str = base64.b64encode(Path(image_str).read_bytes()).decode("utf-8")
            data_uri = f"data:{mime_type};base64,{base64_str}"
            return data_uri, "data"

    async def _url_to_base64(self, url: str) -> str:
        """Download image from URL and convert to base64."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return base64.b64encode(response.content).decode("utf-8")

    def _build_common_kwargs(
        self,
        size: str,
        n: int,
        include_size: bool = True,
        include_style: bool = True,
        quality: str | None = None,
        style: str | None = None,
    ) -> dict[str, Any]:
        """Build common kwargs for image generation calls."""
        kwargs: dict[str, Any] = {
            "model": self.gen_image_model,
            "n": n,
        }
        if include_size:
            kwargs["size"] = size
        if quality:
            kwargs["quality"] = quality
        if include_style and style:
            kwargs["style"] = style
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        return kwargs

    async def _seedream_image_to_image(
        self,
        base_image: str,
        prompt: str,
        strength: float,
        size: str,
        n: int,
    ) -> Any:
        """Shared method for Seedream image-to-image generation (used by edit and variation modes)."""
        base_image_data, base_format = await self._parse_image_data(base_image)
        kwargs = self._build_common_kwargs(
            size=size,
            n=n,
            include_size=False,
            include_style=False,
        )
        kwargs.update(
            {
                "prompt": prompt,
                "strength": strength,
            }
        )
        if base_format == "data":
            kwargs["image"] = base_image_data
        else:
            kwargs["image"] = base_image_data
        return await litellm.aimage_generation(**kwargs)

    async def execute(
        self,
        tool_context: "ToolContext",
        mode: str = "generate",
        prompt: str | None = None,
        base_image: str | None = None,
        mask: str | None = None,
        size: str = "1920x1920",
        quality: str = "standard",
        style: str = "vivid",
        n: int = 1,
        send_to_user: bool = True,
        **kwargs: Any,
    ) -> str:
        try:
            if mode in ["edit", "variation"] and not base_image:
                return f"Error: base_image is required for {mode} mode"
            if mode in ["generate", "edit"] and not prompt:
                return f"Error: prompt is required for {mode} mode"

            # Execute based on mode
            if mode == "generate":
                gen_kwargs = self._build_common_kwargs(
                    size=size,
                    n=n,
                    quality=quality,
                    style=style,
                )
                gen_kwargs["prompt"] = prompt
                response = await litellm.aimage_generation(**gen_kwargs)

            elif mode == "edit":
                if self._is_seedream_model:
                    response = await self._seedream_image_to_image(
                        base_image=base_image,  # type: ignore[arg-type]
                        prompt=prompt,  # type: ignore[arg-type]
                        strength=0.7,
                        size=size,
                        n=n,
                    )
                else:
                    base_image_data, base_format = await self._parse_image_data(base_image)  # type: ignore[arg-type]
                    edit_kwargs = self._build_common_kwargs(size=size, n=n, include_style=False)
                    edit_kwargs["prompt"] = prompt
                    edit_kwargs["image"] = base_image_data
                    if mask:
                        mask_data, mask_format = await self._parse_image_data(mask)
                        if mask_format == "bytes":
                            edit_kwargs["mask"] = BytesIO(mask_data)  # type: ignore
                        else:
                            edit_kwargs["mask"] = mask_data
                    response = await litellm.aimage_edit(**edit_kwargs)

            elif mode == "variation":
                if self._is_seedream_model:
                    response = await self._seedream_image_to_image(
                        base_image=base_image,  # type: ignore[arg-type]
                        prompt="Create a variation of this image",
                        strength=0.3,
                        size=size,
                        n=n,
                    )
                else:
                    base_image_data, base_format = await self._parse_image_data(base_image)  # type: ignore[arg-type]
                    var_kwargs = self._build_common_kwargs(size=size, n=n, include_style=False)
                    var_kwargs["image"] = base_image_data
                    response = await litellm.aimage_variation(**var_kwargs)

            else:
                return f"Error: Unknown mode '{mode}'"

            # Extract and save images
            images = []
            for data in response.data:
                if hasattr(data, "b64_json") and data.b64_json is not None:
                    images.append(data.b64_json)
                elif hasattr(data, "url") and data.url is not None:
                    images.append(await self._url_to_base64(data.url))

            if not images:
                return "Error: No images generated"

            images_dir = get_data_path() / "images"
            images_dir.mkdir(exist_ok=True)
            saved_paths = ["生成图片："]
            saved_filenames = []

            for img in images:
                random_filename = f"{uuid.uuid4().hex}.png"
                image_path = images_dir / random_filename
                if img.startswith("data:"):
                    _, img = img.split(",", 1)
                image_bytes = base64.b64decode(img)
                with open(image_path, "wb") as f:
                    f.write(image_bytes)
                saved_paths.append(f"send://{random_filename}")
                saved_filenames.append(random_filename)

            # Send to user if requested
            sent_to_user = False
            if send_to_user and self._send_callback:
                try:
                    msg_content = "\n".join([f"send://{f}" for f in saved_filenames])
                    msg = OutboundMessage(session_key=tool_context.session_key, content=msg_content)
                    await self._send_callback(msg)
                    sent_to_user = True
                except Exception as e:
                    return f"Error sending image to user: {str(e)}"

            result = "\n".join(saved_paths)
            if sent_to_user:
                result += "\n（已发送给用户）"

            return result

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            log = logging.getLogger(__name__)
            log.error(f"Image generation error: {e}")
            log.error(f"Error details: {error_details}")
            return f"Error generating image: {e}"
