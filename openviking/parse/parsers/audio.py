# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Audio file parser for OpenViking.

Extracts metadata from audio files and optionally transcribes content.
Inspired by microsoft/markitdown approach.
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from openviking.parse.base import ParseResult
from openviking.parse.parsers.base_parser import BaseParser

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class AudioParser(BaseParser):
    """
    Audio file parser for OpenViking.

    Supports: .mp3, .wav, .m4a, .flac, .ogg, .aac

    Extracts metadata from audio files using mutagen,
    and optionally provides transcription placeholder.
    Converts to markdown and delegates to MarkdownParser.

    Features:
    - ID3 tag extraction (title, artist, album, etc.)
    - Audio properties (duration, bitrate, sample rate)
    - Transcription placeholder (requires additional setup)
    """

    def __init__(self, include_transcription: bool = False):
        """
        Initialize Audio parser.

        Args:
            include_transcription: Whether to include transcription placeholder
                                  (actual transcription requires external service)
        """
        self.include_transcription = include_transcription
        self._markdown_parser = None
        self._mutagen_module = None

    def _get_markdown_parser(self):
        """Lazy import MarkdownParser."""
        if self._markdown_parser is None:
            from openviking.parse.parsers.markdown import MarkdownParser

            self._markdown_parser = MarkdownParser()
        return self._markdown_parser

    def _get_mutagen(self):
        """Lazy import mutagen."""
        if self._mutagen_module is None:
            try:
                import mutagen

                self._mutagen_module = mutagen
            except ImportError:
                raise ImportError(
                    "mutagen is required for audio metadata extraction. "
                    "Install with: pip install mutagen"
                )
        return self._mutagen_module

    @property
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        return [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse audio file from file path.

        Args:
            source: File path to audio file
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        # Convert to markdown
        markdown_content = self._convert_to_markdown(path)

        # Delegate to MarkdownParser
        result = await self._get_markdown_parser().parse_content(
            markdown_content, str(path), instruction, **kwargs
        )
        result.source_format = "audio"
        return result

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """
        Parse audio content.

        Note: This expects the actual audio binary content, which isn't directly
        usable. Use parse() with a file path instead.

        Args:
            content: Not directly supported for binary audio content
            source_path: Optional source path for reference
            instruction: Processing instruction
            **kwargs: Additional arguments

        Returns:
            ParseResult with document tree
        """
        if source_path and Path(source_path).exists():
            return await self.parse(source_path, instruction, **kwargs)
        raise ValueError(
            "AudioParser.parse_content() requires a valid source_path to the audio file"
        )

    def _convert_to_markdown(self, path: Path) -> str:
        """
        Convert audio file metadata to Markdown string.

        Args:
            path: Path to audio file

        Returns:
            Markdown formatted string
        """
        markdown_parts = []
        markdown_parts.append(f"# Audio File: {path.name}")

        try:
            mutagen = self._get_mutagen()
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

    def _format_size(self, size_bytes: int) -> str:
        """Format byte size to human readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human readable string."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"
