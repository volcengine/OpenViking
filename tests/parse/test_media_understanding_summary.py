import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.parsers.media import utils as media_utils
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.utils.config.vlm_config import VLMConfig


@pytest.fixture(autouse=True)
def _stub_media_prompt(monkeypatch):
    monkeypatch.setattr(media_utils, "render_prompt", lambda *_args, **_kwargs: "prompt")


class _FS:
    def __init__(self, content=b"media"):
        self.content = content
        self.read_calls = []
        self.read_file_bytes = AsyncMock(return_value=content)
        self.stat = AsyncMock(return_value={"size": len(content)})

    async def read(self, _uri, offset=0, size=-1, ctx=None):
        self.read_calls.append((offset, size))
        if offset >= len(self.content):
            return b""
        return self.content[offset : offset + size]


class _BlockingReadFS:
    def __init__(self):
        self.read_calls = 0
        self.read_file_bytes_calls = 0
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

    async def read_file_bytes(self, uri, ctx=None):
        self.read_file_bytes_calls += 1
        return await self.read(uri, ctx=ctx)


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
        self.content = None

    async def get_media_completion_async(
        self,
        *,
        media_path,
        filename,
        prepare_media=None,
        **_kwargs,
    ):
        if prepare_media is not None:
            await prepare_media()
        self.path_calls += 1
        self.content = media_path.read_bytes()
        return f"# Clip\n\nUseful summary.\n\n### {filename}\n\nDetails."


def _config(model_config, *, max_chars=4000):
    if model_config is None:
        vlm = _MediaVLM()
        vlm.supports_media = lambda **_kwargs: False
        vlm.get_media_completion_async = AsyncMock()
    elif hasattr(model_config, "get_client_instance"):
        vlm = model_config.get_client_instance()
        vlm.model = getattr(model_config, "model", getattr(vlm, "model", "media-vlm"))
    else:
        vlm = model_config
    return SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(overview_max_chars=max_chars, abstract_max_chars=256),
        output_language_override="en",
    )


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


def test_media_utils_imports_in_a_clean_process_without_a_cycle():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from openviking.parse.parsers.media.utils import "
                "generate_image_summary, generate_audio_summary"
            ),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_media_concurrency_bounds_vikingfs_reads_and_inference(monkeypatch):
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
        semantic=SimpleNamespace(overview_max_chars=4000, abstract_max_chars=256),
        output_language_override="en",
    )
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: config)
    llm_sem = asyncio.Semaphore(64)

    tasks = [
        asyncio.create_task(
            media_utils.generate_video_summary(
                f"viking://resources/video/clip-{index}.mp4",
                f"clip-{index}.mp4",
                llm_sem=llm_sem,
            )
        )
        for index in range(4)
    ]

    await fs.two_reads_entered.wait()
    for _ in range(3):
        await asyncio.sleep(0)
    assert fs.read_calls == 2
    assert fs.peak_reads == 2

    fs.release_reads.set()
    await client.two_inferences_entered.wait()
    assert client.peak_inference == 2
    client.release_inference.set()
    results = await asyncio.gather(*tasks)
    assert all(set(result) == {"name", "summary"} for result in results)
    assert fs.peak_reads == 2
    assert fs.read_file_bytes_calls == 0


@pytest.mark.asyncio
async def test_media_summary_stages_vikingfs_content_in_bounded_chunks(monkeypatch):
    content = b"a" * (media_utils._MEDIA_READ_CHUNK_BYTES * 2 + 17)
    fs = _FS(content)
    client = _CapturingPathClient()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/clip.mp4", "clip.mp4"
    )

    assert result["summary"]
    assert client.content == content
    assert fs.read_calls == [
        (0, media_utils._MEDIA_READ_CHUNK_BYTES),
        (
            media_utils._MEDIA_READ_CHUNK_BYTES,
            media_utils._MEDIA_READ_CHUNK_BYTES,
        ),
        (
            media_utils._MEDIA_READ_CHUNK_BYTES * 2,
            media_utils._MEDIA_READ_CHUNK_BYTES,
        ),
        (len(content), media_utils._MEDIA_READ_CHUNK_BYTES),
    ]
    fs.read_file_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_summary_rejects_file_that_grows_past_limit(monkeypatch):
    fs = _FS()
    fs.stat.return_value = {"size": 1}
    fs.read = AsyncMock(side_effect=[b"a" * 4, b"b" * 4, b"c"])
    client = _CapturingPathClient()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    monkeypatch.setattr(media_utils, "_MEDIA_READ_CHUNK_BYTES", 4)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/growing.mp4", "growing.mp4"
    )

    assert result == {"name": "growing.mp4", "summary": ""}
    assert client.path_calls == 0


