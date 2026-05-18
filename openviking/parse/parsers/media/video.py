# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Video parser with bounded preview and transcript support."""

import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Union

from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.audio import AudioParser, _format_optional_number, _truncate_text
from openviking.parse.parsers.media.constants import VIDEO_EXTENSIONS
from openviking.parse.parsers.media.image import ImageParser
from openviking_cli.utils.config.parser_config import VideoConfig
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


class VideoParser(BaseParser):
    """Parser for standalone video resources."""

    def __init__(self, config: Optional[VideoConfig] = None, **kwargs):
        self.config = config or VideoConfig()

    @property
    def supported_extensions(self) -> List[str]:
        return VIDEO_EXTENSIONS

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs):
        start_time = time.time()
        file_path = Path(source) if isinstance(source, str) else source
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {source}")

        video_bytes = file_path.read_bytes()
        ext = file_path.suffix.lower()
        original_filename = file_path.name.replace(" ", "_")
        stem = file_path.stem.replace(" ", "_")
        ext_no_dot = ext[1:] if ext else "video"
        root_dir_name = VikingURI.sanitize_segment(f"{stem}_{ext_no_dot}")

        viking_fs = self._get_viking_fs()
        temp_uri = self._create_temp_uri()
        root_dir_uri = f"{temp_uri}/{root_dir_name}"
        await viking_fs.mkdir(temp_uri, exist_ok=True)
        await viking_fs.mkdir(root_dir_uri, exist_ok=True)
        await viking_fs.write_file_bytes(f"{root_dir_uri}/{original_filename}", video_bytes)

        self._validate_video_signature(file_path, video_bytes)
        metadata = await self._extract_video_metadata(file_path)

        preview_bytes = None
        if self.config.extract_frames:
            preview_bytes = await self._extract_preview_frame(file_path)
            if preview_bytes:
                await viking_fs.write_file_bytes(f"{root_dir_uri}/preview.png", preview_bytes)

        transcript_status = "disabled"
        transcript_text = "Transcription disabled by parser configuration."
        if self.config.enable_transcription:
            transcript_status, transcript_text = await self._build_video_transcript(file_path)
        await viking_fs.write_file(f"{root_dir_uri}/transcript.md", transcript_text)

        preview_description = None
        if preview_bytes:
            preview_description = await self._describe_preview_frame(
                preview_bytes, instruction=instruction
            )

        description = self._build_description(
            original_filename=original_filename,
            metadata=metadata,
            preview_description=preview_description,
            transcript_status=transcript_status,
            transcript_text=transcript_text,
            has_preview=bool(preview_bytes),
        )
        await viking_fs.write_file(f"{root_dir_uri}/description.md", description)

        node = ResourceNode(
            type=NodeType.ROOT,
            title=file_path.stem,
            level=0,
            content_type="video",
            auxiliary_files={
                "original": original_filename,
                "description": "description.md",
                "transcript": "transcript.md",
                **({"preview": "preview.png"} if preview_bytes else {}),
            },
            meta={
                **metadata,
                "content_type": "video",
                "source_title": file_path.stem,
                "semantic_name": file_path.stem,
                "original_filename": original_filename,
                "file_size_bytes": len(video_bytes),
                "has_preview_frame": bool(preview_bytes),
                "transcript_status": transcript_status,
                "description_file": "description.md",
                "transcript_file": "transcript.md",
                "preview_file": "preview.png" if preview_bytes else None,
            },
        )
        await self._generate_semantic_info(
            node=node,
            description=description,
            viking_fs=viking_fs,
            has_key_frames=bool(preview_bytes),
            root_dir_uri=root_dir_uri,
        )

        result = create_parse_result(
            root=node,
            source_path=str(file_path),
            source_format="video",
            parser_name="VideoParser",
            parse_time=time.time() - start_time,
            meta={
                "content_type": "video",
                "format": metadata["format"],
                "has_preview_frame": bool(preview_bytes),
                "transcript_status": transcript_status,
            },
        )
        result.temp_dir_path = temp_uri
        return result

    def _validate_video_signature(self, file_path: Path, video_bytes: bytes) -> None:
        """Validate common video containers with simple signature checks."""
        video_magic_bytes = {
            ".mp4": [b"\x00\x00\x00", b"ftyp"],
            ".avi": [b"RIFF"],
            ".mov": [b"\x00\x00\x00", b"ftyp"],
            ".mkv": [b"\x1a\x45\xdf\xa3"],
            ".webm": [b"\x1a\x45\xdf\xa3"],
            ".flv": [b"FLV"],
            ".wmv": [b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"],
        }

        ext_lower = file_path.suffix.lower()
        magic_list = video_magic_bytes.get(ext_lower, [])
        if not any(
            len(video_bytes) >= len(magic) and video_bytes.startswith(magic) for magic in magic_list
        ):
            raise ValueError(
                f"Invalid video file: {file_path}. "
                f"File signature does not match expected format {ext_lower}"
            )

    async def _extract_video_metadata(self, file_path: Path) -> dict:
        """Probe video metadata with ffprobe or cv2 when available."""
        metadata = {
            "duration": None,
            "width": None,
            "height": None,
            "fps": None,
            "frame_count": None,
            "format": (file_path.suffix.lstrip(".") or "video").lower(),
            "metadata_source": "extension",
        }

        ffprobe_path = shutil.which("ffprobe")
        if ffprobe_path:
            try:
                probed = await asyncio.to_thread(self._probe_with_ffprobe, ffprobe_path, file_path)
                metadata.update({k: v for k, v in probed.items() if v not in (None, "", 0, 0.0)})
                metadata["metadata_source"] = "ffprobe"
                return metadata
            except Exception as exc:
                logger.debug("ffprobe metadata extraction failed for %s: %s", file_path, exc)

        try:
            import cv2

            def _probe_with_cv2() -> dict:
                cap = cv2.VideoCapture(str(file_path))
                if not cap.isOpened():
                    return {}
                fps = cap.get(cv2.CAP_PROP_FPS) or None
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or None
                width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or None
                height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or None
                cap.release()
                return {
                    "fps": fps,
                    "frame_count": frame_count,
                    "width": int(width) if width else None,
                    "height": int(height) if height else None,
                    "duration": (frame_count / fps) if fps and frame_count else None,
                }

            probed = await asyncio.to_thread(_probe_with_cv2)
            if probed:
                metadata.update({k: v for k, v in probed.items() if v not in (None, "", 0, 0.0)})
                metadata["metadata_source"] = "cv2"
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("cv2 metadata extraction failed for %s: %s", file_path, exc)

        return metadata

    def _probe_with_ffprobe(self, ffprobe_path: str, file_path: Path) -> dict:
        """Probe metadata using ffprobe JSON output."""
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
        video_stream = next(
            (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
            {},
        )
        frame_rate_raw = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
        fps = None
        if frame_rate_raw and frame_rate_raw not in {"0/0", "0"}:
            num, _, den = frame_rate_raw.partition("/")
            fps = float(num) / float(den or "1")

        duration = video_stream.get("duration") or payload.get("format", {}).get("duration")
        frame_count = video_stream.get("nb_frames")
        return {
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "fps": fps,
            "duration": float(duration) if duration else None,
            "frame_count": int(frame_count) if frame_count and str(frame_count).isdigit() else None,
        }

    async def _extract_preview_frame(self, file_path: Path) -> Optional[bytes]:
        """Extract a single preview frame without a heavy video pipeline."""
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            try:
                return await asyncio.to_thread(self._extract_preview_with_ffmpeg, ffmpeg_path, file_path)
            except Exception as exc:
                logger.debug("ffmpeg preview extraction failed for %s: %s", file_path, exc)

        try:
            import cv2

            def _extract_with_cv2() -> Optional[bytes]:
                cap = cv2.VideoCapture(str(file_path))
                if not cap.isOpened():
                    return None
                ok, frame = cap.read()
                cap.release()
                if not ok:
                    return None
                ok, encoded = cv2.imencode(".png", frame)
                return bytes(encoded) if ok else None

            return await asyncio.to_thread(_extract_with_cv2)
        except ImportError:
            return None
        except Exception as exc:
            logger.debug("cv2 preview extraction failed for %s: %s", file_path, exc)
            return None

    def _extract_preview_with_ffmpeg(self, ffmpeg_path: str, file_path: Path) -> Optional[bytes]:
        """Extract one PNG frame using ffmpeg."""
        result = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                str(file_path),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
        return result.stdout or None

    async def _build_video_transcript(self, file_path: Path) -> tuple[str, str]:
        """Extract the audio track when possible and reuse AudioParser ASR helpers."""
        audio_bytes = await self._extract_audio_track(file_path)
        if not audio_bytes:
            return "unavailable", "Audio track extraction unavailable: ffmpeg is not installed or failed."

        audio_parser = AudioParser()
        timestamped = await audio_parser._asr_transcribe_with_timestamps(audio_bytes, None)
        if timestamped:
            return "timestamped", timestamped

        plain = await audio_parser._asr_transcribe(audio_bytes, None)
        if plain:
            return ("plain" if "unavailable" not in plain.lower() and "failed" not in plain.lower() else "unavailable"), plain
        return "unavailable", "Audio transcription unavailable: extracted audio track produced no transcript."

    async def _extract_audio_track(self, file_path: Path) -> Optional[bytes]:
        """Extract a WAV audio track using ffmpeg when available."""
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            return None

        def _extract() -> Optional[bytes]:
            result = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    str(file_path),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-f",
                    "wav",
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
            )
            return result.stdout or None

        try:
            return await asyncio.to_thread(_extract)
        except Exception as exc:
            logger.debug("ffmpeg audio extraction failed for %s: %s", file_path, exc)
            return None

    async def _describe_preview_frame(
        self,
        preview_bytes: bytes,
        instruction: str = "",
    ) -> Optional[str]:
        """Describe the preview frame through the existing image VLM helper."""
        image_parser = ImageParser()
        if not image_parser._is_vlm_available():
            return None
        description = await image_parser._vlm_describe(
            preview_bytes,
            model=None,
            instruction=instruction,
        )
        return description or None

    def _build_description(
        self,
        *,
        original_filename: str,
        metadata: dict,
        preview_description: Optional[str],
        transcript_status: str,
        transcript_text: str,
        has_preview: bool,
    ) -> str:
        """Build markdown summary for the video resource."""
        if preview_description:
            summary = f"Preview-frame description: {_truncate_text(preview_description, 320)}"
        elif transcript_text and "unavailable" not in transcript_text.lower():
            summary = f"Video audio transcript excerpt: {_truncate_text(transcript_text, 320)}"
        else:
            summary = (
                f"Video file `{original_filename}` in {metadata['format'].upper()} format "
                "with lightweight metadata-only analysis."
            )

        parts = ["# Video Summary", "", summary, "", "## Metadata"]
        parts.append(f"- Duration: {_format_optional_number(metadata.get('duration'), suffix='s')}")
        resolution = (
            f"{metadata.get('width')}x{metadata.get('height')}"
            if metadata.get("width") and metadata.get("height")
            else "unknown"
        )
        parts.append(f"- Resolution: {resolution}")
        parts.append(f"- FPS: {_format_optional_number(metadata.get('fps'))}")
        parts.append(f"- Frame count: {_format_optional_number(metadata.get('frame_count'))}")
        parts.append(f"- Format: {metadata['format'].upper()}")
        parts.append(f"- Metadata source: {metadata.get('metadata_source', 'unknown')}")
        parts.append(f"- Preview frame extracted: {'yes' if has_preview else 'no'}")

        parts.extend(
            [
                "",
                "## Audio Transcript Status",
                transcript_status,
                "",
                "## Audio Transcript",
                transcript_text.strip(),
            ]
        )
        if preview_description:
            parts.extend(["", "## Preview Frame Description", preview_description.strip()])
        return "\n".join(parts).strip() + "\n"

    async def _generate_semantic_info(
        self,
        node: ResourceNode,
        description: str,
        viking_fs,
        has_key_frames: bool,
        root_dir_uri: str,
    ) -> None:
        """Populate and persist the video L0/L1 summaries."""
        abstract = _truncate_text(description, 220) or f"Video: {node.meta['original_filename']}"
        overview_parts = [
            "## Content Summary",
            "",
            _truncate_text(description, 1800),
            "",
            "## Available Files",
            f"- {node.meta['original_filename']}: Original video file",
            "- description.md: Semantic markdown summary for the video",
            "- transcript.md: Transcript or fallback status for the audio track",
        ]
        if has_key_frames:
            overview_parts.append("- preview.png: Single extracted preview frame")
        overview_parts.extend(
            [
                "",
                "## Metadata",
                f"- Duration: {_format_optional_number(node.meta.get('duration'), suffix='s')}",
                "- Resolution: "
                + (
                    f"{node.meta.get('width')}x{node.meta.get('height')}"
                    if node.meta.get("width") and node.meta.get("height")
                    else "unknown"
                ),
                f"- FPS: {_format_optional_number(node.meta.get('fps'))}",
                f"- Frame count: {_format_optional_number(node.meta.get('frame_count'))}",
                f"- Format: {node.meta['format'].upper()}",
                f"- Transcript status: {node.meta['transcript_status']}",
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
        raise NotImplementedError("Video parsing from content not yet implemented")
