# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for video key frame extraction and metadata in VideoParser."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openviking.parse.parsers.media.video import VideoParser
from openviking_cli.utils.config.parser_config import VideoConfig


def _mock_cv2_capture(fps=30.0, frame_count=300, width=1920, height=1080):
    """Create a mock cv2.VideoCapture that returns configurable metadata."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True

    prop_map = {
        5: fps,  # CAP_PROP_FPS
        7: frame_count,  # CAP_PROP_FRAME_COUNT
        3: width,  # CAP_PROP_FRAME_WIDTH
        4: height,  # CAP_PROP_FRAME_HEIGHT
    }
    mock_cap.get.side_effect = lambda prop: prop_map.get(prop, 0)

    # Simulate frame reads: return True for frame_count frames, then False
    read_count = [0]

    def _read():
        if read_count[0] < frame_count:
            read_count[0] += 1
            frame = MagicMock()
            return True, frame
        return False, None

    mock_cap.read.side_effect = _read
    return mock_cap


@pytest.mark.asyncio
async def test_extract_metadata_returns_values():
    """Metadata extraction returns duration, resolution, fps from cv2."""
    parser = VideoParser()
    mock_cap = _mock_cv2_capture(fps=30.0, frame_count=300, width=1920, height=1080)

    mock_cv2 = MagicMock()
    mock_cv2.VideoCapture.return_value = mock_cap
    mock_cv2.CAP_PROP_FPS = 5
    mock_cv2.CAP_PROP_FRAME_COUNT = 7
    mock_cv2.CAP_PROP_FRAME_WIDTH = 3
    mock_cv2.CAP_PROP_FRAME_HEIGHT = 4

    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        result = await parser._extract_metadata(Path("/fake/video.mp4"))
        assert result["duration"] == 10.0
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert result["fps"] == 30.0
        mock_cap.release.assert_called_once()


@pytest.mark.asyncio
async def test_extract_metadata_returns_zeros_without_cv2():
    """Metadata extraction returns zeros when cv2 is not installed."""
    parser = VideoParser()

    with patch.dict("sys.modules", {"cv2": None}):
        result = await parser._extract_metadata(Path("/fake/video.mp4"))
        assert result["duration"] == 0
        assert result["width"] == 0


@pytest.mark.asyncio
async def test_extract_keyframes_returns_frames():
    """Keyframe extraction returns list of (timestamp, bytes) tuples."""
    parser = VideoParser()
    mock_cap = _mock_cv2_capture(fps=10.0, frame_count=100, width=640, height=480)

    mock_cv2 = MagicMock()
    mock_cv2.VideoCapture.return_value = mock_cap
    mock_cv2.CAP_PROP_FPS = 5
    mock_cv2.imencode.return_value = (True, MagicMock(tobytes=lambda: b"fakejpeg"))

    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        result = await parser._extract_keyframes(Path("/fake/video.mp4"), interval=5.0)
        # 100 frames at 10fps = 10s, interval 5s = frames at 0s and 5s
        assert len(result) >= 2
        assert result[0][0] == 0.0  # First frame at t=0
        mock_cap.release.assert_called_once()


@pytest.mark.asyncio
async def test_extract_keyframes_returns_empty_without_cv2():
    """Keyframe extraction returns empty list when cv2 is not installed."""
    parser = VideoParser()

    with patch.dict("sys.modules", {"cv2": None}):
        result = await parser._extract_keyframes(Path("/fake/video.mp4"), interval=5.0)
        assert result == []


@pytest.mark.asyncio
async def test_generate_description_without_cv2():
    """Video description returns install hint when cv2 is not available."""
    parser = VideoParser()
    config = VideoConfig()

    with patch.dict("sys.modules", {"cv2": None}):
        result = await parser._generate_video_description(Path("/fake/video.mp4"), config)
        assert "opencv-python-headless not installed" in result


@pytest.mark.asyncio
async def test_generate_description_with_metadata():
    """Video description includes metadata when cv2 is available."""
    parser = VideoParser()
    config = VideoConfig(extract_frames=False)

    mock_cap = _mock_cv2_capture(fps=24.0, frame_count=240, width=1280, height=720)
    mock_cv2 = MagicMock()
    mock_cv2.VideoCapture.return_value = mock_cap
    mock_cv2.CAP_PROP_FPS = 5
    mock_cv2.CAP_PROP_FRAME_COUNT = 7
    mock_cv2.CAP_PROP_FRAME_WIDTH = 3
    mock_cv2.CAP_PROP_FRAME_HEIGHT = 4

    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        result = await parser._generate_video_description(Path("/fake/video.mp4"), config)
        assert "1280x720" in result
        assert "24.0" in result