@pytest.mark.asyncio
async def test_media_summary_rejects_unknown_size_past_hard_limit(monkeypatch):
    fs = _FS()
    fs.stat.return_value = {"size": 0}
    fs.read = AsyncMock(side_effect=[b"a" * 4, b"b" * 4, b"c", b""])
    client = _CapturingPathClient()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    monkeypatch.setattr(media_utils, "_MEDIA_READ_CHUNK_BYTES", 4)
    monkeypatch.setattr(media_utils, "MAX_MEDIA_FILE_BYTES", 8)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/unknown-size.mp4",
        "unknown-size.mp4",
    )

    assert result == {"name": "unknown-size.mp4", "summary": ""}
    assert fs.read.await_count == 3
    assert client.path_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("first_chunk", [b"", "not-bytes"])
async def test_media_summary_rejects_empty_or_non_binary_chunk(monkeypatch, first_chunk):
    fs = _FS()
    fs.read = AsyncMock(return_value=first_chunk)
    client = _CapturingPathClient()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/invalid.mp4", "invalid.mp4"
    )

    assert result == {"name": "invalid.mp4", "summary": ""}
    assert client.path_calls == 0
    fs.read_file_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_helper_logs_structured_metadata_without_provider_message(monkeypatch, caplog):
    class ProviderError(RuntimeError):
        status_code = 503
        code = "ServiceUnavailable"
        request_id = "request-safe"

    async def fail_after_write(prepare_media=None, **_kwargs):
        if prepare_media is not None:
            await prepare_media()
        raise ProviderError("SECRET_API_KEY SECRET_PROMPT SECRET_RESPONSE")

    client = _MediaVLM()
    client.get_media_completion_async = fail_after_write
    model_config = SimpleNamespace(
        model="doubao-seed-2-0-lite-260428",
        get_client_instance=lambda: client,
    )
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    media_utils.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger=media_utils.logger.name):
            result = await media_utils.generate_video_summary(
                "viking://resources/video/clip.mp4", "clip.mp4"
            )
    finally:
        media_utils.logger.removeHandler(caplog.handler)

    assert result == {"name": "clip.mp4", "summary": ""}
    assert "SECRET_" not in caplog.text
    assert "model=doubao-seed-2-0-lite-260428" in caplog.text
    assert "media_type=video" in caplog.text
    assert "ProviderError" in caplog.text
    assert "status=503" in caplog.text
    assert "code=ServiceUnavailable" in caplog.text
    assert "request_id=request-safe" in caplog.text
    assert (
        "Verify that the model supports this media type and that provider "
        "access/configuration is valid"
    ) in caplog.text


@pytest.mark.asyncio
async def test_missing_audio_config_returns_only_name_and_empty_summary(monkeypatch):
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(None))

    result = await media_utils.generate_audio_summary(
        "viking://resources/audio/meeting.mp3", "meeting.mp3"
    )

    assert result == {"name": "meeting.mp3", "summary": ""}
    fs.read_file_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_format_skips_client(monkeypatch):
    client = _MediaVLM()
    client.get_media_completion_async = AsyncMock()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_audio_summary(
        "viking://resources/audio/meeting.flac", "meeting.flac"
    )

    assert result == {"name": "meeting.flac", "summary": ""}
    client.get_media_completion_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_normalizes_markdown_and_preserves_filename_h3(monkeypatch):
    raw = "```markdown\nA useful overview paragraph.\n\n## Facts\n\n- Revenue grew 20%.\n```"
    client = _lazy_client(return_value=raw)
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/quarterly.mov", "quarterly.mov"
    )

    assert set(result) == {"name", "summary"}
    assert result["summary"].startswith("# quarterly\n\nA useful overview paragraph.")
    assert "### quarterly.mov" in result["summary"]
    assert result["summary"].index("### quarterly.mov") < result["summary"].index("## Facts")
    assert len(result["summary"]) <= 4000
    client.get_media_completion_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_failure_and_empty_response_return_empty_summary(monkeypatch):
    client = _lazy_client(side_effect=RuntimeError("status code 401"))
    model_config = SimpleNamespace(
        model="doubao-seed-2-0-lite-260428",
        get_client_instance=lambda: client,
    )
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/clip.mp4", "clip.mp4"
    )

    assert result == {"name": "clip.mp4", "summary": ""}


