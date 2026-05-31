# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for multimodal parser semantic artifact generation."""

import asyncio
import io
import wave
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

from PIL import Image

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.audio import AudioParser
from openviking.parse.parsers.media.image import ImageParser
from openviking.parse.parsers.media.video import VideoParser
from openviking_cli.utils.config.parser_config import AudioConfig, ImageConfig, VideoConfig


class FakeVikingFS:
    """Minimal VikingFS mock for parser temp outputs."""

    def __init__(self):
        self.dirs: List[str] = []
        self.files: Dict[str, bytes] = {}
        self._temp_counter = 0

    async def mkdir(self, uri: str, exist_ok: bool = False, **kwargs) -> None:
        if uri not in self.dirs:
            self.dirs.append(uri)

    async def write_file(self, uri: str, content: Any) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[uri] = content

    async def write_file_bytes(self, uri: str, content: bytes) -> None:
        self.files[uri] = content

    def create_temp_uri(self) -> str:
        self._temp_counter += 1
        return f"viking://temp/media_{self._temp_counter}"


def _create_png_bytes(width: int = 48, height: int = 24) -> bytes:
    """Create a simple PNG fixture."""
    img = Image.new("RGB", (width, height), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_wav_bytes(duration_seconds: float = 0.2, sample_rate: int = 8000) -> bytes:
    """Create a small valid WAV file in memory."""
    frame_count = int(duration_seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return buf.getvalue()


def _decode(fake_fs: FakeVikingFS, uri: str) -> str:
    """Decode UTF-8 content stored in the fake FS."""
    return fake_fs.files[uri].decode("utf-8")


def test_image_parser_writes_semantic_artifacts(tmp_path: Path) -> None:
    """Image parsing should emit semantic sidecars and OCR output."""
    file_path = tmp_path / "receipt.png"
    file_path.write_bytes(_create_png_bytes())
    fake_fs = FakeVikingFS()

    with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
        parser = ImageParser(config=ImageConfig(enable_ocr=True, enable_vlm=False))
        with patch.object(
            parser,
            "_ocr_extract",
            new=AsyncMock(return_value="Store receipt total: $42.00"),
        ):
            result = asyncio.run(parser.parse(file_path))

    base_uri = f"{result.temp_dir_path}/receipt_png"
    assert f"{base_uri}/receipt.png" in fake_fs.files
    assert f"{base_uri}/description.md" in fake_fs.files
    assert f"{base_uri}/ocr.md" in fake_fs.files
    assert f"{base_uri}/.abstract.md" in fake_fs.files
    assert f"{base_uri}/.overview.md" in fake_fs.files
    assert result.root.meta["has_ocr"] is True

    description = _decode(fake_fs, f"{base_uri}/description.md")
    overview = _decode(fake_fs, f"{base_uri}/.overview.md")

    assert "OCR Text" in description
    assert "Store receipt total: $42.00" in description
    assert "description.md" in overview
    assert "ocr.md" in overview


def test_audio_parser_writes_transcript_fallback_artifacts(tmp_path: Path) -> None:
    """Audio parsing should write transcript sidecars even when ASR is unavailable."""
    file_path = tmp_path / "meeting.wav"
    file_path.write_bytes(_create_wav_bytes())
    fake_fs = FakeVikingFS()

    with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
        parser = AudioParser(config=AudioConfig(enable_transcription=True))
        with patch.object(
            parser,
            "_build_transcript",
            new=AsyncMock(
                return_value=("unavailable", "Audio transcription unavailable: OPENAI_API_KEY is not set.")
            ),
        ):
            result = asyncio.run(parser.parse(file_path))

    base_uri = f"{result.temp_dir_path}/meeting_wav"
    assert f"{base_uri}/meeting.wav" in fake_fs.files
    assert f"{base_uri}/transcript.md" in fake_fs.files
    assert f"{base_uri}/description.md" in fake_fs.files
    assert f"{base_uri}/.abstract.md" in fake_fs.files
    assert f"{base_uri}/.overview.md" in fake_fs.files
    assert result.root.meta["transcript_status"] == "unavailable"
    assert result.root.meta["metadata_source"] == "wave"

    description = _decode(fake_fs, f"{base_uri}/description.md")
    abstract = _decode(fake_fs, f"{base_uri}/.abstract.md")

    assert "Automatic transcription is unavailable." in description
    assert "meeting.wav" in abstract
    assert "placeholder" not in description.lower()


def test_video_parser_writes_preview_and_semantic_artifacts(tmp_path: Path) -> None:
    """Video parsing should emit preview, transcript, and semantic sidecars."""
    file_path = tmp_path / "demo.mp4"
    file_path.write_bytes(b"\x00\x00\x00\x18ftypisom")
    fake_fs = FakeVikingFS()
    preview_bytes = _create_png_bytes(32, 32)

    with patch.object(BaseParser, "_get_viking_fs", return_value=fake_fs):
        parser = VideoParser(
            config=VideoConfig(extract_frames=True, enable_transcription=True)
        )
        with patch.object(
            parser,
            "_extract_video_metadata",
            new=AsyncMock(
                return_value={
                    "duration": 12.5,
                    "width": 1280,
                    "height": 720,
                    "fps": 30.0,
                    "frame_count": 375,
                    "format": "mp4",
                    "metadata_source": "mock",
                }
            ),
        ), patch.object(
            parser,
            "_extract_preview_frame",
            new=AsyncMock(return_value=preview_bytes),
        ), patch.object(
            parser,
            "_build_video_transcript",
            new=AsyncMock(return_value=("plain", "Speaker says hello from the demo video.")),
        ), patch.object(
            parser,
            "_describe_preview_frame",
            new=AsyncMock(return_value="A presenter stands beside a slide with a chart."),
        ):
            result = asyncio.run(parser.parse(file_path))

    base_uri = f"{result.temp_dir_path}/demo_mp4"
    assert f"{base_uri}/demo.mp4" in fake_fs.files
    assert f"{base_uri}/preview.png" in fake_fs.files
    assert f"{base_uri}/transcript.md" in fake_fs.files
    assert f"{base_uri}/description.md" in fake_fs.files
    assert f"{base_uri}/.abstract.md" in fake_fs.files
    assert f"{base_uri}/.overview.md" in fake_fs.files
    assert result.root.meta["has_preview_frame"] is True

    description = _decode(fake_fs, f"{base_uri}/description.md")
    overview = _decode(fake_fs, f"{base_uri}/.overview.md")

    assert "A presenter stands beside a slide with a chart." in description
    assert "preview.png" in overview
    assert "Transcript status: plain" in overview
