# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Audio parser with Whisper ASR integration.

Features:
1. Speech-to-text transcription using OpenAI Whisper API
2. Audio metadata extraction (duration, sample rate, channels)
3. Timestamp alignment for transcribed text
4. Generate structured ResourceNode with transcript

Example workflow:
    1. Load audio file
    2. Extract metadata (duration, format, sample rate)
    3. Transcribe speech to text using Whisper
    4. Create ResourceNode with:
       - type: NodeType.ROOT
       - children: sections for each speaker/timestamp
       - meta: audio metadata and timestamps
    6. Return ParseResult

Supported formats: MP3, WAV, OGG, FLAC, AAC, M4A
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import AUDIO_EXTENSIONS
from openviking_cli.utils.config.parser_config import AudioConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Map audio extensions to MIME suffixes for the Whisper API
_EXT_TO_SUFFIX = {
    ".mp3": ".mp3",
    ".wav": ".wav",
    ".ogg": ".ogg",
    ".flac": ".flac",
    ".aac": ".aac",
    ".m4a": ".m4a",
    ".opus": ".opus",
}


def _format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    total = int(seconds)
    hrs, remainder = divmod(total, 3600)
    mins, secs = divmod(remainder, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


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

        # Sanitize original filename (replace spaces with underscores)
        original_filename = file_path.name.replace(" ", "_")
        # Root directory name: filename stem + _ + extension (without dot)
        stem = file_path.stem.replace(" ", "_")
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

        # Phase 2: ASR transcription (when enabled)
        transcript_text = None
        timestamp_text = None
        has_transcript = False

        if self.config.enable_transcription:
            logger.info(
                f"[AudioParser.parse] Starting ASR transcription for {file_path.name} "
                f"with model={self.config.transcription_model}"
            )
            transcript_text = await self._asr_transcribe(
                audio_bytes, model=self.config.transcription_model
            )
            timestamp_text = await self._asr_transcribe_with_timestamps(
                audio_bytes, model=self.config.transcription_model
            )

            if transcript_text and not transcript_text.startswith("Audio transcription failed"):
                has_transcript = True
                # Save transcript as markdown file
                transcript_md = f"# Transcript\n\n{transcript_text}\n"
                await viking_fs.write_file(
                    f"{root_dir_uri}/transcript.md",
                    transcript_md,
                )

            if timestamp_text:
                await viking_fs.write_file(
                    f"{root_dir_uri}/transcript_timestamps.md",
                    timestamp_text,
                )

        # Create ResourceNode
        children = []
        if has_transcript and transcript_text:
            children.append(
                ResourceNode(
                    type=NodeType.SECTION,
                    title="Transcript",
                    level=1,
                    detail_file="transcript.md",
                    content_path=f"{root_dir_uri}/transcript.md",
                    children=[],
                    meta={"content_type": "transcript"},
                )
            )
        if timestamp_text:
            children.append(
                ResourceNode(
                    type=NodeType.SECTION,
                    title="Transcript with Timestamps",
                    level=1,
                    detail_file="transcript_timestamps.md",
                    content_path=f"{root_dir_uri}/transcript_timestamps.md",
                    children=[],
                    meta={"content_type": "transcript_timestamps"},
                )
            )

        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            detail_file=None,
            content_path=None,
            children=children,
            meta={
                "duration": duration,
                "sample_rate": sample_rate,
                "channels": channels,
                "format": format_str.lower(),
                "content_type": "audio",
                "source_title": file_path.stem,
                "semantic_name": file_path.stem,
                "original_filename": original_filename,
                "has_transcript": has_transcript,
            },
        )

        # Phase 3: Build directory structure (handled by TreeBuilder)
        return ParseResult(
            root=root_node,
            source_path=str(file_path),
            temp_dir_path=temp_uri,
            source_format="audio",
            parser_name="AudioParser",
            meta={
                "content_type": "audio",
                "format": format_str.lower(),
                "has_transcript": has_transcript,
            },
        )

    def _get_openai_client(self):
        """
        Get an OpenAI client for Whisper ASR calls.

        Uses the VLM config's API key/base when the provider is OpenAI-compatible,
        otherwise falls back to OPENAI_API_KEY from the environment.

        Returns:
            openai.OpenAI client instance
        """
        try:
            import openai
        except ImportError:
            raise ImportError("Please install openai: pip install openai")

        from openviking_cli.utils.config import get_openviking_config

        client_kwargs = {}
        try:
            vlm_config = get_openviking_config().vlm_config
            if vlm_config and vlm_config.api_key:
                client_kwargs["api_key"] = vlm_config.api_key
            if vlm_config and vlm_config.api_base:
                client_kwargs["base_url"] = vlm_config.api_base
        except Exception:
            pass

        if "api_key" not in client_kwargs:
            api_key = os.environ.get("OPENAI_API_KEY")
            if api_key:
                client_kwargs["api_key"] = api_key

        return openai.OpenAI(**client_kwargs)

    async def _asr_transcribe(self, audio_bytes: bytes, model: Optional[str]) -> str:
        """
        Generate audio transcription using OpenAI Whisper API.

        Args:
            audio_bytes: Audio binary data
            model: ASR model name (defaults to config.transcription_model)

        Returns:
            Audio transcription in markdown format
        """
        import asyncio

        model = model or self.config.transcription_model or "whisper-1"

        def _transcribe_sync() -> str:
            client = self._get_openai_client()
            suffix = ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                with open(tmp_path, "rb") as audio_file:
                    transcript = client.audio.transcriptions.create(
                        model=model,
                        file=audio_file,
                    )
                return transcript.text
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        try:
            text = await asyncio.get_event_loop().run_in_executor(None, _transcribe_sync)
            logger.info(
                f"[AudioParser._asr_transcribe] Whisper transcription received, "
                f"length: {len(text)}, preview: {text[:256]}"
            )
            return text.strip()
        except Exception as e:
            logger.error(
                f"[AudioParser._asr_transcribe] Whisper transcription failed: {e}",
                exc_info=True,
            )
            return (
                "Audio transcription failed\n\n"
                f"ASR transcription using model '{model}' encountered an error: {e}"
            )

    async def _asr_transcribe_with_timestamps(
        self, audio_bytes: bytes, model: Optional[str]
    ) -> Optional[str]:
        """
        Extract transcription with timestamps from audio using OpenAI Whisper API.

        Uses the verbose_json response format to obtain word-level or segment-level
        timestamps, then formats them as a markdown transcript.

        Args:
            audio_bytes: Audio binary data
            model: ASR model name (defaults to config.transcription_model)

        Returns:
            Transcript with timestamps in markdown format, or None if not available
        """
        import asyncio

        model = model or self.config.transcription_model or "whisper-1"

        def _transcribe_verbose_sync() -> Optional[str]:
            client = self._get_openai_client()
            suffix = ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                with open(tmp_path, "rb") as audio_file:
                    transcript = client.audio.transcriptions.create(
                        model=model,
                        file=audio_file,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"],
                    )

                segments = getattr(transcript, "segments", None)
                if not segments:
                    return None

                lines = ["## Transcript with Timestamps\n"]
                for seg in segments:
                    start = seg.get("start", 0) if isinstance(seg, dict) else getattr(seg, "start", 0)
                    end = seg.get("end", 0) if isinstance(seg, dict) else getattr(seg, "end", 0)
                    text = seg.get("text", "") if isinstance(seg, dict) else getattr(seg, "text", "")
                    start_fmt = _format_timestamp(start)
                    end_fmt = _format_timestamp(end)
                    lines.append(f"**[{start_fmt} - {end_fmt}]** {text.strip()}\n")

                return "\n".join(lines)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _transcribe_verbose_sync
            )
            if result:
                logger.info(
                    f"[AudioParser._asr_transcribe_with_timestamps] "
                    f"Timestamped transcript received, length: {len(result)}"
                )
            return result
        except Exception as e:
            logger.error(
                f"[AudioParser._asr_transcribe_with_timestamps] "
                f"Whisper timestamp transcription failed: {e}",
                exc_info=True,
            )
            return None

    async def _generate_semantic_info(
        self, node: ResourceNode, description: str, viking_fs, has_transcript: bool
    ):
        """
        Phase 2: Generate abstract and overview.

        Args:
            node: ResourceNode to update
            description: Audio description
            viking_fs: VikingFS instance
            has_transcript: Whether transcript file exists
        """
        # Generate abstract (short summary, < 100 tokens)
        abstract = description[:200] if len(description) > 200 else description

        # Generate overview (content summary + file list + usage instructions)
        overview_parts = [
            "## Content Summary\n",
            description,
            "\n\n## Available Files\n",
            f"- {node.meta['original_filename']}: Original audio file ({node.meta['duration']}s, {node.meta['sample_rate']}Hz, {node.meta['channels']}ch, {node.meta['format'].upper()} format)\n",
        ]

        if has_transcript:
            overview_parts.append("- transcript.md: Transcript with timestamps from the audio\n")

        overview_parts.append("\n## Usage\n")
        overview_parts.append("### Play Audio\n")
        overview_parts.append("```python\n")
        overview_parts.append("audio_bytes = await audio_resource.play()\n")
        overview_parts.append("# Returns: Audio file binary data\n")
        overview_parts.append("# Purpose: Play or save the audio\n")
        overview_parts.append("```\n\n")

        if has_transcript:
            overview_parts.append("### Get Timestamps Transcript\n")
            overview_parts.append("```python\n")
            overview_parts.append("timestamps = await audio_resource.timestamps()\n")
            overview_parts.append("# Returns: FileContent object or None\n")
            overview_parts.append("# Purpose: Extract timestamped transcript from the audio\n")
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

        # Store in node meta
        node.meta["abstract"] = abstract
        node.meta["overview"] = overview

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Parse audio from base64-encoded content string.

        Decodes the base64 content, writes it to a temporary file, and
        delegates to the file-based ``parse()`` method.

        Args:
            content: Base64-encoded audio content
            source_path: Optional source path for metadata
            **kwargs: Additional parsing parameters

        Returns:
            ParseResult with audio content
        """
        import base64

        audio_bytes = base64.b64decode(content)

        suffix = ".mp3"
        if source_path:
            p = Path(source_path)
            if p.suffix.lower() in _EXT_TO_SUFFIX:
                suffix = p.suffix.lower()

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            return await self.parse(tmp_path, instruction=instruction, **kwargs)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
