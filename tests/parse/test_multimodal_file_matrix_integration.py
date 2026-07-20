# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Black-box compatibility tests for multimodal resource ingestion."""

import json
import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio

import openviking as ov

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class MultimodalCase:
    row: int
    category: str
    label: str
    extension: str
    url: str

    @property
    def pytest_id(self) -> str:
        return f"{self.row:02d}-{self.category}-{self.label}"


MULTIMODAL_CASES: tuple[MultimodalCase, ...] = (
    MultimodalCase(
        1,
        "image",
        "bmp",
        ".bmp",
        "https://filesamples.com/samples/image/bmp/sample_640%C3%97426.bmp",
    ),
    MultimodalCase(
        2,
        "image",
        "jpeg",
        ".jpeg",
        "https://filesamples.com/samples/image/jpeg/sample_640%C3%97426.jpeg",
    ),
    MultimodalCase(
        3,
        "image",
        "jpg",
        ".jpg",
        "https://filesamples.com/samples/image/jpg/sample_640%C3%97426.jpg",
    ),
    MultimodalCase(
        4,
        "image",
        "png",
        ".png",
        "https://filesamples.com/samples/image/png/sample_640%C3%97426.png",
    ),
    MultimodalCase(
        5,
        "image",
        "webp",
        ".webp",
        "https://filesamples.com/samples/image/webp/sample1.webp",
    ),
    MultimodalCase(
        6,
        "audio",
        "aac",
        ".aac",
        "https://filesamples.com/samples/audio/aac/sample1.aac",
    ),
    MultimodalCase(
        7,
        "audio",
        "ac3",
        ".ac3",
        "https://filesamples.com/samples/audio/ac3/sample1.ac3",
    ),
    MultimodalCase(
        8,
        "audio",
        "flac",
        ".flac",
        "https://samples.ffmpeg.org/flac/24-bit_192kHz.flac",
    ),
    MultimodalCase(
        9,
        "audio",
        "m4a",
        ".m4a",
        "https://filesamples.com/samples/audio/m4a/sample1.m4a",
    ),
    MultimodalCase(
        10,
        "audio",
        "mp3",
        ".mp3",
        "https://filesamples.com/samples/audio/mp3/sample1.mp3",
    ),
    MultimodalCase(
        11,
        "audio",
        "opus",
        ".opus",
        "https://filesamples.com/samples/audio/opus/sample1.opus",
    ),
    MultimodalCase(
        12,
        "audio",
        "wav",
        ".wav",
        "https://samplelib.com/wav/sample-3s.wav",
    ),
    MultimodalCase(
        13,
        "video",
        "avi",
        ".avi",
        "https://filesamples.com/samples/video/avi/sample_1280x720.avi",
    ),
    MultimodalCase(
        14,
        "video",
        "flv",
        ".flv",
        "https://res.cloudinary.com/demo/video/upload/w_1280,h_720,c_fill,vc_h264,fps_30,du_10/dog.flv",
    ),
    MultimodalCase(
        15,
        "video",
        "mkv",
        ".mkv",
        "https://test-videos.co.uk/vids/bigbuckbunny/mkv/720/Big_Buck_Bunny_720_10s_2MB.mkv",
    ),
    MultimodalCase(
        16,
        "video",
        "mov",
        ".mov",
        "https://filesamples.com/samples/video/mov/sample_1280x720.mov",
    ),
    MultimodalCase(
        17,
        "video",
        "mp4",
        ".mp4",
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_2MB.mp4",
    ),
    MultimodalCase(
        18,
        "video",
        "ts",
        ".ts",
        "https://filesamples.com/samples/video/ts/sample_1280x720.ts",
    ),
    MultimodalCase(
        19,
        "video",
        "webm",
        ".webm",
        "https://test-videos.co.uk/vids/bigbuckbunny/webm/vp9/720/Big_Buck_Bunny_720_10s_2MB.webm",
    ),
    MultimodalCase(
        20,
        "video",
        "mpeg",
        ".mpeg",
        "https://filesamples.com/samples/video/mpeg/sample_1280x720.mpeg",
    ),
    MultimodalCase(
        21,
        "video",
        "hevc",
        ".mp4",
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h265/720/Big_Buck_Bunny_720_10s_2MB.mp4",
    ),
    MultimodalCase(
        22,
        "video",
        "avi-codec",
        ".avi",
        "https://filesamples.com/samples/video/avi/sample_1280x720.avi",
    ),
    MultimodalCase(
        23,
        "video",
        "vp8",
        ".webm",
        "https://test-videos.co.uk/vids/bigbuckbunny/webm/vp8/720/Big_Buck_Bunny_720_10s_2MB.webm",
    ),
    MultimodalCase(
        24,
        "video",
        "vp9",
        ".webm",
        "https://test-videos.co.uk/vids/bigbuckbunny/webm/vp9/720/Big_Buck_Bunny_720_10s_2MB.webm",
    ),
    MultimodalCase(
        25,
        "video",
        "h264",
        ".mp4",
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_2MB.mp4",
    ),
    MultimodalCase(
        26,
        "video",
        "h265",
        ".mp4",
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h265/720/Big_Buck_Bunny_720_10s_2MB.mp4",
    ),
    MultimodalCase(
        27,
        "document",
        "txt",
        ".txt",
        "https://filesamples.com/samples/document/txt/sample1.txt",
    ),
    MultimodalCase(
        28,
        "document",
        "doc",
        ".doc",
        "https://filesamples.com/samples/document/doc/sample1.doc",
    ),
    MultimodalCase(
        29,
        "document",
        "docx",
        ".docx",
        "https://filesamples.com/samples/document/docx/sample1.docx",
    ),
    MultimodalCase(
        30,
        "document",
        "md",
        ".md",
        "https://raw.githubusercontent.com/github/markup/master/README.md",
    ),
    MultimodalCase(
        31,
        "document",
        "pdf",
        ".pdf",
        "https://filesamples.com/samples/document/pdf/sample1.pdf",
    ),
)


