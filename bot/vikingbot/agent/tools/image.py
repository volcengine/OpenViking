"""Image generation tool using LiteLLM's image generation capabilities."""

import base64
import logging
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import httpx
import litellm

from vikingbot.agent.tools.base import Tool


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
                    "default": "generate"
                },
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate or edit (required for generate and edit modes)"
                },
                "base_image": {
                    "type": "string",
                    "description": "Base image for edit/variation mode: base64 data URI or image URL (required for edit and variation modes)"
                },
                "mask": {
                    "type": "string",
                    "description": "Mask image for edit mode: base64 data URI or image URL (optional, transparent areas indicate where to edit)"
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1792x1024", "1024x1792", "1920x1920"],
                    "description": "Image size (default: 1920x1920)",
                    "default": "1920x1920"
                },
                "quality": {
                    "type": "string",
                    "enum": ["standard", "hd"],
                    "description": "Image quality (default: standard)",
                    "default": "standard"
                },
                "style": {
                    "type": "string",
                    "enum": ["vivid", "natural"],
                    "description": "Image style (DALL-E 3 only, default: vivid)",
                    "default": "vivid"
                },
                "n": {
                    "type": "integer",
                    "description": "Number of images to generate (1-4)",
                    "minimum": 1,
                    "maximum": 4,
                    "default": 1
                }
            },
            "required": []
        }
    
    def __init__(
        self, 
        gen_image_model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None
    ):
        self.gen_image_model = gen_image_model or "openai/doubao-seedream-4-5-251128"
        self.api_key = api_key
        self.api_base = api_base
    
    async def _parse_image_data(self, image_str: str) -> tuple[bytes | str, str]:
        """
        Parse image from base64 data URI or URL.
        Returns: (image_data, format_type) where format_type is "bytes" or "url"
        """
        if image_str.startswith("data:"):
            # Parse data URI
            header, data = image_str.split(",", 1)
            if ";base64" in header:
                return base64.b64decode(data), "bytes"
            else:
                return data.encode("utf-8"), "bytes"
        elif image_str.startswith("http://") or image_str.startswith("https://"):
            # Return URL directly for the model
            return image_str, "url"
        else:
            # Assume it's raw base64 without prefix
            return base64.b64decode(image_str), "bytes"
    
    async def _url_to_base64(self, url: str) -> str:
        """Download image from URL and convert to base64."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            image_data = response.content
            b64 = base64.b64encode(image_data).decode("utf-8")
            return b64
    
    async def execute(
        self, 
        mode: str = "generate",
        prompt: str | None = None,
        base_image: str | None = None,
        mask: str | None = None,
        size: str = "1920x1920",
        quality: str = "standard",
        style: str = "vivid",
        n: int = 1,
        **kwargs: Any
    ) -> str:
        try:
            # Validate required parameters based on mode
            if mode in ["edit", "variation"] and not base_image:
                return f"Error: base_image is required for {mode} mode"
            
            if mode in ["generate", "edit"] and not prompt:
                return f"Error: prompt is required for {mode} mode"
            
            # Common kwargs
            common_kwargs: dict[str, Any] = {
                "model": self.gen_image_model,
                "size": size,
                "n": n,
            }
            
            # Pass api_key and api_base
            if self.api_key:
                common_kwargs["api_key"] = self.api_key
            if self.api_base:
                common_kwargs["api_base"] = self.api_base
            
            # Execute based on mode
            if mode == "generate":
                # Generate from scratch
                kwargs = {
                    **common_kwargs,
                    "prompt": prompt,
                    "quality": quality,
                    "style": style,
                }
                response = await litellm.aimage_generation(**kwargs)
            elif mode == "edit":
                # Edit existing image
                # For Seedream models, use image-to-image generation instead of edit endpoint
                if "seedream" in self.gen_image_model.lower():
                    # Use image-to-image generation for Seedream models
                    base_image_result = await self._parse_image_data(base_image)  # type: ignore[arg-type]
                    base_image_data, base_format = base_image_result
                    
                    # Use image-to-image generation with strength parameter
                    edit_kwargs = {
                        **common_kwargs,
                        "prompt": prompt,
                        "strength": 0.7,  # Default edit strength
                    }
                    
                    # Add image parameter based on format
                    if base_format == "bytes":
                        # bytes format: wrap in BytesIO
                        assert isinstance(base_image_data, bytes), "Expected bytes for 'bytes' format"
                        edit_kwargs["image"] = BytesIO(base_image_data)
                    else:  # url
                        # url format: pass directly as URL
                        assert isinstance(base_image_data, str), "Expected str for 'url' format"
                        edit_kwargs["image"] = base_image_data
                    
                    # Remove style parameter for Seedream if not supported
                    if "style" in edit_kwargs:
                        del edit_kwargs["style"]
                    
                    # Remove size parameter for Seedream image-to-image mode
                    if "size" in edit_kwargs:
                        del edit_kwargs["size"]
                    
                    response = await litellm.aimage_generation(**edit_kwargs)
                else:
                    # Use standard edit endpoint for other models
                    base_image_result = await self._parse_image_data(base_image)  # type: ignore[arg-type]
                    base_image_data, base_format = base_image_result
                    
                    edit_kwargs = {
                        **common_kwargs,
                        "prompt": prompt,
                    }
                    
                    # Add image parameter based on format
                    if base_format == "bytes":
                        edit_kwargs["image"] = BytesIO(base_image_data)  # type: ignore
                    else:  # url
                        edit_kwargs["image"] = base_image_data  # type: ignore
                    
                    if mask:
                        mask_result = await self._parse_image_data(mask)  # type: ignore[arg-type]
                        mask_data, mask_format = mask_result
                        if mask_format == "bytes":
                            edit_kwargs["mask"] = BytesIO(mask_data)  # type: ignore
                        else:  # url
                            edit_kwargs["mask"] = mask_data  # type: ignore
                    
                    response = await litellm.aimage_edit(**edit_kwargs)
            elif mode == "variation":
                # Create variations
                # For Seedream models, use image-to-image generation with low strength
                if "seedream" in self.gen_image_model.lower():
                    # Use image-to-image generation with low strength for variations
                    base_image_result = await self._parse_image_data(base_image)  # type: ignore[arg-type]
                    base_image_data, base_format = base_image_result
                    
                    # Use image-to-image generation with low strength for variations
                    variation_kwargs = {
                        **common_kwargs,
                        "prompt": "Create a variation of this image",  # Simple prompt for variations
                        "strength": 0.3,  # Low strength for variations
                    }
                    
                    # Add image parameter based on format
                    if base_format == "bytes":
                        # bytes format: wrap in BytesIO
                        assert isinstance(base_image_data, bytes), "Expected bytes for 'bytes' format"
                        variation_kwargs["image"] = BytesIO(base_image_data)
                    else:  # url
                        # url format: pass directly as URL
                        assert isinstance(base_image_data, str), "Expected str for 'url' format"
                        variation_kwargs["image"] = base_image_data
                    
                    # Remove style parameter for Seedream if not supported
                    if "style" in variation_kwargs:
                        del variation_kwargs["style"]
                    
                    # Remove size parameter for Seedream image-to-image mode
                    if "size" in variation_kwargs:
                        del variation_kwargs["size"]
                    
                    response = await litellm.aimage_generation(**variation_kwargs)
                else:
                    # Use standard variation endpoint for other models
                    base_image_result = await self._parse_image_data(base_image)  # type: ignore[arg-type]
                    base_image_data, base_format = base_image_result
                    
                    variation_kwargs = {
                        **common_kwargs,
                    }
                    
                    # Add image parameter based on format
                    if base_format == "bytes":
                        variation_kwargs["image"] = BytesIO(base_image_data)  # type: ignore
                    else:  # url
                        variation_kwargs["image"] = base_image_data  # type: ignore
                    
                    response = await litellm.aimage_variation(**variation_kwargs)
            else:
                return f"Error: Unknown mode '{mode}'"
            
            # Extract images
            images = []
            for data in response.data:
                if hasattr(data, "b64_json") and data.b64_json is not None:
                    images.append(data.b64_json)
                elif hasattr(data, "url") and data.url is not None:
                    # Download URL and convert to base64
                    b64 = await self._url_to_base64(data.url)
                    images.append(b64)
            
            if not images:
                return "Error: No images generated"
            
            # Format response - return base64 with data URI prefix
            lines = []
            for i, img in enumerate(images, 1):
                if img.startswith("data:"):
                    lines.append(img)
                else:
                    lines.append(f"data:image/png;base64,{img}")
            
            return "\n\n".join(lines)
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            log = logging.getLogger(__name__)
            log.error(f"Image generation error: {e}")
            log.error(f"Error details: {error_details}")
            return f"Error generating image: {e}"
