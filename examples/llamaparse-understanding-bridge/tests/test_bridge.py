# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the LlamaParse Understanding API bridge."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from openviking_llamaparse_bridge import bridge
from openviking_llamaparse_bridge.bridge import (
    Bridge,
    LlamaParseClient,
    LlamaParseError,
    Settings,
    build_artifact,
    create_app,
    load_settings,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llama_api_key="llx-test",
        bridge_api_key="bridge-secret-with-at-least-32-chars",
        llama_base_url="https://llama.test",
        public_url="http://bridge.test",
        timeout_seconds=5,
        organization_id="org-1",
        project_id="project-1",
    )


class FakeLlamaParseClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes]] = []
        self.jobs: list[dict[str, str | None]] = []
        self.status: dict[str, Any] = {"job": {"status": "PENDING"}}
        self.result: dict[str, Any] = {}
        self.asset_downloads = 0

    async def upload_file(self, filename: str, file: Any, _: str | None) -> dict[str, Any]:
        self.uploads.append((filename, file.read()))
        return {"id": "file-1"}

    async def create_job(
        self, *, file_id: str | None = None, source_url: str | None = None
    ) -> dict[str, Any]:
        self.jobs.append({"file_id": file_id, "source_url": source_url})
        return {"id": "job-1"}

    async def get_job(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        assert job_id == "job-1"
        return self.result if include_result else self.status

    async def download_asset(self, _: str) -> bytes:
        self.asset_downloads += 1
        return b"image-bytes"


def _auth(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.bridge_api_key}"}


@pytest.fixture
async def bridge_client(settings: Settings):
    llama = FakeLlamaParseClient()
    app = create_app(settings, llama)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.test"
    ) as client:
        yield client, llama


def test_settings_keep_simple_defaults_and_validate_required_values() -> None:
    values = {
        "LLAMA_CLOUD_API_KEY": "llx-test",
        "PARSER_BRIDGE_API_KEY": "bridge-secret-with-at-least-32-chars",
    }

    settings = load_settings(values)

    assert settings.llama_base_url == "https://api.cloud.llamaindex.ai"
    assert settings.tier == "agentic"
    assert settings.cost_optimizer is True
    with pytest.raises(ValueError, match="LLAMA_CLOUD_API_KEY"):
        load_settings({**values, "LLAMA_CLOUD_API_KEY": ""})
    with pytest.raises(ValueError, match="requires an agentic tier"):
        load_settings(
            {
                **values,
                "LLAMAPARSE_TIER": "cost_effective",
                "LLAMAPARSE_COST_OPTIMIZER": "true",
            }
        )
    with pytest.raises(ValueError, match="output_options.*object"):
        load_settings({**values, "LLAMAPARSE_PARSE_OPTIONS_JSON": '{"output_options":null}'})


async def test_llamaparse_client_maps_upload_parse_poll_and_asset_requests(
    settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    async def api_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/beta/files":
            body = await request.aread()
            assert b'filename="report.pdf"' in body
            return httpx.Response(200, json={"id": "file-1"})
        if request.method == "POST":
            payload = json.loads((await request.aread()).decode())
            assert payload["file_id"] == "file-1"
            assert payload["tier"] == "agentic"
            assert payload["output_options"]["images_to_save"] == ["embedded", "layout"]
            return httpx.Response(200, json={"id": "job-1"})
        assert request.url.params["expand"] == ("markdown_full,markdown,images_content_metadata")
        return httpx.Response(200, json={"job": {"status": "COMPLETED"}})

    async def asset_handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"image")

    api = httpx.AsyncClient(
        base_url=settings.llama_base_url,
        headers={"Authorization": f"Bearer {settings.llama_api_key}"},
        transport=httpx.MockTransport(api_handler),
    )
    assets = httpx.AsyncClient(transport=httpx.MockTransport(asset_handler))
    client = LlamaParseClient(settings, api_client=api, download_client=assets)
    try:
        assert (await client.upload_file("report.pdf", io.BytesIO(b"data"), "application/pdf"))[
            "id"
        ] == "file-1"
        assert (await client.create_job(file_id="file-1"))["id"] == "job-1"
        assert (await client.get_job("job-1", include_result=True))["job"]["status"] == (
            "COMPLETED"
        )
        assert await client.download_asset("https://assets.test/image.png") == b"image"
    finally:
        await api.aclose()
        await assets.aclose()

    assert all(request.headers["authorization"] == "Bearer llx-test" for request in requests)


