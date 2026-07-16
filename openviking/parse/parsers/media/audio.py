# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Audio parser - Future implementation.

Planned Features:
1. Speech-to-text transcription using ASR models
2. Audio metadata extraction (duration, sample rate, channels)
3. Speaker diarization (identify different speakers)
4. Timestamp alignment for transcribed text
5. Generate structured ResourceNode with transcript

Example workflow:
    1. Load audio file
    2. Extract metadata (duration, format, sample rate)
    3. Transcribe speech to text using Whisper or similar
    4. (Optional) Perform speaker diarization
    5. Create ResourceNode with:
       - type: NodeType.ROOT
       - children: sections for each speaker/timestamp
       - meta: audio metadata and timestamps
    6. Return ParseResult

Supported formats: MP3, WAV, OGG, FLAC, AAC, M4A, OPUS, AC3
"""

import base64
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import AUDIO_EXTENSIONS
from openviking.parse.parsers.media.naming import resolve_media_names
from openviking_cli.utils.config.parser_config import AudioConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class AudioParser(BaseParser):
    """
    Audio parser for audio files.
    """

    def __init__(self, config: Optional[AudioConfig] = None, **kwargs):
        """
        Initialize AudioParser.

        Args:
            config: Audio parsing configuration
            **kwargs: Additional configuration parameters
        """
        self.config = config or AudioConfig()

    @property
    def supported_extensions(self) -> List[str]:
        """Return supported audio file extensions."""
        return AUDIO_EXTENSIONS

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse audio file - only copy original file and extract basic metadata, no content understanding.

        Args:
            source: Audio file path
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with audio content

        Raises:
            FileNotFoundError: If source file does not exist
            IOError: If audio processing fails
        """
        from openviking.storage.viking_fs import get_viking_fs

        # Convert to Path object
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {source}")

        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()

        # Phase 1: Generate temporary files
        audio_bytes = file_path.read_bytes()
        ext = file_path.suffix

        from openviking_cli.utils.uri import VikingURI

        # Resolve the resource name from the caller's resource_name / source_name
        # (falling back to the temp file name) so the filename, URI and title
        # reflect the real upload, not the internal temp id — see resolve_media_names.
        display_stem, stem, original_filename = resolve_media_names(file_path, ext, **kwargs)
        # Root directory name: filename stem + _ + extension (without dot)
        ext_no_dot = ext[1:] if ext else ""
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(root_dir_uri, exist_ok=True)

        # 1.1 Save original audio with original filename (sanitized)
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", audio_bytes)

        # 1.2 Validate audio file using magic bytes
        # Define magic bytes for supported audio formats
        audio_magic_bytes = {
            ".mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
            ".wav": [b"RIFF"],
            ".ogg": [b"OggS"],
            ".flac": [b"fLaC"],
            ".aac": [b"\xff\xf1", b"\xff\xf9"],
            ".m4a": [b"\x00\x00\x00", b"ftypM4A", b"ftypisom"],
            ".opus": [b"OggS"],
            ".ac3": [b"\x0b\x77"],
        }

        # Check magic bytes
        valid = False
        ext_lower = ext.lower()
        magic_list = audio_magic_bytes.get(ext_lower, [])
        for magic in magic_list:
            if len(audio_bytes) >= len(magic) and audio_bytes.startswith(magic):
                valid = True
                break

        if not valid:
            raise ValueError(
                f"Invalid audio file: {file_path}. File signature does not match expected format {ext_lower}"
            )

        # Extract audio metadata (placeholder)
        duration = 0
        sample_rate = 0
        channels = 0
        format_str = ext[1:].upper()

        # Create ResourceNode - metadata only, no content understanding yet
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=display_stem,
            level=0,
            detail_file=None,
            content_path=None,
            children=[],
            meta={
                "duration": duration,
                "sample_rate": sample_rate,
                "channels": channels,
                "format": format_str.lower(),
                "content_type": "audio",
                "source_title": display_stem,
                "semantic_name": display_stem,
                "original_filename": original_filename,
            },
        )

        # Phase 3: Build directory structure (handled by TreeBuilder)
        return ParseResult(
            root=root_node,
            source_path=str(file_path),
            temp_dir_path=temp_uri,
            source_format="audio",
            parser_name="AudioParser",
            meta={"content_type": "audio", "format": format_str.lower()},
        )

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse audio from base64 content string.

        Args:
            content: Audio content (base64 or binary string)
            source_path: Optional source path for metadata
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with audio content

        Raises:
            ValueError: If content is not valid base64 audio data
        """
        temp_file_path = None
        try:
            if content.startswith("data:") and "," in content:
                content = content.split(",", 1)[1]

            audio_bytes = base64.b64decode(content, validate=True)
            suffix = Path(source_path).suffix if source_path else ".wav"
            if not suffix:
                suffix = ".wav"

            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as temp_file:
                temp_file.write(audio_bytes)
                temp_file_path = temp_file.name

            result = await self.parse(temp_file_path, instruction=instruction, **kwargs)
            if source_path:
                result.source_path = source_path
            return result
        except Exception as e:
            logger.exception("Failed to parse audio content: %s", e)
            raise ValueError(f"Invalid audio content: {str(e)}") from e
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to cleanup temporary parse file %s: %s",
                        temp_file_path,
                        cleanup_error,
                    )
