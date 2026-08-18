import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from openviking.parse.parsers.media import utils as media_utils
from openviking_cli.utils.config.parser_config import ImageConfig
from openviking_cli.utils.config.vlm_config import VLMConfig


@pytest.fixture(autouse=True)
def _stub_media_prompt(monkeypatch):
    monkeypatch.setattr(media_utils, "render_prompt", lambda *_args, **_kwargs: "prompt")


class _FS:
    def __init__(self, content=b"media"):
        self.content = content
        self.stat = AsyncMock(return_value={"size": len(content)})

    async def read(self, _uri, offset=0, size=-1, ctx=None):
        if offset >= len(self.content):
            return b""
        return self.content[offset : offset + size]

    async def read_file_bytes(self, _uri, ctx=None):
        return self.content


class _BlockingReadFS:
    def __init__(self):
        self.read_calls = 0
        self.active_reads = 0
        self.peak_reads = 0
        self.two_reads_entered = asyncio.Event()
        self.release_reads = asyncio.Event()

    async def stat(self, *_args, **_kwargs):
        return {"size": 5}

    async def read(self, _uri, offset=0, size=-1, ctx=None):
        if offset:
            return b""
        self.read_calls += 1
        self.active_reads += 1
        self.peak_reads = max(self.peak_reads, self.active_reads)
        if self.active_reads == 2:
            self.two_reads_entered.set()
        try:
            await self.release_reads.wait()
            return b"video"
        finally:
            self.active_reads -= 1


class _MediaVLM:
    model = "media-vlm"

    def supports_media(self, *, media_type, filename, size_bytes):
        extensions = {
            "audio": {".mp3", ".wav", ".aac", ".m4a"},
            "video": {".mp4", ".avi", ".mov"},
        }
        return (
            Path(filename).suffix.lower() in extensions.get(media_type, set())
            and 0 <= size_bytes <= 512 * 1024 * 1024
        )


class _BlockingMediaClient(_MediaVLM):
    def __init__(self):
        self.active_inference = 0
        self.peak_inference = 0
        self.two_inferences_entered = asyncio.Event()
        self.release_inference = asyncio.Event()

    async def get_media_completion_async(self, **kwargs):
        self.active_inference += 1
        self.peak_inference = max(self.peak_inference, self.active_inference)
        if self.active_inference == 2:
            self.two_inferences_entered.set()
        try:
            await self.release_inference.wait()
            return f"# Clip\n\nUseful video summary.\n\n### {kwargs['filename']}\n\nDetailed scene."
        finally:
            self.active_inference -= 1


class _CapturingPathClient(_MediaVLM):
    def __init__(self):
        self.path_calls = 0

    async def get_media_completion_async(
        self,
        *,
        media_path,
        prepare_media=None,
        **_kwargs,
    ):
        if prepare_media is not None:
            await prepare_media()
        self.path_calls += 1
        media_path.read_bytes()
        return "# Clip\n\nUseful summary."


class _ImageVLM:
    def __init__(self):
        self.images = []

    async def get_vision_completion_async(self, *, prompt, images):
        self.images = images
        return "image summary"


def _config(model_config, *, max_chars=4000):
    if hasattr(model_config, "get_client_instance"):
        vlm = model_config.get_client_instance()
        vlm.model = getattr(model_config, "model", getattr(vlm, "model", "media-vlm"))
    else:
        vlm = model_config
    return SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(
            overview_max_chars=max_chars,
            abstract_max_chars=256,
        ),
        output_language_override="en",
    )


def _jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def _lazy_client(*, return_value=None, side_effect=None):
    async def invoke(prepare_media=None, **_kwargs):
        if prepare_media is not None:
            await prepare_media()
        if side_effect is not None:
            raise side_effect
        return return_value

    client = _MediaVLM()
    client.get_media_completion_async = AsyncMock(side_effect=invoke)
    return client