async def test_disabled_cost_optimizer_overrides_advanced_options(settings: Settings) -> None:
    payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload.update(json.loads((await request.aread()).decode()))
        return httpx.Response(200, json={"id": "job-1"})

    configured = replace(
        settings,
        cost_optimizer=False,
        parse_options={
            "processing_options": {
                "cost_optimizer": {"enable": True},
                "ignore": {"ignore_diagonal_text": True},
            }
        },
    )
    api = httpx.AsyncClient(
        base_url=settings.llama_base_url, transport=httpx.MockTransport(handler)
    )
    client = LlamaParseClient(configured, api_client=api)
    try:
        await client.create_job(file_id="file-1")
    finally:
        await client.aclose()
        await api.aclose()

    assert "cost_optimizer" not in payload["processing_options"]
    assert payload["processing_options"]["ignore"] == {"ignore_diagonal_text": True}


def test_asset_client_keeps_proxy_and_certificate_environment(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(bridge.httpx, "AsyncClient", Client)

    LlamaParseClient(settings)

    assert calls[1].get("trust_env", True) is True
    assert calls[1]["follow_redirects"] is True


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            {"type": "file", "file": {"file_id": "file-1"}},
            {"file_id": "file-1", "source_url": None},
        ),
        (
            {"type": "input_file", "file_url": "https://x.test/a.pdf"},
            {"file_id": None, "source_url": "https://x.test/a.pdf"},
        ),
        (
            {"type": "input_image", "image_url": "https://x.test/a.png"},
            {"file_id": None, "source_url": "https://x.test/a.png"},
        ),
        (
            {"type": "input_audio", "audio_url": "https://x.test/a.wav"},
            {"file_id": None, "source_url": "https://x.test/a.wav"},
        ),
    ],
)
async def test_understanding_sources_map_to_llamaparse(
    bridge_client: Any, settings: Settings, content: dict[str, Any], expected: dict[str, Any]
) -> None:
    client, llama = bridge_client
    response = await client.post(
        "/api/v3/responses",
        headers=_auth(settings),
        json={"input": [{"content": [content]}]},
    )

    assert response.json() == {"id": "job-1", "object": "response", "status": "in_progress"}
    assert llama.jobs == [expected]


