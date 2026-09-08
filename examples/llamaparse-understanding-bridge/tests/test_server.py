# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Understanding API-compatible server."""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from openviking_llamaparse_bridge import __main__ as cli
from openviking_llamaparse_bridge import server
from openviking_llamaparse_bridge.config import Settings
from openviking_llamaparse_bridge.llamaparse import LlamaParseError
from openviking_llamaparse_bridge.server import create_app


class FakeLlamaParseClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str | None]] = []
        self.jobs: list[dict[str, str | None]] = []
        self.job_result: dict[str, Any] = {"job": {"status": "PENDING"}}

    async def upload_file(
        self, filename: str, file: Any, content_type: str | None
    ) -> dict[str, Any]:
        self.uploads.append((filename, file.read(), content_type))
        return {"id": "file-1", "name": filename}

    async def create_job(
        self, *, file_id: str | None = None, source_url: str | None = None
    ) -> dict[str, Any]:
        self.jobs.append({"file_id": file_id, "source_url": source_url})
        return {"id": "job-1", "status": "PENDING"}

    async def get_job(self, job_id: str, *, include_result: bool = False) -> dict[str, Any]:
        assert job_id == "job-1"
        if include_result and self.job_result.get("job", {}).get("status") == "COMPLETED":
            return {**self.job_result, "markdown_full": "# Parsed document"}
        return self.job_result

    async def download_asset(self, _: str) -> bytes:
        raise AssertionError("test result contains no images")


@pytest.fixture
async def bridge_client(settings: Settings):
    llama = FakeLlamaParseClient()
    app = create_app(settings, llama)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
        yield client, llama


def _auth(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.bridge_api_key}"}


async def _wait_for_completed(client: httpx.AsyncClient, settings: Settings) -> httpx.Response:
    for _ in range(20):
        response = await client.get("/api/v3/responses/job-1", headers=_auth(settings))
        if response.json()["status"] == "completed":
            return response
        await asyncio.sleep(0.01)
    pytest.fail("artifact preparation did not complete")


