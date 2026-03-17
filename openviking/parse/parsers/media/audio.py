# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Audio parser with metadata extraction and Whisper transcription.

Features:
1. Speech-to-text transcription using Whisper API
2. Audio metadata extraction (duration, sample rate, channels) via mutagen
3. Timestamp alignment for transcribed text
4. Generate structured ResourceNode with transcript segments

Supported formats: MP3, WAV, OGG, FLAC, AAC, M4A, OPUS
"""

import io
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import AUDIO_EXTENSIONS
from openviking_cli.utils.config.parser_config import AudioConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Magic bytes for audio format validation
AUDIO_MAGIC_BYTES: Dict[str, List[bytes]] = {
    ".mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
    ".wav": [b"RIFF"],
    ".ogg": [b"OggS"],
    ".flac": [b"fLaC"],
    ".aac": [b"\xff\xf1", b"\xff\xf9"],
    ".m4a": [b"\x00\x00\x00", b"ftypM4A", b"ftypisom"],
    ".opus": [b"OggS"],
}


def _try_import_mutagen():
    """Lazily import mutagen, returning None if not installed."""
    try:
        import mutagen

        return mutagen
    except ImportError:
        return None


def _format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS or H:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _extract_metadata_mutagen(file_path: Path) -> Dict[str, Any]:
    """
    Extract audio metadata using mutagen.

    Args:
        file_path: Path to audio file

    Returns:
        Dictionary with duration, sample_rate, channels, bitrate, format
    """
    mutagen = _try_import_mutagen()
    if mutagen is None:
        logger.warning(
            "[AudioParser] mutagen not installed, skipping metadata extraction. "
            "Install with: pip install mutagen"
        )
        return {}

    try:
        audio = mutagen.File(str(file_path))
        if audio is None:
            logger.warning(f"[AudioParser] mutagen could not identify file: {file_path}")
            return {}

        meta: Dict[str, Any] = {}

        # Duration
        if hasattr(audio.info, "length"):
            meta["duration"] = round(audio.info.length, 2)

        # Sample rate
        if hasattr(audio.info, "sample_rate"):
            meta["sample_rate"] = audio.info.sample_rate

        # Channels
        if hasattr(audio.info, "channels"):
            meta["channels"] = audio.info.channels

        # Bitrate (bits per second)
        if hasattr(audio.info, "bitrate"):
            meta["bitrate"] = audio.info.bitrate

        return meta

    except Exception as e:
        logger.warning(f"[AudioParser] mutagen metadata extraction failed: {e}")
        return {}


class AudioParser(BaseParser):
    """
    Audio parser for audio files.

    Extracts metadata via mutagen and transcribes speech via Whisper API.
    Falls back to metadata-only output when transcription is unavailable.
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
        Parse audio file - extract metadata, transcribe via Whisper, build ResourceNode tree.

        Args:
            source: Audio file path
            instruction: Processing instruction
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with audio content tree

        Raises:
            FileNotFoundError: If source file does not exist
            ValueError: If file signature does not match expected format
        """
        from openviking.storage.viking_fs import get_viking_fs

        start_time = time.monotonic()

        # Convert to Path object
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {source}")

        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()

        # Read audio bytes
        audio_bytes = file_path.read_bytes()
        ext = file_path.suffix

        # Validate magic bytes
        self._validate_audio_bytes(audio_bytes, ext, file_path)

        from openviking_cli.utils.uri import VikingURI

        # Sanitize original filename (replace spaces with underscores)
        original_filename = file_path.name.replace(" ", "_")
        stem = file_path.stem.replace(" ", "_")
        ext_no_dot = ext[1:] if ext else ""
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(root_dir_uri, exist_ok=True)

        # Save original audio
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", audio_bytes)

        # Extract metadata via mutagen
        mutagen_meta = _extract_metadata_mutagen(file_path)
        duration = mutagen_meta.get("duration", 0)
        sample_rate = mutagen_meta.get("sample_rate", 0)
        channels = mutagen_meta.get("channels", 0)
        bitrate = mutagen_meta.get("bitrate", 0)
        format_str = ext_no_dot.lower()

        # Attempt transcription
        transcript_segments: List[Dict[str, Any]] = []
        full_transcript = ""
        warnings: List[str] = []

        if self.config.enable_transcription:
            try:
                transcript_segments = await self._asr_transcribe_with_timestamps(
                    audio_bytes, self.config.transcription_model, ext
                )
                if transcript_segments:
                    full_transcript = "\n".join(seg["text"] for seg in transcript_segments)
                else:
                    # Try plain transcription
                    full_transcript = await self._asr_transcribe(
                        audio_bytes, self.config.transcription_model, ext
                    )
            except Exception as e:
                logger.warning(f"[AudioParser] Transcription failed: {e}")
                warnings.append(f"Transcription unavailable: {e}")

        has_transcript = bool(full_transcript.strip())

        # Save transcript file if available
        if has_transcript:
            transcript_md = self._build_transcript_markdown(
                transcript_segments, full_transcript, file_path.stem
            )
            await viking_fs.write_file(f"{root_dir_uri}/transcript.md", transcript_md)

        # Build segment child nodes
        children = []
        if transcript_segments:
            for i, seg in enumerate(transcript_segments):
                seg_start = seg.get("start", 0)
                seg_end = seg.get("end", 0)
                seg_text = seg.get("text", "").strip()
                if not seg_text:
                    continue

                child = ResourceNode(
                    type=NodeType.SECTION,
                    title=f"segment_{i + 1:03d} ({_format_timestamp(seg_start)}-{_format_timestamp(seg_end)})",
                    level=1,
                    detail_file=None,
                    content_path=None,
                    children=[],
                    content_type="text",
                    meta={
                        "start": seg_start,
                        "end": seg_end,
                        "text": seg_text,
                    },
                )
                children.append(child)

        # Build root node meta
        root_meta: Dict[str, Any] = {
            "duration": duration,
            "sample_rate": sample_rate,
            "channels": channels,
            "bitrate": bitrate,
            "format": format_str,
            "content_type": "audio",
            "source_title": file_path.stem,
            "semantic_name": file_path.stem,
            "original_filename": original_filename,
            "has_transcript": has_transcript,
            "segment_count": len(children),
        }

        # Create root ResourceNode
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            detail_file=None,
            content_path=None,
            children=children,
            content_type="audio",
            meta=root_meta,
        )

        # Generate semantic info (L0 abstract, L1 overview)
        description = full_transcript if has_transcript else f"Audio file: {file_path.name}"
        await self._generate_semantic_info(root_node, description, viking_fs, has_transcript)

        if not has_transcript:
            warnings.append(
                "No transcript available. Metadata-only output. "
                "Configure Whisper API or install openai-whisper for transcription."
            )

        parse_time = time.monotonic() - start_time

        return ParseResult(
            root=root_node,
            source_path=str(file_path),
            temp_dir_path=temp_uri,
            source_format="audio",
            parser_name="AudioParser",
            parse_time=parse_time,
            meta={"content_type": "audio", "format": format_str},
            warnings=warnings,
        )

    def _validate_audio_bytes(self, audio_bytes: bytes, ext: str, file_path: Path) -> None:
        """Validate audio file using magic bytes."""
        ext_lower = ext.lower()
        magic_list = AUDIO_MAGIC_BYTES.get(ext_lower, [])
        for magic in magic_list:
            if len(audio_bytes) >= len(magic) and audio_bytes.startswith(magic):
                return
        # If no magic bytes defined for this extension, skip validation
        if not magic_list:
            return
        raise ValueError(
            f"Invalid audio file: {file_path}. "
            f"File signature does not match expected format {ext_lower}"
        )

    async def _asr_transcribe(
        self, audio_bytes: bytes, model: Optional[str], ext: str = ".mp3"
    ) -> str:
        """
        Transcribe audio using Whisper API via OpenAI client.

        Args:
            audio_bytes: Audio binary data
            model: Whisper model name
            ext: File extension for mime type hint

        Returns:
            Transcription text
        """
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            import openai

            client = openai.AsyncOpenAI(
                api_key=config.llm.api_key if hasattr(config, "llm") else None,
            )

            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = f"audio{ext}"

            response = await client.audio.transcriptions.create(
                model=model or "whisper-1",
                file=audio_file,
                language=self.config.language,
            )

            return response.text

        except Exception as e:
            logger.warning(f"[AudioParser._asr_transcribe] Whisper API call failed: {e}")
            return ""

    async def _asr_transcribe_with_timestamps(
        self, audio_bytes: bytes, model: Optional[str], ext: str = ".mp3"
    ) -> List[Dict[str, Any]]:
        """
        Transcribe audio with timestamps using Whisper API verbose_json format.

        Args:
            audio_bytes: Audio binary data
            model: Whisper model name
            ext: File extension

        Returns:
            List of segment dicts with keys: start, end, text
        """
        try:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            import openai

            client = openai.AsyncOpenAI(
                api_key=config.llm.api_key if hasattr(config, "llm") else None,
            )

            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = f"audio{ext}"

            response = await client.audio.transcriptions.create(
                model=model or "whisper-1",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                language=self.config.language,
            )

            segments = []
            if hasattr(response, "segments") and response.segments:
                for seg in response.segments:
                    segments.append(
                        {
                            "start": seg.get("start", 0)
                            if isinstance(seg, dict)
                            else getattr(seg, "start", 0),
                            "end": seg.get("end", 0)
                            if isinstance(seg, dict)
                            else getattr(seg, "end", 0),
                            "text": seg.get("text", "")
                            if isinstance(seg, dict)
                            else getattr(seg, "text", ""),
                        }
                    )

            return segments

        except Exception as e:
            logger.warning(
                f"[AudioParser._asr_transcribe_with_timestamps] Whisper API call failed: {e}"
            )
            return []

    def _build_transcript_markdown(
        self,
        segments: List[Dict[str, Any]],
        full_transcript: str,
        title: str,
    ) -> str:
        """
        Build a markdown transcript file from segments or plain text.

        Args:
            segments: Timestamped transcript segments
            full_transcript: Full transcript text (used if no segments)
            title: Audio file title

        Returns:
            Markdown-formatted transcript
        """
        parts = [f"# Transcript: {title}\n"]

        if segments:
            for seg in segments:
                start = _format_timestamp(seg.get("start", 0))
                end = _format_timestamp(seg.get("end", 0))
                text = seg.get("text", "").strip()
                if text:
                    parts.append(f"**[{start} - {end}]** {text}\n")
        elif full_transcript.strip():
            parts.append(full_transcript.strip())
            parts.append("")

        return "\n".join(parts)

    async def _generate_semantic_info(
        self,
        node: ResourceNode,
        description: str,
        viking_fs: Any,
        has_transcript: bool,
    ) -> None:
        """
        Generate L0 abstract and L1 overview for the audio resource.

        Args:
            node: ResourceNode to update
            description: Audio transcript or description text
            viking_fs: VikingFS instance
            has_transcript: Whether transcript is available
        """
        # L0 abstract: short summary (< 256 chars)
        if has_transcript and len(description) > 50:
            first_sentence_end = description.find(".", 20)
            if 20 < first_sentence_end < 256:
                abstract = description[: first_sentence_end + 1]
            else:
                abstract = description[:253] + "..." if len(description) > 256 else description
        else:
            abstract = description[:253] + "..." if len(description) > 256 else description

        # L1 overview
        overview_parts = [
            "## Content Summary\n",
            abstract,
            "\n\n## Available Files\n",
            (
                f"- {node.meta['original_filename']}: Original audio file "
                f"({node.meta['duration']}s, {node.meta['sample_rate']}Hz, "
                f"{node.meta['channels']}ch, {node.meta['format'].upper()} format)\n"
            ),
        ]

        if has_transcript:
            overview_parts.append("- transcript.md: Timestamped transcript from the audio\n")

        overview_parts.append("\n## Usage\n")
        overview_parts.append("### Play Audio\n")
        overview_parts.append("```python\n")
        overview_parts.append("audio_bytes = await audio_resource.play()\n")
        overview_parts.append("# Returns: Audio file binary data\n")
        overview_parts.append("```\n\n")

        if has_transcript:
            overview_parts.append("### Get Timestamped Transcript\n")
            overview_parts.append("```python\n")
            overview_parts.append("timestamps = await audio_resource.timestamps()\n")
            overview_parts.append("# Returns: FileContent object or None\n")
            overview_parts.append("```\n\n")

        overview_parts.append("### Get Audio Metadata\n")
        overview_parts.append("```python\n")
        overview_parts.append(
            f"duration = audio_resource.get_duration()  # {node.meta['duration']}s\n"
        )
        overview_parts.append(
            f"sample_rate = audio_resource.get_sample_rate()  # {node.meta['sample_rate']}Hz\n"
        )
        overview_parts.append(
            f"channels = audio_resource.get_channels()  # {node.meta['channels']}\n"
        )
        overview_parts.append(f'format = audio_resource.get_format()  # "{node.meta["format"]}"\n')
        overview_parts.append("```\n")

        overview = "".join(overview_parts)

        node.meta["abstract"] = abstract
        node.meta["overview"] = overview

    async def parse_content(
        self,
        content: str,
        source_path: Optional[str] = None,
        instruction: str = "",
        **kwargs,
    ) -> ParseResult:
        """
        Parse audio from content string - Not yet implemented.

        Args:
            content: Audio content (base64 or binary string)
            source_path: Optional source path for metadata
            instruction: Processing instruction
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with audio content

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        raise NotImplementedError("Audio parsing from content not yet implemented")