async def test_media_concurrency_bounds_staging_and_inference(monkeypatch):
    fs = _BlockingReadFS()
    client = _BlockingMediaClient()
    config_vlm = VLMConfig(
        provider="volcengine",
        api_key="test-key",
        model="media-model",
        media={"enabled": True, "max_concurrent": 2},
    )
    config_vlm._vlm_instance = client
    config = SimpleNamespace(
        vlm=config_vlm,
        semantic=SimpleNamespace(
            overview_max_chars=4000,
            abstract_max_chars=256,
        ),
        output_language_override="en",
    )
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: config)

    tasks = [
        asyncio.create_task(
            media_utils.generate_video_summary(
                f"viking://resources/video/clip-{index}.mp4",
                f"clip-{index}.mp4",
                llm_sem=asyncio.Semaphore(64),
            )
        )
        for index in range(4)
    ]

    await fs.two_reads_entered.wait()
    assert fs.read_calls == 2
    assert fs.peak_reads == 2
    fs.release_reads.set()
    await client.two_inferences_entered.wait()
    assert client.peak_inference == 2
    client.release_inference.set()
    results = await asyncio.gather(*tasks)

    assert all(result["summary"] for result in results)
    assert fs.peak_reads == 2


async def test_image_summary_downsamples_large_model_input(monkeypatch):
    original = _jpeg_bytes(80, 220)
    fs = _FS(original)
    vlm = _ImageVLM()
    config = SimpleNamespace(
        vlm=vlm,
        image=ImageConfig(
            preview_max_dimension=64,
            max_file_size_mb=100.0,
            large_image_threshold_dimension=100,
        ),
    )
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: config)

    result = await media_utils.generate_image_summary(
        "viking://resources/docs/large.jpg",
        "large.jpg",
    )

    assert result == {"name": "large.jpg", "summary": "image summary"}
    assert len(vlm.images) == 1
    assert max(_image_size(vlm.images[0])) <= 64
    assert fs.content == original


async def test_unknown_size_media_stops_at_hard_staging_limit(
    monkeypatch,
):
    fs = _FS()
    fs.stat.return_value = {"size": 0}
    fs.read = AsyncMock(side_effect=[b"a" * 4, b"b" * 4, b"c", b""])
    client = _CapturingPathClient()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    monkeypatch.setattr(media_utils, "_MEDIA_READ_CHUNK_BYTES", 4)
    monkeypatch.setattr(media_utils, "MAX_MEDIA_FILE_BYTES", 8)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        media_utils,
        "get_openviking_config",
        lambda: _config(model_config),
    )

    result = await media_utils.generate_video_summary(
        "viking://resources/video/unknown-size.mp4",
        "unknown-size.mp4",
    )

    assert result == {
        "name": "unknown-size.mp4",
        "summary": "",
    }
    assert fs.read.await_count == 3
    assert client.path_calls == 0


async def test_success_normalizes_markdown_and_filename_heading(
    monkeypatch,
):
    raw = "```markdown\nA useful overview paragraph.\n\n## Facts\n\n- Revenue grew 20%.\n```"
    client = _lazy_client(return_value=raw)
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        media_utils,
        "get_openviking_config",
        lambda: _config(model_config),
    )

    result = await media_utils.generate_video_summary(
        "viking://resources/video/quarterly.mov",
        "quarterly.mov",
    )

    assert result["summary"].startswith("# quarterly\n\nA useful overview paragraph.")
    assert "### quarterly.mov" in result["summary"]
    assert result["summary"].index("### quarterly.mov") < result["summary"].index("## Facts")
    client.get_media_completion_async.assert_awaited_once()


async def test_provider_failure_returns_empty_summary(monkeypatch):
    client = _lazy_client(side_effect=RuntimeError("status code 401"))
    model_config = SimpleNamespace(
        model="doubao-seed-2-0-lite-260428",
        get_client_instance=lambda: client,
    )
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        media_utils,
        "get_openviking_config",
        lambda: _config(model_config),
    )

    result = await media_utils.generate_video_summary(
        "viking://resources/video/clip.mp4",
        "clip.mp4",
    )

    assert result == {"name": "clip.mp4", "summary": ""}


def test_refusal_provider_output_is_rejected():
    for raw in (
        "Unable to analyze this media.",
        "抱歉，我无法理解该视频内容。",
        "# Provider response\n\nI'm unable to analyze this media.",
    ):
        assert (
            media_utils._normalize_media_markdown(
                raw,
                filename="clip.mp4",
                overview_max_chars=4000,
                abstract_max_chars=256,
            )
            == ""
        )
