# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Audio parser with bounded semantic artifact generation."""

import asyncio
import base64
import io
import os
import re
import tempfile
import time
import wave
from pathlib import Path
from typing import List, Optional, Union

import openai

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import AUDIO_EXTENSIONS
from openviking_cli.utils.config.parser_config import AudioConfig
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


def _clean_text(value: str) -> str:
    """Normalize whitespace for sidecar content."""
    return re.sub(r"\s+", " ", value or "").strip()


def _truncate_text(value: str, limit: int) -> str:
    """Truncate text without leaving trailing whitespace."""
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_optional_number(value, *, digits: int = 2, suffix: str = "") -> str:
    """Format metadata fields consistently."""
    if value in (None, 0, 0.0, ""):
        return "unknown"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _transcript_success(text: Optional[str]) -> bool:
    """Return True when transcript text contains actual recognized speech."""
    if not text:
        return False
    lowered = text.lower().strip()
    return not lowered.startswith(
        (
            "audio transcription unavailable:",
            "audio transcription failed:",
            "audio transcription returned empty result.",
            "transcription disabled",
            "audio track extraction unavailable:",
        )
    )


class AudioParser(BaseParser):
    """Parser for standalone audio resources."""

    def __init__(self, config: Optional[AudioConfig] = None, **kwargs):
        self.config = config or AudioConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return AUDIO_EXTENSIONS

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs):
        start_time = time.time()
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {source}")

        audio_bytes = file_path.read_bytes()
        ext = file_path.suffix.lower()
        original_filename = file_path.name.replace(" ", "_")
        stem = file_path.stem.replace(" ", "_")
        ext_no_dot = ext[1:] if ext else "audio"
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")

        viking_fs = self._get_viking_fs()
        temp_uri = self._create_temp_uri()
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(temp_uri, exist_ok=True)
        await viking_fs.mkdir(root_dir_uri, exist_ok=True)
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", audio_bytes)

        self._validate_audio_signature(file_path, audio_bytes)
        metadata = self._extract_audio_metadata(file_path, audio_bytes)

        transcript_status = "disabled"
        transcript_text = "Transcription disabled by parser configuration."
        if self.config.enable_transcription:
            transcript_status, transcript_text = await self._build_transcript(audio_bytes)
        await viking_fs.write_file(f"{root_dir_uri}/transcript.md", transcript_text)

        description = self._build_description(
            original_filename=original_filename,
            metadata=metadata,
            transcript_status=transcript_status,
            transcript_text=transcript_text,
        )
        await viking_fs.write_file(f"{root_dir_uri}/description.md", description)

        node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            content_type="audio",
            auxiliary_files={
                "original": original_filename,
                "description": "description.md",
                "transcript": "transcript.md",
            },
            meta={
                **metadata,
                "content_type": "audio",
                "source_title": file_path.stem,
                "semantic_name": file_path.stem,
                "original_filename": original_filename,
                "file_size_bytes": len(audio_bytes),
                "transcript_status": transcript_status,
                "description_file": "description.md",
                "transcript_file": "transcript.md",
            },
        )
        await self._generate_semantic_info(
            node=node,
            description=description,
            viking_fs=viking_fs,
            has_transcript=_transcript_success(transcript_text),
            root_dir_uri=root_dir_uri,
        )

        result = create_parse_result(
            root=node,
            source_path=str(file_path),
            source_format="audio",
            parser_name="AudioParser",
            parse_time=time.time() - start_time,
            meta={
                "content_type": "audio",
                "format": metadata["format"],
                "transcript_status": transcript_status,
            },
        )
        result.temp_dir_path = temp_uri
        return result

    def _validate_audio_signature(self, file_path: Path, audio_bytes: bytes) -> None:
        """Validate common audio containers using lightweight signature checks."""
        audio_magic_bytes = {
            ".mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
            ".wav": [b"RIFF"],
            ".ogg": [b"OggS"],
            ".flac": [b"fLaC"],
            ".aac": [b"\xff\xf1", b"\xff\xf9"],
            ".m4a": [b"\x00\x00\x00", b"ftypM4A", b"ftypisom"],
            ".opus": [b"OggS"],
        }

        ext_lower = file_path.suffix.lower()
        magic_list = audio_magic_bytes.get(ext_lower, [])
        if not any(
            len(audio_bytes) >= len(magic) and audio_bytes.startswith(magic) for magic in magic_list
        ):
            raise ValueError(
                f"Invalid audio file: {file_path}. "
                f"File signature does not match expected format {ext_lower}"
            )

    def _extract_audio_metadata(self, file_path: Path, audio_bytes: bytes) -> dict:
        """Collect lightweight audio metadata with standard-library fallbacks."""
        metadata = {
            "duration": None,
            "sample_rate": None,
            "channels": None,
            "bitrate": None,
            "format": (file_path.suffix.lstrip(".") or "audio").lower(),
            "metadata_source": "extension",
        }

        if file_path.suffix.lower() == ".wav":
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
                    frame_rate = wav_file.getframerate()
                    frame_count = wav_file.getnframes()
                    channels = wav_file.getnchannels()
                    sample_width = wav_file.getsampwidth()
                    metadata.update(
                        {
                            "duration": frame_count / frame_rate if frame_rate else None,
                            "sample_rate": frame_rate or None,
                            "channels": channels or None,
                            "bitrate": frame_rate * channels * sample_width * 8
                            if frame_rate and channels and sample_width
                            else None,
                            "metadata_source": "wave",
                        }
                    )
            except Exception as exc:
                logger.debug("Failed to read WAV metadata for %s: %s", file_path, exc)

        try:
            from mutagen import File as MutagenFile

            audio_info = MutagenFile(file_path)
            info = getattr(audio_info, "info", None)
            if info is not None:
                metadata.update(
                    {
                        "duration": metadata["duration"] or getattr(info, "length", None),
                        "sample_rate": metadata["sample_rate"]
                        or getattr(info, "sample_rate", None),
                        "channels": metadata["channels"] or getattr(info, "channels", None),
                        "bitrate": metadata["bitrate"] or getattr(info, "bitrate", None),
                        "metadata_source": "mutagen",
                    }
                )
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Failed to read Mutagen metadata for %s: %s", file_path, exc)

        return metadata

    async def _build_transcript(self, audio_bytes: bytes) -> tuple[str, str]:
        """Generate the best available transcript artifact."""
        timestamped = await self._asr_transcribe_with_timestamps(
            audio_bytes, self.config.transcription_model
        )
        if _transcript_success(timestamped):
            return "timestamped", timestamped

        plain = await self._asr_transcribe(audio_bytes, self.config.transcription_model)
        if _transcript_success(plain):
            return "plain", plain

        return "unavailable", plain

    def _build_description(
        self,
        *,
        original_filename: str,
        metadata: dict,
        transcript_status: str,
        transcript_text: str,
    ) -> str:
        """Build markdown summary for the audio resource."""
        if _transcript_success(transcript_text):
            summary = (
                f"Audio transcript generated with `{transcript_status}` detail. "
                f"Excerpt: {_truncate_text(transcript_text, 320)}"
            )
        else:
            summary = (
                f"Audio file `{original_filename}` in {metadata['format'].upper()} format. "
                f"Automatic transcription is {transcript_status}."
            )

        parts = ["# Audio Summary", "", summary, "", "## Metadata"]
        parts.append(
            f"- Duration: {_format_optional_number(metadata.get('duration'), suffix='s')}"
        )
        parts.append(
            f"- Sample rate: {_format_optional_number(metadata.get('sample_rate'), suffix=' Hz')}"
        )
        parts.append(f"- Channels: {_format_optional_number(metadata.get('channels'))}")
        parts.append(f"- Bitrate: {_format_optional_number(metadata.get('bitrate'), suffix=' bps')}")
        parts.append(f"- Format: {metadata['format'].upper()}")
        parts.append(f"- Metadata source: {metadata.get('metadata_source', 'unknown')}")

        parts.extend(
            [
                "",
                "## Transcript Status",
                transcript_status,
                "",
                "## Transcript",
                transcript_text.strip(),
            ]
        )
        return "\n".join(parts).strip() + "\n"

    async def _asr_transcribe(self, audio_bytes: bytes, model: Optional[str]) -> str:
        """Generate audio transcription using an OpenAI-compatible ASR backend."""
        model_name = model or self.config.transcription_model
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not found, skip audio transcription")
            return "Audio transcription unavailable: OPENAI_API_KEY is not set."

        temp_file_path = None

        def _sync_transcribe() -> str:
            nonlocal temp_file_path
            client_kwargs = {"api_key": api_key}
            base_url = os.getenv("OPENAI_BASE_URL")
            if base_url:
                client_kwargs["base_url"] = base_url

            client = openai.OpenAI(**client_kwargs)
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_bytes)
                temp_file_path = temp_file.name

            with open(temp_file_path, "rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=model_name,
                    file=audio_file,
                    language=self.config.language,
                )

            if isinstance(response, dict):
                return str(response.get("text", "")).strip()
            return str(getattr(response, "text", "")).strip()

        try:
            text = await asyncio.get_event_loop().run_in_executor(None, _sync_transcribe)
            return text or "Audio transcription returned empty result."
        except Exception as exc:
            logger.exception("Audio transcription failed: %s", exc)
            return f"Audio transcription failed: {exc}"
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to cleanup temporary audio file %s: %s",
                        temp_file_path,
                        cleanup_error,
                    )

    async def _asr_transcribe_with_timestamps(
        self, audio_bytes: bytes, model: Optional[str]
    ) -> Optional[str]:
        """Extract transcription with timestamps from audio using ASR."""
        model_name = model or self.config.transcription_model
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not found, skip timestamp transcription")
            return None

        temp_file_path = None

        def _format_timestamp(seconds: float) -> str:
            total_seconds = max(0, int(float(seconds)))
            minutes, secs = divmod(total_seconds, 60)
            return f"{minutes:02d}:{secs:02d}"

        def _sync_transcribe_with_timestamps() -> Optional[str]:
            nonlocal temp_file_path
            client_kwargs = {"api_key": api_key}
            base_url = os.getenv("OPENAI_BASE_URL")
            if base_url:
                client_kwargs["base_url"] = base_url

            client = openai.OpenAI(**client_kwargs)
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_bytes)
                temp_file_path = temp_file.name

            with open(temp_file_path, "rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model=model_name,
                    file=audio_file,
                    language=self.config.language,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )

            segments = response.get("segments") if isinstance(response, dict) else getattr(
                response, "segments", None
            )
            if not segments:
                return None

            lines = []
            for segment in segments:
                if isinstance(segment, dict):
                    start = segment.get("start")
                    end = segment.get("end")
                    text = str(segment.get("text", "")).strip()
                else:
                    start = getattr(segment, "start", None)
                    end = getattr(segment, "end", None)
                    text = str(getattr(segment, "text", "")).strip()

                if start is None or end is None or not text:
                    continue
                lines.append(f"**[{_format_timestamp(start)} - {_format_timestamp(end)}]** {text}")

            return "\n\n".join(lines) if lines else None

        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, _sync_transcribe_with_timestamps
            )
        except Exception as exc:
            logger.exception("Timestamp transcription failed: %s", exc)
            return None
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to cleanup temporary audio file %s: %s",
                        temp_file_path,
                        cleanup_error,
                    )

    async def _generate_semantic_info(
        self,
        node: ResourceNode,
        description: str,
        viking_fs,
        has_transcript: bool,
        root_dir_uri: str,
    ) -> None:
        """Populate and persist the audio L0/L1 summaries."""
        if has_transcript:
            abstract = _truncate_text(description, 220)
        else:
            abstract = (
                f"Audio file {node.meta['original_filename']} "
                f"({_format_optional_number(node.meta.get('duration'), suffix='s')})"
            )

        overview_parts = [
            "## Content Summary",
            "",
            _truncate_text(description, 1800),
            "",
            "## Available Files",
            f"- {node.meta['original_filename']}: Original audio file",
            "- description.md: Semantic markdown summary for the audio",
            "- transcript.md: Transcript or fallback status for the audio track",
            "",
            "## Metadata",
            f"- Duration: {_format_optional_number(node.meta.get('duration'), suffix='s')}",
            f"- Sample rate: {_format_optional_number(node.meta.get('sample_rate'), suffix=' Hz')}",
            f"- Channels: {_format_optional_number(node.meta.get('channels'))}",
            f"- Bitrate: {_format_optional_number(node.meta.get('bitrate'), suffix=' bps')}",
            f"- Format: {node.meta['format'].upper()}",
            f"- Transcript status: {node.meta['transcript_status']}",
        ]
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
        """Parse audio from base64 content string."""
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
        except Exception as exc:
            logger.exception("Failed to parse audio content: %s", exc)
            raise ValueError(f"Invalid audio content: {exc}") from exc
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
