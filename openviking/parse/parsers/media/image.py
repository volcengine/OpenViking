# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Image parser with lightweight semantic artifact generation."""

import asyncio
import io
import re
import time
from pathlib import Path
from typing import List, Optional, Union

from PIL import Image

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import IMAGE_EXTENSIONS
from openviking.prompts import render_prompt
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.config.parser_config import ImageConfig
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


def _clean_text(value: str) -> str:
    """Collapse whitespace so semantic sidecars stay compact."""
    return re.sub(r"\s+", " ", value or "").strip()


def _truncate_text(value: str, limit: int) -> str:
    """Truncate text without returning empty placeholders."""
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


class ImageParser(BaseParser):
    """Parser for standalone image resources."""

    def __init__(self, config: Optional[ImageConfig] = None, **kwargs):
        self.config = config or ImageConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return IMAGE_EXTENSIONS

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs):
        start_time = time.time()
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Image file not found: {source}")

        image_bytes = file_path.read_bytes()
        ext = file_path.suffix.lower()
        original_filename = file_path.name.replace(" ", "_")
        stem = file_path.stem.replace(" ", "_")
        ext_no_dot = ext[1:] if ext else "image"
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")

        viking_fs = self._get_viking_fs()
        temp_uri = self._create_temp_uri()
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(temp_uri, exist_ok=True)
        await viking_fs.mkdir(root_dir_uri, exist_ok=True)
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", image_bytes)

        metadata = self._extract_image_metadata(file_path)

        ocr_text = None
        if self.config.enable_ocr:
            ocr_text = await self._ocr_extract(image_bytes, lang=self.config.ocr_lang)
            if ocr_text:
                await viking_fs.write_file(f"{root_dir_uri}/ocr.md", ocr_text)

        visual_description = None
        if self.config.enable_vlm and self._is_vlm_available() and ext != ".svg":
            visual_description = await self._vlm_describe(
                image_bytes,
                model=self.config.vlm_model,
                instruction=instruction,
            )

        description = self._build_description(
            original_filename=original_filename,
            metadata=metadata,
            visual_description=visual_description,
            ocr_text=ocr_text,
        )
        await viking_fs.write_file(f"{root_dir_uri}/description.md", description)

        node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            content_type="image",
            auxiliary_files={
                "original": original_filename,
                "description": "description.md",
                **({"ocr": "ocr.md"} if ocr_text else {}),
            },
            meta={
                **metadata,
                "content_type": "image",
                "source_title": file_path.stem,
                "semantic_name": file_path.stem,
                "original_filename": original_filename,
                "file_size_bytes": len(image_bytes),
                "has_ocr": bool(ocr_text),
                "has_visual_description": bool(visual_description),
                "description_file": "description.md",
                "ocr_file": "ocr.md" if ocr_text else None,
            },
        )
        await self._generate_semantic_info(
            node=node,
            description=description,
            viking_fs=viking_fs,
            has_ocr=bool(ocr_text),
            root_dir_uri=root_dir_uri,
        )

        result = create_parse_result(
            root=node,
            source_path=str(file_path),
            source_format="image",
            parser_name="ImageParser",
            parse_time=time.time() - start_time,
            meta={
                "content_type": "image",
                "format": metadata["format"],
                "has_ocr": bool(ocr_text),
                "has_visual_description": bool(visual_description),
            },
        )
        result.temp_dir_path = temp_uri
        return result

    def _extract_image_metadata(self, file_path: Path) -> dict:
        """Validate the image and collect basic metadata."""
        try:
            with Image.open(file_path) as img:
                img.verify()
            with Image.open(file_path) as img:
                width, height = img.size
                format_str = (img.format or file_path.suffix.lstrip(".") or "image").lower()
                mode = img.mode or "unknown"
        except Exception as exc:
            raise ValueError(f"Invalid image file: {file_path}. Error: {exc}") from exc

        return {
            "width": width,
            "height": height,
            "format": format_str,
            "mode": mode,
        }

    def _is_vlm_available(self) -> bool:
        """Return True when VLM config is present and usable."""
        try:
            return get_openviking_config().vlm.is_available()
        except Exception:
            return False

    def _build_description(
        self,
        *,
        original_filename: str,
        metadata: dict,
        visual_description: Optional[str],
        ocr_text: Optional[str],
    ) -> str:
        """Assemble a semantic markdown description for the image."""
        summary = _clean_text(visual_description or "")
        if not summary and ocr_text:
            summary = (
                "Image content inferred from OCR-recognized text because VLM analysis "
                "was unavailable."
            )
        if not summary:
            summary = (
                f"Image file `{original_filename}` in {metadata['format'].upper()} format "
                f"with resolution {metadata['width']}x{metadata['height']}."
            )

        parts = ["# Image Summary", "", summary, "", "## Metadata"]
        parts.append(f"- Format: {metadata['format'].upper()}")
        parts.append(f"- Resolution: {metadata['width']}x{metadata['height']}")
        parts.append(f"- Color mode: {metadata['mode']}")

        if ocr_text:
            parts.extend(
                [
                    "",
                    "## OCR Text",
                    ocr_text.strip(),
                ]
            )
        elif self.config.enable_ocr:
            parts.extend(["", "## OCR Text", "No OCR text was detected in the image."])

        return "\n".join(parts).strip() + "\n"

    async def _vlm_describe(
        self,
        image_bytes: bytes,
        model: Optional[str],
        instruction: str = "",
    ) -> str:
        """Generate image description using VLM when configured."""
        try:
            vlm = get_openviking_config().vlm
            prompt = render_prompt(
                "parsing.image_summary",
                {
                    "context": instruction.strip() or "No additional context",
                },
            )
            response = await vlm.get_vision_completion_async(
                prompt=prompt,
                images=[image_bytes],
            )
            logger.info(
                "[ImageParser._vlm_describe] VLM response received, length=%s",
                len(response),
            )
            return str(response).strip()
        except Exception as exc:
            logger.error(
                "[ImageParser._vlm_describe] Error in VLM image description: %s",
                exc,
                exc_info=True,
            )
            return ""

    async def _ocr_extract(self, image_bytes: bytes, lang: str) -> Optional[str]:
        """Extract text from image using OCR via Tesseract."""
        try:
            import pytesseract
        except ImportError:
            logger.warning("pytesseract not installed. Install with: pip install openviking[ocr]")
            return None

        def _sync_ocr() -> Optional[str]:
            img = Image.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(img, lang=lang).strip()
            return text if text else None

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _sync_ocr)
        except Exception as exc:
            logger.error("[ImageParser._ocr_extract] OCR extraction failed: %s", exc, exc_info=True)
            return None

    async def _generate_semantic_info(
        self,
        node: ResourceNode,
        description: str,
        viking_fs,
        has_ocr: bool,
        root_dir_uri: str,
    ) -> None:
        """Populate and persist the image L0/L1 summaries."""
        abstract = _truncate_text(description, 220) or f"Image: {node.meta['original_filename']}"

        overview_parts = [
            "## Content Summary",
            "",
            _truncate_text(description, 1800),
            "",
            "## Available Files",
            f"- {node.meta['original_filename']}: Original image file",
            "- description.md: Semantic markdown summary for the image",
        ]
        if has_ocr:
            overview_parts.append("- ocr.md: OCR-recognized text extracted from the image")

        overview_parts.extend(
            [
                "",
                "## Metadata",
                f"- Format: {node.meta['format'].upper()}",
                f"- Resolution: {node.meta['width']}x{node.meta['height']}",
                f"- Color mode: {node.meta['mode']}",
            ]
        )

        overview = "\n".join(overview_parts).strip() + "\n"

        node.meta["abstract"] = abstract
        node.meta["overview"] = overview

        await viking_fs.write_file(f"{root_dir_uri}/.abstract.md", abstract)
        await viking_fs.write_file(f"{root_dir_uri}/.overview.md", overview)

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ):
        raise NotImplementedError("Image parsing not yet implemented")
