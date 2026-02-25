# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VolcEngine VLM backend implementation"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

from .openai_vlm import OpenAIVLM

logger = logging.getLogger(__name__)


class VolcEngineVLM(OpenAIVLM):
    """VolcEngine VLM backend"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client = None
        # Ensure provider type is correct
        self.provider = "volcengine"

        # VolcEngine-specific defaults
        if not self.api_base:
            self.api_base = "https://ark.cn-beijing.volces.com/api/v3"
        if not self.model:
            self.model = "doubao-seed-1-8-251228"

    def get_client(self):
        """Get sync client"""
        if self._sync_client is None:
            try:
                import volcenginesdkarkruntime
            except ImportError:
                raise ImportError(
                    "Please install volcenginesdkarkruntime: pip install volcenginesdkarkruntime"
                )
            self._sync_client = volcenginesdkarkruntime.Ark(
                api_key=self.api_key,
                base_url=self.api_base,
            )
        return self._sync_client

    def get_async_client(self):
        """Get async client"""
        if self._async_client is None:
            try:
                import volcenginesdkarkruntime
            except ImportError:
                raise ImportError(
                    "Please install volcenginesdkarkruntime: pip install volcenginesdkarkruntime"
                )
            self._async_client = volcenginesdkarkruntime.AsyncArk(
                api_key=self.api_key,
                base_url=self.api_base,
            )
        return self._async_client

    def get_completion(self, prompt: str, thinking: bool = False) -> str:
        """Get text completion"""
        client = self.get_client()
        kwargs = {
            "model": self.model or "doubao-seed-1-8-251228",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }

        response = client.chat.completions.create(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    async def get_completion_async(
        self, prompt: str, thinking: bool = False, max_retries: int = 0
    ) -> str:
        """Get text completion asynchronously"""
        client = self.get_async_client()
        kwargs = {
            "model": self.model or "doubao-seed-1-8-251228",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(**kwargs)
                self._update_token_usage_from_response(response)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        if last_error:
            raise last_error
        else:
            raise RuntimeError("Unknown error in async completion")

    def _detect_image_format(self, data: bytes) -> str:
        """Detect image format from magic bytes.

        Returns the MIME type, or raises ValueError for unsupported formats like SVG.

        Supported formats per VolcEngine docs:
        https://www.volcengine.com/docs/82379/1362931
        - JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, DIB, ICNS, SGI, JPEG2000, HEIC, HEIF
        """
        if len(data) < 12:
            logger.warning(f"[VolcEngineVLM] Image data too small: {len(data)} bytes")
            return "image/png"

        # PNG: 89 50 4E 47 0D 0A 1A 0A
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        # JPEG: FF D8
        elif data[:2] == b"\xff\xd8":
            return "image/jpeg"
        # GIF: GIF87a or GIF89a
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        # WEBP: RIFF....WEBP
        elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
            return "image/webp"
        # BMP: BM
        elif data[:2] == b"BM":
            return "image/bmp"
        # TIFF (little-endian): 49 49 2A 00
        # TIFF (big-endian): 4D 4D 00 2A
        elif data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
            return "image/tiff"
        # ICO: 00 00 01 00
        elif data[:4] == b"\x00\x00\x01\x00":
            return "image/ico"
        # ICNS: 69 63 6E 73 ("icns")
        elif data[:4] == b"icns":
            return "image/icns"
        # SGI: 01 DA
        elif data[:2] == b"\x01\xda":
            return "image/sgi"
        # JPEG2000: 00 00 00 0C 6A 50 20 20 (JP2 signature)
        elif data[:8] == b"\x00\x00\x00\x0cjP  " or data[:4] == b"\xff\x4f\xff\x51":
            return "image/jp2"
        # HEIC/HEIF: ftyp box with heic/heif brand
        # 00 00 00 XX 66 74 79 70 68 65 69 63 (heic)
        # 00 00 00 XX 66 74 79 70 68 65 69 66 (heif)
        elif len(data) >= 12 and data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand == b"heic":
                return "image/heic"
            elif brand == b"heif":
                return "image/heif"
            elif brand[:3] == b"mif":
                return "image/heif"
        # SVG (not supported)
        elif data[:4] == b"<svg" or (data[:5] == b"<?xml" and b"<svg" in data[:100]):
            raise ValueError(
                "SVG format is not supported by VolcEngine VLM API. "
                "Supported formats: JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, ICNS, SGI, JPEG2000, HEIC, HEIF"
            )

        # Unknown format - log and default to PNG
        logger.warning(f"[VolcEngineVLM] Unknown image format, magic bytes: {data[:16].hex()}")
        return "image/png"

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        """Prepare image data"""
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            mime_type = self._detect_image_format(image)
            logger.info(
                f"[VolcEngineVLM] Preparing image from bytes, size={len(image)}, detected mime={mime_type}"
            )
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        elif isinstance(image, Path) or (
            isinstance(image, str) and not image.startswith(("http://", "https://"))
        ):
            path = Path(image)
            suffix = path.suffix.lower()
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".dib": "image/bmp",
                ".tiff": "image/tiff",
                ".tif": "image/tiff",
                ".ico": "image/ico",
                ".icns": "image/icns",
                ".sgi": "image/sgi",
                ".j2c": "image/jp2",
                ".j2k": "image/jp2",
                ".jp2": "image/jp2",
                ".jpc": "image/jp2",
                ".jpf": "image/jp2",
                ".jpx": "image/jp2",
                ".heic": "image/heic",
                ".heif": "image/heif",
            }.get(suffix, "image/png")
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        else:
            return {"type": "image_url", "image_url": {"url": image}}

    def get_vision_completion(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion"""
        client = self.get_client()

        content = []
        for img in images:
            content.append(self._prepare_image(img))
        content.append({"type": "text", "text": prompt})

        kwargs = {
            "model": self.model or "doubao-seed-1-8-251228",
            "messages": [{"role": "user", "content": content}],
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }

        response = client.chat.completions.create(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    async def get_vision_completion_async(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion asynchronously"""
        client = self.get_async_client()

        content = []
        for img in images:
            content.append(self._prepare_image(img))
        content.append({"type": "text", "text": prompt})

        kwargs = {
            "model": self.model or "doubao-seed-1-8-251228",
            "messages": [{"role": "user", "content": content}],
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }

        response = await client.chat.completions.create(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""
