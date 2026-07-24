import asyncio
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.models.media_understanding.base import MediaUnderstandingClient
from openviking.parse.parsers.media import utils as media_utils
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


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


class _BlockingMediaClient(MediaUnderstandingClient):
    def __init__(self):
        super().__init__(max_concurrent=2)
        self.active_inference = 0
        self.peak_inference = 0
        self.two_inferences_entered = asyncio.Event()
        self.release_inference = asyncio.Event()

    async def _understand(self, **kwargs):
        self.active_inference += 1
        self.peak_inference = max(self.peak_inference, self.active_inference)
        if self.active_inference == 2:
            self.two_inferences_entered.set()
        try:
            await self.release_inference.wait()
            return (
                "# Clip\n\nUseful video summary.\n\n"
                f"### {kwargs['filename']}\n\nDetailed scene."
            )
        finally:
            self.active_inference -= 1


class _CapturingPathClient(MediaUnderstandingClient):
    def __init__(self):
        super().__init__(max_concurrent=1)
        self.path_calls = 0
        self.content = None

    async def _understand(self, **kwargs):
        raise AssertionError("byte hook must not be used")

    async def _understand_path(self, *, path, filename, **_kwargs):
        self.path_calls += 1
        self.content = path.read_bytes()
        return f"# Clip\n\nUseful summary.\n\n### {filename}\n\nDetails."


def _config(model_config, *, max_chars=4000):
    return SimpleNamespace(
        media_understanding=SimpleNamespace(audio=model_config, video=model_config),
        semantic=SimpleNamespace(overview_max_chars=max_chars, abstract_max_chars=256),
        output_language_override="en",
    )


def _lazy_client(*, return_value=None, side_effect=None):
    async def invoke(*, content_writer, filename, **_kwargs):
        with tempfile.TemporaryDirectory() as temp_dir:
            await content_writer(Path(temp_dir) / filename)
            if side_effect is not None:
                raise side_effect
            return return_value

    return SimpleNamespace(understand_from_writer=AsyncMock(side_effect=invoke))


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
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    tasks = [
        asyncio.create_task(
            media_utils.generate_video_summary(
                f"viking://resources/video/clip-{index}.mp4",
                f"clip-{index}.mp4",
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
    monkeypatch.setattr(media_utils, "_ARK_MAX_FILE_BYTES", 8)
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_video_summary(
        "viking://resources/video/growing.mp4", "growing.mp4"
    )

    assert result == {"name": "growing.mp4", "summary": ""}
    assert client.path_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("first_chunk", [b"", "not-bytes"])
async def test_media_summary_rejects_empty_or_non_binary_chunk(
    monkeypatch, first_chunk
):
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
async def test_media_helper_logs_structured_metadata_without_provider_message(
    monkeypatch, caplog
):
    class ProviderError(RuntimeError):
        status_code = 503
        code = "ServiceUnavailable"
        request_id = "request-safe"

    async def fail_after_write(*, content_writer, filename, **_kwargs):
        with tempfile.TemporaryDirectory() as temp_dir:
            await content_writer(Path(temp_dir) / filename)
            raise ProviderError("SECRET_API_KEY SECRET_PROMPT SECRET_RESPONSE")

    client = SimpleNamespace(understand_from_writer=fail_after_write)
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    with caplog.at_level(logging.WARNING, logger=media_utils.logger.name):
        result = await media_utils.generate_video_summary(
            "viking://resources/video/clip.mp4", "clip.mp4"
        )

    assert result == {"name": "clip.mp4", "summary": ""}
    assert "SECRET_" not in caplog.text
    assert "ProviderError" in caplog.text
    assert "status=503" in caplog.text
    assert "request_id=request-safe" in caplog.text


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
    client = SimpleNamespace(understand_from_writer=AsyncMock())
    model_config = SimpleNamespace(get_client_instance=lambda: client)
    fs = _FS()
    monkeypatch.setattr(media_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(media_utils, "get_openviking_config", lambda: _config(model_config))

    result = await media_utils.generate_audio_summary(
        "viking://resources/audio/meeting.flac", "meeting.flac"
    )

    assert result == {"name": "meeting.flac", "summary": ""}
    client.understand_from_writer.assert_not_awaited()


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
    assert result["summary"].startswith(
        "# quarterly\n\nA useful overview paragraph."
    )
    assert "### quarterly.mov" in result["summary"]
    assert result["summary"].index("### quarterly.mov") < result["summary"].index(
        "## Facts"
    )
    assert len(result["summary"]) <= 4000
    client.understand_from_writer.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_failure_and_empty_response_return_empty_summary(monkeypatch):
    client = _lazy_client(side_effect=RuntimeError("status code 401"))
    model_config = SimpleNamespace(get_client_instance=lambda: client)
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
    client.understand_from_writer.assert_awaited_once()


@pytest.mark.asyncio
async def test_oversize_media_skips_read_and_provider(monkeypatch):
    client = SimpleNamespace(understand_from_writer=AsyncMock())
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
    client.understand_from_writer.assert_not_awaited()


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
        (
            "# Unable to analyze media\n\n"
            "Unable to analyze this media.\n\n"
            "### clip.mp4"
        ),
        (
            "# Provider response\n\n"
            "I'm unable to analyze this media.\n\n"
            "### clip.mp4"
        ),
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
        "```json\n{\"unexpected\": true}\n```\n"
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
