# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for runtime image-MIME blacklisting and content-based image detection.

When a vision endpoint rejects an image payload with an "invalid image"
error, the offending MIME type is blacklisted for the process lifetime so
subsequent files of that format skip the VLM. Formats outside the
universally-decodable set (PNG/JPEG/GIF/WebP) are converted to JPEG before
being sent, so well-formed TIFF/BMP/AVIF files never reach the endpoint in
an undecodable form.
"""

import io

import httpx
import openai
import pytest
from PIL import Image

from openviking.models.vlm.backends.openai_vlm import (
    OpenAIVLM,
    _blacklisted_image_mimes,
    blacklist_image_mime,
    is_image_error,
    is_image_mime_blacklisted,
)
from openviking.utils.image_search import (
    NATIVE_IMAGE_MIME_TYPES,
    _prepare_image_bytes_for_model,
    detect_image_mime,
)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buf, format="JPEG")
    return buf.getvalue()


def _tiff_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(buf, format="TIFF")
    return buf.getvalue()


def _bmp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "black").save(buf, format="BMP")
    return buf.getvalue()


def _invalid_image_error(message: str) -> openai.BadRequestError:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "http://example.com/v1/chat/completions"),
        content=b'{"error": {"message": "%s"}}' % message.encode(),
    )
    return openai.BadRequestError(
        f"Error code: 400 - {message}", response=response, body={"error": {"message": message}}
    )


class _FailingChatCompletions:
    def __init__(self, message):
        self.message = message

    async def create(self, **_kwargs):
        raise _invalid_image_error(self.message)


class _FailingAsyncClient:
    def __init__(self, message):
        class _Chat:
            pass

        self.chat = _Chat()
        self.chat.completions = _FailingChatCompletions(message)


@pytest.fixture(autouse=True)
def _clean_blacklist():
    _blacklisted_image_mimes.clear()
    yield
    _blacklisted_image_mimes.clear()


PROVIDER = "openai"
API_BASE = "http://ollama:11434/v1"
MODEL = "gemma4:e2b"


def _vlm() -> OpenAIVLM:
    return OpenAIVLM(
        {
            "provider": PROVIDER,
            "model": MODEL,
            "api_key": "key",
            "api_base": API_BASE,
        }
    )


class TestDetectImageMime:
    def test_detects_raster_formats(self):
        assert detect_image_mime(_png_bytes()) == "image/png"
        assert detect_image_mime(_jpeg_bytes()) == "image/jpeg"
        assert detect_image_mime(_tiff_bytes()) == "image/tiff"
        assert detect_image_mime(_bmp_bytes()) == "image/bmp"

    def test_rejects_non_images(self):
        assert detect_image_mime(b"hello world text") is None
        assert detect_image_mime(b"") is None
        assert detect_image_mime(b"\x00\x01\x02\xff\xff\xff") is None


class TestPrepareImageBytesForModel:
    def test_native_formats_pass_through_unchanged(self):
        for data in (_png_bytes(), _jpeg_bytes()):
            out, changed = _prepare_image_bytes_for_model(data)
            assert out == data
            assert changed is False

    def test_non_native_formats_convert_to_jpeg(self):
        for data in (_tiff_bytes(), _bmp_bytes()):
            out, changed = _prepare_image_bytes_for_model(data)
            assert changed is True
            assert detect_image_mime(out) == "image/jpeg"
            assert out not in (data,)

    def test_undecodable_content_passes_through(self):
        garbage = b"\x00\x01garbage-not-an-image"
        out, changed = _prepare_image_bytes_for_model(garbage)
        assert out == garbage
        assert changed is True


class TestImageErrorClassification:
    def test_recognizes_image_rejection_messages(self):
        assert is_image_error(_invalid_image_error("invalid image input"))
        assert is_image_error(_invalid_image_error("Failed to load image or audio file"))

    def test_ignores_non_image_errors(self):
        assert not is_image_error(RuntimeError("server overloaded"))
        assert not is_image_error(openai.APITimeoutError(request=httpx.Request("GET", "http://x")))


class TestBlacklistLearning:
    @pytest.mark.asyncio
    async def test_invalid_image_400_blacklists_mime(self):
        vlm = _vlm()
        vlm._async_client_cache.get = lambda factory: _FailingAsyncClient("invalid image input")

        with pytest.raises(openai.BadRequestError):
            await vlm.get_vision_completion_async(prompt="ok", images=[_tiff_bytes()])

        assert is_image_mime_blacklisted(PROVIDER, API_BASE, MODEL, "image/tiff")

    @pytest.mark.asyncio
    async def test_blacklisted_mime_is_skipped(self):
        vlm = _vlm()
        blacklist_image_mime(PROVIDER, API_BASE, MODEL, "image/tiff")
        assert vlm._prepare_image(_tiff_bytes()) is None

    @pytest.mark.asyncio
    async def test_universally_decodable_mimes_are_never_blacklisted(self):
        vlm = _vlm()
        vlm._async_client_cache.get = lambda factory: _FailingAsyncClient("invalid image input")

        with pytest.raises(openai.BadRequestError):
            await vlm.get_vision_completion_async(prompt="ok", images=[_png_bytes()])

        assert not is_image_mime_blacklisted(PROVIDER, API_BASE, MODEL, "image/png")
        assert vlm._prepare_image(_png_bytes()) is not None

    @pytest.mark.asyncio
    async def test_non_image_error_does_not_blacklist(self):
        vlm = _vlm()
        vlm.max_retries = 1

        class _OverloadedCompletions:
            async def create(self, **_kwargs):
                raise openai.APIStatusError(
                    "server overloaded",
                    response=httpx.Response(
                        503, request=httpx.Request("POST", "http://x/v1/chat/completions")
                    ),
                    body=None,
                )

        class _OverloadedClient:
            def __init__(self):
                class _Chat:
                    pass

                self.chat = _Chat()
                self.chat.completions = _OverloadedCompletions()

        vlm._async_client_cache.get = lambda factory: _OverloadedClient()

        with pytest.raises(openai.APIStatusError):
            await vlm.get_vision_completion_async(prompt="ok", images=[_tiff_bytes()])

        assert not is_image_mime_blacklisted(PROVIDER, API_BASE, MODEL, "image/tiff")

    @pytest.mark.asyncio
    async def test_blacklist_is_scoped_per_endpoint(self):
        blacklist_image_mime(PROVIDER, API_BASE, MODEL, "image/tiff")

        assert is_image_mime_blacklisted(PROVIDER, API_BASE, MODEL, "image/tiff")
        assert not is_image_mime_blacklisted(PROVIDER, "http://other:11434/v1", MODEL, "image/tiff")
        assert not is_image_mime_blacklisted(PROVIDER, API_BASE, "other-model", "image/tiff")

    @pytest.mark.asyncio
    async def test_all_images_skipped_without_prompt_raises(self):
        vlm = _vlm()
        blacklist_image_mime(PROVIDER, API_BASE, MODEL, "image/tiff")

        with pytest.raises(ValueError):
            await vlm.get_vision_completion_async(prompt="", images=[_tiff_bytes()])


class TestNativeMimeSet:
    def test_native_set_matches_universal_vision_formats(self):
        assert NATIVE_IMAGE_MIME_TYPES == {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
        }