def test_cli_starts_the_configured_server(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = object()
    call: dict[str, Any] = {}
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_app", lambda loaded: app)

    def run(target: object, **kwargs: Any) -> None:
        call.update({"target": target, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", run)

    cli.main()

    assert call == {
        "target": app,
        "host": settings.bind_host,
        "port": settings.bind_port,
    }


async def test_app_closes_its_llamaparse_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    llama = FakeLlamaParseClient()
    llama.closed = False

    async def close() -> None:
        llama.closed = True

    llama.aclose = close  # type: ignore[attr-defined]
    monkeypatch.setattr(server, "LlamaParseClient", lambda _: llama)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert not llama.closed

    assert llama.closed


async def test_health_does_not_expose_secrets(bridge_client: Any) -> None:
    client, _ = bridge_client

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": "llamaparse-v2",
        "tier": "agentic",
        "cost_optimizer": True,
    }


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic secret"}],
)
async def test_understanding_routes_require_authentication(
    bridge_client: Any, headers: dict[str, str]
) -> None:
    client, _ = bridge_client

    response = await client.get("/api/v3/responses/job-1", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_file_upload_returns_a_llamaparse_file_id(
    bridge_client: Any, settings: Settings
) -> None:
    client, llama = bridge_client

    response = await client.post(
        "/api/v3/files",
        headers=_auth(settings),
        data={"purpose": "user_data"},
        files={"file": ("../report.pdf", b"document", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "file-1"
    assert llama.uploads == [("report.pdf", b"document", "application/pdf")]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            {"type": "file", "file": {"file_id": "file-1"}},
            {"file_id": "file-1", "source_url": None},
        ),
        (
            {"type": "input_file", "file_url": "https://documents.test/report.pdf"},
            {"file_id": None, "source_url": "https://documents.test/report.pdf"},
        ),
    ],
)
async def test_response_creation_maps_supported_sources(
    bridge_client: Any, settings: Settings, content: dict[str, Any], expected: dict[str, Any]
) -> None:
    client, llama = bridge_client
    payload = {
        "input": [{"role": "user", "content": [content]}],
        "tools": [{"type": "understanding"}],
        "store": True,
    }

    response = await client.post("/api/v3/responses", headers=_auth(settings), json=payload)

    assert response.status_code == 200
    assert response.json() == {"id": "job-1", "object": "response", "status": "in_progress"}
    assert llama.jobs == [expected]


@pytest.mark.parametrize("content_type", ["input_image", "input_audio", "input_video"])
async def test_response_creation_rejects_unsupported_media(
    bridge_client: Any, settings: Settings, content_type: str
) -> None:
    client, _ = bridge_client
    payload = {"input": [{"content": [{"type": content_type, "image_url": "https://x"}]}]}

    response = await client.post("/api/v3/responses", headers=_auth(settings), json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_input"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"input": [{"content": []}]},
        {"input": [{"content": [{"type": "file", "file": {}}]}]},
        {"input": [{"content": [{"type": "input_file", "file_url": "ftp://x"}]}]},
    ],
)
async def test_response_creation_rejects_malformed_sources(
    bridge_client: Any, settings: Settings, payload: dict[str, Any]
) -> None:
    client, _ = bridge_client

    response = await client.post("/api/v3/responses", headers=_auth(settings), json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_file_upload_rejects_a_response_without_an_id(
    bridge_client: Any, settings: Settings
) -> None:
    client, llama = bridge_client

    async def upload_file(_: str, __: Any, ___: str | None) -> dict[str, Any]:
        return {}

    llama.upload_file = upload_file

    response = await client.post(
        "/api/v3/files",
        headers=_auth(settings),
        files={"file": ("report.pdf", b"document", "application/pdf")},
    )

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "LlamaParse file response has no id"


async def test_response_creation_rejects_a_job_without_an_id(
    bridge_client: Any, settings: Settings
) -> None:
    client, llama = bridge_client

    async def create_job(
        *, file_id: str | None = None, source_url: str | None = None
    ) -> dict[str, Any]:
        del file_id, source_url
        return {}

    llama.create_job = create_job
    payload = {"input": [{"content": [{"type": "file", "file": {"file_id": "x"}}]}]}

    response = await client.post("/api/v3/responses", headers=_auth(settings), json=payload)

    assert response.status_code == 502
    assert response.json()["error"]["message"] == "LlamaParse create response has no id"


@pytest.mark.parametrize(
    ("job_status", "expected_status"),
    [
        ("PENDING", "in_progress"),
        ("RUNNING", "in_progress"),
        ("FAILED", "failed"),
        ("CANCELLED", "failed"),
    ],
)
async def test_polling_maps_llamaparse_statuses(
    bridge_client: Any, settings: Settings, job_status: str, expected_status: str
) -> None:
    client, llama = bridge_client
    llama.job_result = {"job": {"status": job_status, "error_message": "parse failed"}}

    response = await client.get("/api/v3/responses/job-1", headers=_auth(settings))

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    if expected_status == "failed":
        assert response.json()["output"][0]["content"][0]["text"] == "parse failed"


@pytest.mark.parametrize(
    ("job_result", "message"),
    [
        ({}, "no job object"),
        ({"job": {"status": "PAUSED"}}, "unknown job status: PAUSED"),
    ],
)
async def test_polling_rejects_invalid_llamaparse_status(
    bridge_client: Any,
    settings: Settings,
    job_result: dict[str, Any],
    message: str,
) -> None:
    client, llama = bridge_client
    llama.job_result = job_result

    response = await client.get("/api/v3/responses/job-1", headers=_auth(settings))

    assert response.status_code == 502
    assert message in response.json()["error"]["message"]


async def test_completed_job_produces_downloadable_zip(
    bridge_client: Any, settings: Settings
) -> None:
    client, llama = bridge_client
    llama.job_result = {"job": {"status": "COMPLETED"}}

    status_response = await _wait_for_completed(client, settings)
    zip_url = status_response.json()["result"]["zip_url"]
    artifact_response = await client.get(urlsplit(zip_url).path + "?" + urlsplit(zip_url).query)

    assert artifact_response.status_code == 200
    assert artifact_response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(artifact_response.content)) as archive:
        assert archive.namelist() == ["content.md"]
        assert archive.read("content.md") == b"# Parsed document"


async def test_completed_status_waits_for_and_reuses_cached_artifact(
    bridge_client: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, llama = bridge_client
    llama.job_result = {"job": {"status": "COMPLETED"}}
    build_started = asyncio.Event()
    release_build = asyncio.Event()
    build_calls = 0

    async def build(_: Any, job_id: str) -> bytes:
        nonlocal build_calls
        assert job_id == "job-1"
        build_calls += 1
        build_started.set()
        await release_build.wait()
        return b"cached zip"

    monkeypatch.setattr(server, "build_artifact", build)

    first_status = await client.get("/api/v3/responses/job-1", headers=_auth(settings))

    assert first_status.status_code == 200
    assert first_status.json()["status"] == "in_progress"
    await asyncio.wait_for(build_started.wait(), timeout=1)
    release_build.set()

    for _ in range(10):
        await asyncio.sleep(0)
        completed_status = await client.get("/api/v3/responses/job-1", headers=_auth(settings))
        if completed_status.json()["status"] == "completed":
            break
    else:
        pytest.fail("artifact preparation did not complete")

    parsed = urlsplit(completed_status.json()["result"]["zip_url"])
    first_artifact = await client.get(parsed.path + "?" + parsed.query)
    second_artifact = await client.get(parsed.path + "?" + parsed.query)

    assert first_artifact.content == b"cached zip"
    assert second_artifact.content == b"cached zip"
    assert build_calls == 1


async def test_artifact_preparation_error_is_returned_on_poll(
    bridge_client: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, llama = bridge_client
    llama.job_result = {"job": {"status": "COMPLETED"}}

    async def fail(_: Any, __: str) -> bytes:
        raise LlamaParseError(502, "artifact failed")

    monkeypatch.setattr(server, "build_artifact", fail)

    first_status = await client.get("/api/v3/responses/job-1", headers=_auth(settings))
    assert first_status.json()["status"] == "in_progress"
    await asyncio.sleep(0)

    failed_status = await client.get("/api/v3/responses/job-1", headers=_auth(settings))

    assert failed_status.status_code == 502
    assert failed_status.json()["error"]["message"] == "artifact failed"


async def test_valid_artifact_url_returns_not_found_before_cache_is_ready(
    bridge_client: Any, settings: Settings
) -> None:
    client, _ = bridge_client
    url = server.ArtifactSigner(settings.bridge_api_key, 300).create_url(
        settings.public_url, "job-1"
    )
    parsed = urlsplit(url)

    response = await client.get(parsed.path + "?" + parsed.query)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"


async def test_artifact_url_survives_process_restart_with_shared_cache(settings: Settings) -> None:
    first_llama = FakeLlamaParseClient()
    first_llama.job_result = {"job": {"status": "COMPLETED"}}
    first_app = create_app(settings, first_llama)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app), base_url="http://bridge.test"
    ) as first_client:
        response = await _wait_for_completed(first_client, settings)
        zip_url = response.json()["result"]["zip_url"]

    restarted_llama = FakeLlamaParseClient()
    restarted_llama.job_result = {"job": {"status": "COMPLETED"}}
    restarted_app = create_app(settings, restarted_llama)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app), base_url="http://bridge.test"
    ) as restarted_client:
        parsed = urlsplit(zip_url)
        artifact = await restarted_client.get(parsed.path + "?" + parsed.query)

    assert artifact.status_code == 200


async def test_artifact_rejects_a_forged_signature(bridge_client: Any) -> None:
    client, _ = bridge_client

    response = await client.get("/artifacts/job-1.zip?expires=9999999999&signature=forged")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_artifact_signature"


async def test_upstream_error_is_returned_to_openviking(
    bridge_client: Any, settings: Settings
) -> None:
    client, llama = bridge_client

    async def fail(_: str, *, include_result: bool = False) -> dict[str, Any]:
        del include_result
        raise LlamaParseError(429, "rate limited")

    llama.get_job = fail
    response = await client.get("/api/v3/responses/job-1", headers=_auth(settings))

    assert response.status_code == 429
    assert response.json() == {"error": {"code": "llamaparse_http_429", "message": "rate limited"}}