@pytest.mark.asyncio
async def test_empty_provider_response_returns_empty_summary(monkeypatch):
    client = _lazy_client(return_value="")
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_audio_summary(
        "viking://resources/audio/silence.wav", "silence.wav"
    )

    assert result == {"name": "silence.wav", "summary": ""}
    client.get_media_completion_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_oversize_media_skips_read_and_provider(monkeypatch):
    client = _MediaVLM()
    client.get_media_completion_async = AsyncMock()
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    fs.stat.return_value = {"size": 512 * 1024 * 1024 + 1}
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/large.mp4", "large.mp4"
    )

    assert result == {"name": "large.mp4", "summary": ""}
    fs.read_file_bytes.assert_not_awaited()
    client.get_media_completion_async.assert_not_awaited()


@pytest.mark.parametrize(
    "raw",
    [
        "Unable to analyze this media.",
        "I cannot analyze the provided audio.",
        "I'm unable to analyze this media.",
        "Sorry, I couldn't understand the video.",
        "I’m sorry, but I can’t analyze the supplied media file.",
        "抱歉，我无法理解该视频内容。",
        "无法识别音频内容。",
    ],
)
def test_short_whole_response_refusals_are_rejected(raw):
    assert (
        media_utils._normalize_media_markdown(
            raw,
            filename="clip.mp4",
            overview_max_chars=4000,
            abstract_max_chars=256,
        )
        == ""
    )


@pytest.mark.parametrize(
    "raw",
    [
        "---",
        "| Item | Value |\n| --- | --- |\n| Revenue | 20% |",
        "```markdown\n# Unclosed fence",
        "```",
    ],
)
def test_non_prose_only_outputs_are_rejected(raw):
    assert (
        media_utils._normalize_media_markdown(
            raw,
            filename="clip.mp4",
            overview_max_chars=4000,
            abstract_max_chars=256,
        )
        == ""
    )


@pytest.mark.parametrize(
    "raw",
    [
        ("# Unable to analyze media\n\nUnable to analyze this media.\n\n### clip.mp4"),
        ("# Provider response\n\nI'm unable to analyze this media.\n\n### clip.mp4"),
    ],
)
def test_prompt_shaped_refusal_brief_is_rejected(raw):
    assert (
        media_utils._normalize_media_markdown(
            raw,
            filename="clip.mp4",
            overview_max_chars=4000,
            abstract_max_chars=256,
        )
        == ""
    )


@pytest.mark.parametrize(
    "raw",
    [
        "I cannot access the provided video.",
        "I don't have access to the supplied audio.",
        "The provided media is not accessible to me.",
    ],
)
def test_narrow_no_access_provider_responses_are_rejected(raw):
    assert (
        media_utils._normalize_media_markdown(
            raw,
            filename="clip.mp4",
            overview_max_chars=4000,
            abstract_max_chars=256,
        )
        == ""
    )


def test_prefaced_markdown_fence_is_rejected_instead_of_repaired():
    raw = (
        "Here is the requested summary:\n\n"
        "```markdown\n# Clip\n\nUseful detail.\n\n### clip.mp4\n\nScene.\n```"
    )

    assert (
        media_utils._normalize_media_markdown(
            raw,
            filename="clip.mp4",
            overview_max_chars=4000,
            abstract_max_chars=256,
        )
        == ""
    )


def test_residual_fence_after_outer_markdown_unwrap_is_rejected():
    raw = (
        "```markdown\n"
        "# Clip\n\nUseful detail.\n\n### clip.mp4\n\n"
        '```json\n{"unexpected": true}\n```\n'
        "```"
    )

    assert (
        media_utils._normalize_media_markdown(
            raw,
            filename="clip.mp4",
            overview_max_chars=4000,
            abstract_max_chars=256,
        )
        == ""
    )


