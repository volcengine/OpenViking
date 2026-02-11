# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Audio file parser for OpenViking.

Extracts metadata from audio files using mutagen.
Inspired by microsoft/markitdown approach.
"""

import os
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser
from openviking.utils.config.parser_config import ParserConfig
from openviking.utils.logger import get_logger

logger = get_logger(__name__)


class AudioParser(BaseParser):
    """
    Audio file parser for OpenViking.

    Supports: .mp3, .wav, .m4a, .flac, .ogg, .aac, .wma

    Extracts metadata from audio files using mutagen,
    converts to markdown and delegates to MarkdownParser.
    """

    def __init__(self, config: Optional[ParserConfig] = None, include_transcription: bool = False):
        """
        Initialize Audio parser.

        Args:
            config: Parser configuration
            include_transcription: Whether to include transcription placeholder
        """
        from openviking.parse.parsers.markdown import MarkdownParser

        self._md_parser = MarkdownParser(config=config)
        self.config = config or ParserConfig()
        self.include_transcription = include_transcription

    @property
    def supported_extensions(self) -> List[str]:
        return [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """Parse audio file from file path."""
        path = Path(source)

        if path.exists():
            markdown_content = self._convert_to_markdown(path)
            result = await self._md_parser.parse_content(
                markdown_content, source_path=str(path), instruction=instruction, **kwargs
            )
        else:
            result = await self._md_parser.parse_content(
                str(source), instruction=instruction, **kwargs
            )
        result.source_format = "audio"
        result.parser_name = "AudioParser"
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """Parse content - delegates to MarkdownParser."""
        result = await self._md_parser.parse_content(content, source_path, **kwargs)
        result.source_format = "audio"
        result.parser_name = "AudioParser"
        return result

    def _convert_to_markdown(self, path: Path) -> str:
        """Convert audio file metadata to Markdown string."""
        markdown_parts = []
        markdown_parts.append(f"# Audio File: {path.name}")

        try:
            import mutagen

            audio = mutagen.File(path)

            if audio is None:
                markdown_parts.append("*Unable to read audio file metadata*")
                return "\n\n".join(markdown_parts)

            # Basic file info
            file_size = os.path.getsize(path)
            markdown_parts.append(f"**File Size:** {self._format_size(file_size)}")

            # Audio properties
            if hasattr(audio, "info"):
                info = audio.info

                if hasattr(info, "length"):
                    duration = self._format_duration(info.length)
                    markdown_parts.append(f"**Duration:** {duration}")

                if hasattr(info, "bitrate"):
                    markdown_parts.append(f"**Bitrate:** {info.bitrate // 1000} kbps")

                if hasattr(info, "sample_rate"):
                    markdown_parts.append(f"**Sample Rate:** {info.sample_rate} Hz")

                if hasattr(info, "channels"):
                    markdown_parts.append(f"**Channels:** {info.channels}")

            # Extract metadata/tags
            tags = self._extract_tags(audio)
            if tags:
                markdown_parts.append("## Metadata")
                for key, value in tags.items():
                    markdown_parts.append(f"- **{key}:** {value}")

            # Transcription placeholder
            if self.include_transcription:
                markdown_parts.append("## Transcription")
                markdown_parts.append(
                    "*Transcription requires external speech-to-text service. "
                    "Configure whisper or similar service for full transcription.*"
                )

        except ImportError:
            markdown_parts.append("*mutagen not installed. Install with: pip install mutagen*")
        except Exception as e:
            markdown_parts.append(f"*Error reading audio file: {e}*")

        return "\n\n".join(markdown_parts)

    def _extract_tags(self, audio) -> dict:
        """Extract metadata tags from audio file."""
        tags = {}

        if hasattr(audio, "tags") and audio.tags:
            tag_mapping = {
                "TIT2": "Title",
                "TPE1": "Artist",
                "TALB": "Album",
                "TCON": "Genre",
                "TYER": "Year",
                "TDRC": "Date",
                "TRCK": "Track Number",
                "COMM": "Comments",
                "TPE2": "Album Artist",
                "TPUB": "Publisher",
                "TCOM": "Composer",
            }

            for key, label in tag_mapping.items():
                if key in audio.tags:
                    try:
                        value = str(audio.tags[key])
                        if value:
                            tags[label] = value
                    except Exception:
                        pass

            # For MP4/M4A files
            if hasattr(audio.tags, "_DictProxy__dict"):
                mp4_mapping = {
                    "\xa9nam": "Title",
                    "\xa9ART": "Artist",
                    "\xa9alb": "Album",
                    "\xa9gen": "Genre",
                    "\xa9day": "Year",
                    "trkn": "Track Number",
                }
                for key, label in mp4_mapping.items():
                    if key in audio.tags:
                        try:
                            value = (
                                audio.tags[key][0]
                                if isinstance(audio.tags[key], list)
                                else audio.tags[key]
                            )
                            tags[label] = str(value)
                        except Exception:
                            pass

        return tags

    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human readable string."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"
