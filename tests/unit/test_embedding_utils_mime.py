# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for _infer_image_mime / _infer_media_mime helpers and vectorize_file multimodal coverage."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("photo.JPG", "image/jpeg"),
        ("screenshot.png", "image/png"),
        ("anim.gif", "image/gif"),
        ("hero.webp", "image/webp"),
        ("icon.bmp", "image/bmp"),
        ("logo.svg", "image/svg+xml"),
        ("unknown.tiff", None),
        ("noext", None),
    ],
)
def test_infer_image_mime(filename, expected):
    from openviking.utils.embedding_utils import _infer_image_mime
    assert _infer_image_mime(filename) == expected


@pytest.mark.parametrize(
    "filename,expected_mime",
    [
        # Images
        ("photo.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("anim.gif", "image/gif"),
        ("hero.webp", "image/webp"),
        # Video (Gemini-specific MIME strings, not RFC standard)
        ("clip.mp4", "video/mp4"),
        ("movie.mov", "video/mov"),
        ("film.avi", "video/avi"),
        ("video.wmv", "video/wmv"),
        ("video.mpeg", "video/mpeg"),
        ("clip.webm", "video/webm"),
        # Audio (Gemini uses audio/mp3, audio/wav etc.)
        ("song.mp3", "audio/mp3"),
        ("sound.wav", "audio/wav"),
        ("track.ogg", "audio/ogg"),
        ("music.flac", "audio/flac"),
        # PDF
        ("doc.pdf", "application/pdf"),
        # Unknown / not in Gemini's supported list
        ("file.xyz", None),
        ("film.mkv", None),  # mkv not in Gemini MIME map
    ],
)
def test_infer_media_mime(filename, expected_mime):
    from openviking.utils.embedding_utils import _infer_media_mime
    assert _infer_media_mime(filename) == expected_mime



@pytest.mark.parametrize(
    "fname,provider,expect_media",
    [
        ("photo.png", "gemini", False),   # text-only embedder → no media
        ("clip.mp4", "gemini", False),    # text-only embedder → no media
        ("song.mp3", "gemini", False),    # text-only embedder → no media
        ("doc.pdf", "gemini", False),     # text-only embedder → no media
        ("photo.png", "openai", False),   # non-gemini → no media
        ("clip.mp4", None, False),         # no provider → no media
    ],
)
def test_vectorize_file_multimodal_coverage(fname, provider, expect_media):
    """vectorize_file sets Vectorize.media for all Gemini-supported types iff provider==gemini."""
    captured = {}

    def fake_set_vectorize(v):
        captured["vectorize"] = v

    mock_ctx = MagicMock()
    mock_ctx.user = "test_user"
    mock_ctx.account_id = "test_account"

    mock_queue = AsyncMock()
    mock_queue_manager = MagicMock()
    mock_queue_manager.get_queue.return_value = mock_queue
    mock_queue_manager.EMBEDDING = "embedding"

    mock_context = MagicMock()
    mock_context.set_vectorize = fake_set_vectorize

    mock_msg = MagicMock()

    with (
        patch("openviking.utils.embedding_utils.get_queue_manager", return_value=mock_queue_manager),
        patch("openviking.utils.embedding_utils.get_viking_fs", return_value=MagicMock()),
        patch("openviking.utils.embedding_utils.Context", return_value=mock_context),
        patch("openviking.utils.embedding_utils.EmbeddingMsgConverter") as mock_converter,
        patch("openviking.utils.embedding_utils._owner_space_for_uri", return_value="space"),
    ):
        mock_converter.from_context.return_value = mock_msg

        from openviking.utils.embedding_utils import vectorize_file
        asyncio.run(
            vectorize_file(
                file_path=f"/fake/path/{fname}",
                summary_dict={"name": fname, "summary": "A test file"},
                parent_uri="/fake/path",
                ctx=mock_ctx,
                embedding_provider=provider,
            )
        )

    v = captured.get("vectorize")
    assert v is not None, "set_vectorize was never called"
    if expect_media:
        assert v.media is not None, f"Expected media to be set for {fname} with provider={provider!r}"
    else:
        assert v.media is None, f"Expected no media for {fname} with provider={provider!r}"
