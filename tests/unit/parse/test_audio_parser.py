# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for AudioParser with mocked Whisper API and mutagen."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.base import NodeType
from openviking.parse.parsers.media.audio import (
    AUDIO_MAGIC_BYTES,
    AudioParser,
    _extract_metadata_mutagen,
    _format_timestamp,
)
from openviking_cli.utils.config.parser_config import AudioConfig


class TestFormatTimestamp:
    def test_seconds_only(self):
        assert _format_timestamp(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert _format_timestamp(125) == "2:05"

    def test_hours(self):
        assert _format_timestamp(3661) == "1:01:01"

    def test_zero(self):
        assert _format_timestamp(0) == "0:00"


class TestExtractMetadataMutagen:
    @patch("openviking.parse.parsers.media.audio._try_import_mutagen")
    def test_mutagen_not_installed(self, mock_import):
        mock_import.return_value = None
        result = _extract_metadata_mutagen(Path("/fake/audio.mp3"))
        assert result == {}

    @patch("openviking.parse.parsers.media.audio._try_import_mutagen")
    def test_mutagen_returns_metadata(self, mock_import):
        mock_mutagen = MagicMock()
        mock_audio = MagicMock()
        mock_audio.info.length = 120.5
        mock_audio.info.sample_rate = 44100
        mock_audio.info.channels = 2
        mock_audio.info.bitrate = 320000
        mock_mutagen.File.return_value = mock_audio
        mock_import.return_value = mock_mutagen

        result = _extract_metadata_mutagen(Path("/fake/audio.mp3"))
        assert result["duration"] == 120.5
        assert result["sample_rate"] == 44100
        assert result["channels"] == 2
        assert result["bitrate"] == 320000

    @patch("openviking.parse.parsers.media.audio._try_import_mutagen")
    def test_mutagen_file_returns_none(self, mock_import):
        mock_mutagen = MagicMock()
        mock_mutagen.File.return_value = None
        mock_import.return_value = mock_mutagen

        result = _extract_metadata_mutagen(Path("/fake/audio.mp3"))
        assert result == {}

    @patch("openviking.parse.parsers.media.audio._try_import_mutagen")
    def test_mutagen_raises_exception(self, mock_import):
        mock_mutagen = MagicMock()
        mock_mutagen.File.side_effect = Exception("corrupt file")
        mock_import.return_value = mock_mutagen

        result = _extract_metadata_mutagen(Path("/fake/audio.mp3"))
        assert result == {}


class TestAudioParserInit:
    def test_default_config(self):
        parser = AudioParser()
        assert parser.config.enable_transcription is True
        assert parser.config.transcription_model == "whisper-large-v3"

    def test_custom_config(self):
        config = AudioConfig(enable_transcription=False, language="en")
        parser = AudioParser(config=config)
        assert parser.config.enable_transcription is False
        assert parser.config.language == "en"

    def test_supported_extensions(self):
        parser = AudioParser()
        exts = parser.supported_extensions
        assert ".mp3" in exts
        assert ".wav" in exts
        assert ".ogg" in exts
        assert ".flac" in exts
        assert ".aac" in exts
        assert ".m4a" in exts

    def test_can_parse(self):
        parser = AudioParser()
        assert parser.can_parse("test.mp3") is True
        assert parser.can_parse("test.wav") is True
        assert parser.can_parse("test.txt") is False
        assert parser.can_parse("test.pdf") is False


class TestAudioParserValidation:
    def test_validate_mp3_id3(self):
        parser = AudioParser()
        audio_bytes = b"ID3" + b"\x00" * 100
        parser._validate_audio_bytes(audio_bytes, ".mp3", Path("test.mp3"))

    def test_validate_wav_riff(self):
        parser = AudioParser()
        audio_bytes = b"RIFF" + b"\x00" * 100
        parser._validate_audio_bytes(audio_bytes, ".wav", Path("test.wav"))

    def test_validate_flac(self):
        parser = AudioParser()
        audio_bytes = b"fLaC" + b"\x00" * 100
        parser._validate_audio_bytes(audio_bytes, ".flac", Path("test.flac"))

    def test_validate_ogg(self):
        parser = AudioParser()
        audio_bytes = b"OggS" + b"\x00" * 100
        parser._validate_audio_bytes(audio_bytes, ".ogg", Path("test.ogg"))

    def test_invalid_mp3_raises(self):
        parser = AudioParser()
        audio_bytes = b"NOT_MP3" + b"\x00" * 100
        with pytest.raises(ValueError, match="Invalid audio file"):
            parser._validate_audio_bytes(audio_bytes, ".mp3", Path("test.mp3"))

    def test_unknown_extension_skips_validation(self):
        parser = AudioParser()
        audio_bytes = b"anything"
        parser._validate_audio_bytes(audio_bytes, ".xyz", Path("test.xyz"))


class TestAudioParserParse:
    @pytest.mark.asyncio
    async def test_file_not_found(self):
        parser = AudioParser()
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            await parser.parse("/nonexistent/audio.mp3")

    @pytest.mark.asyncio
    @patch("openviking.parse.parsers.media.audio._extract_metadata_mutagen")
    async def test_parse_metadata_only(self, mock_metadata):
        """Test parsing with transcription disabled - metadata only."""
        mock_metadata.return_value = {
            "duration": 60.0,
            "sample_rate": 44100,
            "channels": 2,
            "bitrate": 128000,
        }

        config = AudioConfig(enable_transcription=False)
        parser = AudioParser(config=config)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"ID3" + b"\x00" * 200)
            tmp_path = f.name

        try:
            mock_viking_fs = MagicMock()
            mock_viking_fs.create_temp_uri.return_value = "viking://temp/test123"
            mock_viking_fs.mkdir = AsyncMock()
            mock_viking_fs.write_file_bytes = AsyncMock()
            mock_viking_fs.write_file = AsyncMock()

            with patch(
                "openviking.parse.parsers.media.audio.get_viking_fs",
                return_value=mock_viking_fs,
            ):
                result = await parser.parse(tmp_path)

            assert result.parser_name == "AudioParser"
            assert result.source_format == "audio"
            assert result.root.type == NodeType.ROOT
            assert result.root.meta["duration"] == 60.0
            assert result.root.meta["sample_rate"] == 44100
            assert result.root.meta["channels"] == 2
            assert result.root.meta["has_transcript"] is False
            assert len(result.warnings) > 0
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    @patch("openviking.parse.parsers.media.audio._extract_metadata_mutagen")
    async def test_parse_with_transcript_segments(self, mock_metadata):
        """Test parsing with mocked Whisper returning timestamped segments."""
        mock_metadata.return_value = {
            "duration": 30.0,
            "sample_rate": 16000,
            "channels": 1,
            "bitrate": 64000,
        }

        config = AudioConfig(enable_transcription=True)
        parser = AudioParser(config=config)

        segments = [
            {"start": 0.0, "end": 10.0, "text": "Hello world."},
            {"start": 10.0, "end": 20.0, "text": "This is a test."},
            {"start": 20.0, "end": 30.0, "text": "Goodbye."},
        ]

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"ID3" + b"\x00" * 200)
            tmp_path = f.name

        try:
            mock_viking_fs = MagicMock()
            mock_viking_fs.create_temp_uri.return_value = "viking://temp/test456"
            mock_viking_fs.mkdir = AsyncMock()
            mock_viking_fs.write_file_bytes = AsyncMock()
            mock_viking_fs.write_file = AsyncMock()

            with (
                patch(
                    "openviking.parse.parsers.media.audio.get_viking_fs",
                    return_value=mock_viking_fs,
                ),
                patch.object(
                    parser,
                    "_asr_transcribe_with_timestamps",
                    new_callable=AsyncMock,
                    return_value=segments,
                ),
            ):
                result = await parser.parse(tmp_path)

            assert result.root.meta["has_transcript"] is True
            assert result.root.meta["segment_count"] == 3
            assert len(result.root.children) == 3
            assert result.root.children[0].type == NodeType.SECTION
            assert "0:00" in result.root.children[0].title
            assert result.root.children[0].meta["text"] == "Hello world."
            assert len(result.warnings) == 0

            mock_viking_fs.write_file.assert_called_once()
            call_args = mock_viking_fs.write_file.call_args
            assert "transcript.md" in call_args[0][0]
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestAudioParserTranscript:
    def test_build_transcript_markdown_with_segments(self):
        parser = AudioParser()
        segments = [
            {"start": 0.0, "end": 15.0, "text": "First segment."},
            {"start": 15.0, "end": 30.0, "text": "Second segment."},
        ]
        md = parser._build_transcript_markdown(segments, "", "test_audio")
        assert "# Transcript: test_audio" in md
        assert "**[0:00 - 0:15]** First segment." in md
        assert "**[0:15 - 0:30]** Second segment." in md

    def test_build_transcript_markdown_plain(self):
        parser = AudioParser()
        md = parser._build_transcript_markdown(
            [], "This is the full transcript text.", "test_audio"
        )
        assert "# Transcript: test_audio" in md
        assert "This is the full transcript text." in md


class TestAudioParserParseContent:
    @pytest.mark.asyncio
    async def test_parse_content_not_implemented(self):
        parser = AudioParser()
        with pytest.raises(NotImplementedError):
            await parser.parse_content("base64data")


class TestAudioMagicBytes:
    def test_magic_bytes_defined(self):
        """Verify magic bytes are defined for all supported formats."""
        assert ".mp3" in AUDIO_MAGIC_BYTES
        assert ".wav" in AUDIO_MAGIC_BYTES
        assert ".ogg" in AUDIO_MAGIC_BYTES
        assert ".flac" in AUDIO_MAGIC_BYTES
        assert ".aac" in AUDIO_MAGIC_BYTES
        assert ".m4a" in AUDIO_MAGIC_BYTES
        assert ".opus" in AUDIO_MAGIC_BYTES