@pytest.mark.parametrize(
    ("source_name", "upload_name"), [("../report.pdf", "report.pdf"), ("..", "upload")]
)
async def test_bridge_uploads_local_file(
    bridge_client: Any, settings: Settings, source_name: str, upload_name: str
) -> None:
    client, llama = bridge_client

    response = await client.post(
        "/api/v3/files",
        headers=_auth(settings),
        files={"file": (source_name, b"document", "application/pdf")},
    )

    assert response.json()["id"] == "file-1"
    assert llama.uploads == [(upload_name, b"document")]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            {
                "type": "input_file",
                "file_url": "https://example.feishu.cn/docx/a",
                "lark_file": {"user_access_token": "secret"},
            },
            "credential-gated URLs",
        ),
        ({"type": "input_video", "video_url": "https://x.test/a.mp4"}, "input_video"),
    ],
)
async def test_bridge_rejects_unsupported_inputs(
    bridge_client: Any, settings: Settings, content: dict[str, Any], message: str
) -> None:
    client, llama = bridge_client

    response = await client.post(
        "/api/v3/responses",
        headers=_auth(settings),
        json={"input": [{"content": [content]}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_input"
    assert message in response.json()["error"]["message"]
    assert llama.jobs == []


@pytest.mark.parametrize("include_markdown_full", [True, False])
async def test_completed_result_is_prebuilt_cached_and_downloadable(
    bridge_client: Any, settings: Settings, include_markdown_full: bool
) -> None:
    client, llama = bridge_client
    llama.status = {"job": {"status": "COMPLETED"}}
    llama.result = {
        "job": {"status": "COMPLETED"},
        "markdown": {
            "pages": [
                {"page_number": 1, "markdown": "page one", "success": True},
                {"page_number": 2, "markdown": "", "success": False},
            ]
        },
        "images_content_metadata": {
            "images": [{"filename": "chart.png", "presigned_url": "https://assets.test/chart.png"}]
        },
    }
    if include_markdown_full:
        llama.result["markdown_full"] = "page one"

    first = await client.get("/api/v3/responses/job-1", headers=_auth(settings))
    assert first.json()["status"] == "in_progress"
    for _ in range(20):
        await asyncio.sleep(0)
        status = await client.get("/api/v3/responses/job-1", headers=_auth(settings))
        if status.json()["status"] == "completed":
            break
    else:
        pytest.fail("artifact did not become ready")

    parsed = urlsplit(status.json()["result"]["zip_url"])
    artifact = await client.get(f"{parsed.path}?{parsed.query}")
    repeated = await client.get(f"{parsed.path}?{parsed.query}")

    assert artifact.status_code == 200
    assert repeated.content == artifact.content
    assert llama.asset_downloads == 1
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        assert archive.namelist() == ["content.md", "chart.png"]
        assert archive.read("content.md").decode() == ("page one\n\n> Page 2 failed to parse.")
        assert archive.read("chart.png") == b"image-bytes"


async def test_completed_build_is_cached_without_another_poll(settings: Settings) -> None:
    llama = FakeLlamaParseClient()
    llama.result = {"job": {"status": "COMPLETED"}, "markdown_full": "content"}
    bridge = Bridge(settings, llama, owns_client=False)  # type: ignore[arg-type]

    assert await bridge._artifact_ready("job-1") is False
    await asyncio.gather(*bridge.builds.values())
    await asyncio.sleep(0)

    assert "job-1" in bridge.artifacts
    assert bridge.builds == {}


@pytest.mark.parametrize(
    ("status_payload", "expected"),
    [
        ({"job": {"status": "RUNNING"}}, "in_progress"),
        ({"job": {"status": "FAILED", "error_message": "bad PDF"}}, "failed"),
    ],
)
async def test_bridge_maps_job_statuses(
    bridge_client: Any, settings: Settings, status_payload: dict[str, Any], expected: str
) -> None:
    client, llama = bridge_client
    llama.status = status_payload

    response = await client.get("/api/v3/responses/job-1", headers=_auth(settings))

    assert response.json()["status"] == expected
    if expected == "failed":
        assert response.json()["output"][0]["content"][0]["text"] == "bad PDF"


async def test_bridge_requires_auth_and_rejects_invalid_artifact_url(
    bridge_client: Any, settings: Settings
) -> None:
    client, _ = bridge_client

    unauthorized = await client.get("/api/v3/responses/job-1")
    invalid_artifact = await client.get("/artifacts/job-1.zip?expires=1&signature=bad")

    assert unauthorized.status_code == 401
    assert invalid_artifact.status_code == 403


async def test_build_artifact_fails_when_no_usable_markdown_exists() -> None:
    llama = FakeLlamaParseClient()
    llama.result = {
        "job": {"status": "COMPLETED"},
        "markdown": {"pages": [{"page_number": 1, "success": False}]},
    }

    with pytest.raises(LlamaParseError, match="no usable Markdown"):
        await build_artifact(llama, "job-1")  # type: ignore[arg-type]