def test_brief_only_summary_is_recoverable_beneath_exact_filename_h3():
    brief = "The recording confirms the owner, deadline, and release scope."

    summary = media_utils._normalize_media_markdown(
        f"# Release meeting\n\n{brief}",
        filename="meeting.mp3",
        overview_max_chars=4000,
        abstract_max_chars=256,
    )
    recovered = SemanticProcessor()._parse_overview_md(summary)

    assert recovered == {"meeting.mp3": brief}


def test_later_h3_cannot_steal_recoverable_filename_summary():
    brief = "The clip confirms the release owner and deadline."
    raw = (
        f"# Release clip\n\n{brief}\n\n"
        "### clip.mp4\n\n"
        "## Details\n\n### Scene analysis\n\nConcrete visual detail."
    )

    summary = media_utils._normalize_media_markdown(
        raw,
        filename="clip.mp4",
        overview_max_chars=4000,
        abstract_max_chars=256,
    )
    recovered = SemanticProcessor()._parse_overview_md(summary)

    assert recovered["clip.mp4"] == brief


@pytest.mark.parametrize(
    ("raw", "expected_start"),
    [
        (
            "A concrete English overview with useful facts and named entities.",
            "# meeting\n\nA concrete English overview",
        ),
        (
            "# 项目复盘\n\n本次会议确认了发布范围、负责人和下周的交付日期。",
            "# 项目复盘\n\n本次会议确认了发布范围",
        ),
        (
            "Встреча подтвердила сроки выпуска и ответственных участников.",
            "# meeting\n\nВстреча подтвердила сроки",
        ),
        (
            "أكد الاجتماع موعد الإصدار والمسؤولين عن خطوات التسليم.",
            "# meeting\n\nأكد الاجتماع موعد الإصدار",
        ),
        (
            "회의에서 출시 일정과 후속 작업 담당자를 확정했습니다.",
            "# meeting\n\n회의에서 출시 일정과 후속 작업",
        ),
        (
            "ミーティングではリリースのスケジュールとタスクをまとめました。",
            "# meeting\n\nミーティングではリリースのスケジュール",
        ),
    ],
)
def test_valid_english_and_chinese_prose_is_preserved(raw, expected_start):
    summary = media_utils._normalize_media_markdown(
        raw,
        filename="meeting.mp3",
        overview_max_chars=4000,
        abstract_max_chars=256,
    )

    assert summary.startswith(expected_start)
    assert "### meeting.mp3" in summary


def test_refusal_phrase_inside_real_prose_is_not_broadly_rejected():
    raw = (
        "# Incident Review\n\n"
        'The team investigated the message "Unable to analyze this media." '
        "and documented a concrete recovery plan."
    )

    summary = media_utils._normalize_media_markdown(
        raw,
        filename="incident.mp4",
        overview_max_chars=4000,
        abstract_max_chars=256,
    )

    assert summary.startswith("# Incident Review\n\nThe team investigated")


def test_refusal_phrase_in_detail_does_not_reject_substantive_brief():
    raw = (
        "# Incident Review\n\n"
        "The team documented a concrete recovery plan with owners and dates.\n\n"
        "### incident.mp4\n\n"
        '> The original provider response was "Unable to analyze this media."'
    )

    summary = media_utils._normalize_media_markdown(
        raw,
        filename="incident.mp4",
        overview_max_chars=4000,
        abstract_max_chars=256,
    )

    assert summary.startswith("# Incident Review\n\nThe team documented")
    assert "Unable to analyze this media." in summary


def test_no_access_phrase_quoted_in_substantive_detail_is_preserved():
    raw = (
        "# Incident Review\n\n"
        "The team documented a concrete recovery plan with owners and dates.\n\n"
        "### incident.mp4\n\n"
        '> The provider had said "I cannot access the provided video."'
    )

    summary = media_utils._normalize_media_markdown(
        raw,
        filename="incident.mp4",
        overview_max_chars=4000,
        abstract_max_chars=256,
    )

    assert summary.startswith("# Incident Review\n\nThe team documented")
    assert "I cannot access the provided video." in summary