RUN_ID = uuid4().hex[:12]
LOCAL_SERVICE_URL = "http://127.0.0.1:1933"


def _live_tests_enabled() -> bool:
    return os.getenv("OPENVIKING_RUN_MULTIMODAL_INTEGRATION") == "1"


def _request_timeout() -> float:
    return float(os.getenv("OPENVIKING_MULTIMODAL_TIMEOUT", "600"))


def _build_client() -> ov.AsyncHTTPClient:
    try:
        return ov.AsyncHTTPClient(timeout=_request_timeout())
    except ValueError as error:
        if "url is required" not in str(error):
            raise
        return ov.AsyncHTTPClient(url=LOCAL_SERVICE_URL, timeout=_request_timeout())


def _assert_ingest_succeeded(result: dict[str, Any]) -> None:
    errors = result.get("errors") or []
    assert not errors, f"OpenViking returned resource errors: {errors}"

    root_uri = result.get("root_uri")
    assert isinstance(root_uri, str) and root_uri.startswith("viking://"), (
        f"OpenViking did not return a valid root_uri: {result}"
    )

    queue_failures: list[str] = []
    queue_status = result.get("queue_status") or {}
    if isinstance(queue_status, dict):
        for queue_name, status in queue_status.items():
            if not isinstance(status, dict):
                continue
            error_count = int(status.get("error_count") or 0)
            queue_errors = status.get("errors") or []
            if error_count or queue_errors:
                queue_failures.append(
                    f"{queue_name}: error_count={error_count}, errors={queue_errors}"
                )

    assert not queue_failures, "OpenViking queue errors: " + "; ".join(queue_failures)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def ov_client():
    client = _build_client()
    await client.initialize()
    try:
        assert await client.health(), "OpenViking service health check failed"
        yield client
    finally:
        await client.close()


def test_multimodal_matrix_has_unique_case_ids():
    case_ids = [case.pytest_id for case in MULTIMODAL_CASES]

    assert len(case_ids) == 31
    assert len(case_ids) == len(set(case_ids))


def test_build_client_prefers_current_openviking_config(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "https://configured-ov.example.com",
                "api_key": "configured-key",
                "account": "configured-account",
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_URL", raising=False)
    monkeypatch.delenv("OPENVIKING_BASE_URL", raising=False)
    monkeypatch.delenv("OPENVIKING_API_KEY", raising=False)

    client = _build_client()

    assert client._url == "https://configured-ov.example.com"
    assert client._api_key == "configured-key"
    assert client._account == "configured-account"


def test_build_client_falls_back_to_localhost_without_configured_url(tmp_path, monkeypatch):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text("{}")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("OPENVIKING_URL", raising=False)
    monkeypatch.delenv("OPENVIKING_BASE_URL", raising=False)

    client = _build_client()

    assert client._url == "http://127.0.0.1:1933"


def test_service_response_accepts_success():
    _assert_ingest_succeeded(
        {
            "root_uri": "viking://resources/sample",
            "queue_status": {
                "Semantic": {"processed": 1, "error_count": 0, "errors": []},
                "Embedding": {"processed": 1, "error_count": 0, "errors": []},
            },
        }
    )


def test_service_response_requires_root_uri():
    with pytest.raises(AssertionError, match="root_uri"):
        _assert_ingest_succeeded({})


def test_service_response_rejects_resource_errors():
    result = {
        "root_uri": "viking://resources/sample",
        "errors": ["resource processing failed"],
    }

    with pytest.raises(AssertionError, match="resource errors"):
        _assert_ingest_succeeded(result)


def test_service_response_rejects_queue_errors():
    result = {
        "root_uri": "viking://resources/sample",
        "queue_status": {
            "Semantic": {
                "error_count": 1,
                "errors": ["processing failed"],
            }
        },
    }

    with pytest.raises(AssertionError, match="Semantic"):
        _assert_ingest_succeeded(result)


@pytest.mark.skipif(
    not _live_tests_enabled(),
    reason="set OPENVIKING_RUN_MULTIMODAL_INTEGRATION=1 to run against an OpenViking service",
)
@pytest.mark.parametrize("case", MULTIMODAL_CASES, ids=lambda case: case.pytest_id)
@pytest.mark.asyncio(loop_scope="module")
async def test_multimodal_file_is_supported(case: MultimodalCase, ov_client):
    target_uri = f"viking://resources/multimodal-{RUN_ID}-{case.row:02d}-{case.label}"

    try:
        result = await ov_client.add_resource(
            path=case.url,
            to=target_uri,
            wait=False,
        )
    except Exception as error:
        pytest.fail(
            f"OpenViking failed to ingest {case.pytest_id} from {case.url}: "
            f"{type(error).__name__}: {error}"
        )

    _assert_ingest_succeeded(result)
