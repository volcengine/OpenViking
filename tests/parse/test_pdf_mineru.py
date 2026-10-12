# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the MinerU protocol flavors in PDFParser.

Covers the three supported protocols:
- v1-sync: legacy self-hosted inline contract (md_content + base64 images),
  unchanged under ``mineru_api_mode="sync"`` and auto-detected under "auto"
- v2-tasks: current self-hosted task API (POST /tasks -> status_url/result_url)
- online-batch: the online batch API (POST /extract/task/batch -> poll
  extract-results -> full_zip_url, Bearer-token auth)

plus the failure surfaces and config validation.
"""

import base64
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig

BASE = "https://mineru.example"
ENDPOINT = f"{BASE}/api/v4"
FILE_PARSE_URL = f"{ENDPOINT}/file_parse"
TASKS_URL = f"{ENDPOINT}/tasks"
BATCH_URL = f"{ENDPOINT}/extract/task/batch"

FAKE_PDF = b"%PDF-1.4 minimal"


class _FakeStorage:
    """Media storage double: records saved images under a fake media dir."""

    def __init__(self, tmp_path: Path):
        self.media_dir = tmp_path / "media"
        self.media_dir.mkdir()
        self.saved = []

    def save_image(self, resource_name, image_bytes, filename="", extension=".png"):
        target = self.media_dir / f"{resource_name}-{filename}{extension}"
        target.write_bytes(image_bytes)
        self.saved.append(target.name)
        return target


class _FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


class _RoutedClient:
    """Routes POST/GET by url substring to scripted response queues.

    One dict for POSTs (endpoint keys) and one for GETs (poll/result keys) so
    a poll url like ``/tasks/<id>/status`` never collides with the ``/tasks``
    POST key. POSTs always replay the first entry; GETs consume entries in
    order and the last one repeats.
    """

    def __init__(self, routes, get_routes=None):
        self._routes = routes
        self._get_routes = get_routes if get_routes is not None else routes
        self.posts = []
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        for key, queue in self._routes.items():
            if key in url:
                return queue[0]
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        for key, queue in self._get_routes.items():
            if key in url:
                if len(queue) > 1:
                    return queue.pop(0)
                return queue[0]
        raise AssertionError(f"unexpected GET {url}")

    def __call__(self, **kwargs):
        return self


def _split(routes):
    """POST endpoint keys vs GET url keys (keys only reachable by GET)."""
    post_keys = ("file_parse", "/tasks", "/extract/task/batch")
    posts = {k: v for k, v in routes.items() if any(p in k for p in post_keys)}
    gets = {k: v for k, v in routes.items() if k not in posts}
    return _RoutedClient(posts, gets)


def _sync_payload(md="# Doc\n\n![img](images/a.jpg)"):
    data_url = "data:image/jpeg;base64," + base64.b64encode(b"jpegdata").decode()
    return {
        "status": "completed",
        "version": 2,
        "backend": "pipeline",
        "task_id": "t-1",
        "results": {"doc.pdf": {"md_content": md, "images": {"a.jpg": data_url}}},
    }


def _v2_create_payload():
    return {
        "task_id": "task-42",
        "status_url": f"{ENDPOINT}/tasks/task-42/status",
        "result_url": f"{ENDPOINT}/tasks/task-42/result",
        "file_names": ["doc.pdf"],
        "queued_ahead": 0,
    }


def _online_create_payload():
    return {"code": 0, "msg": "success", "data": {"batch_id": "batch-77"}}


def _result_zip(md="# Online\n\n![pic](images/pic.jpg)"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", md)
        zf.writestr("images/pic.jpg", b"fakejpg")
        zf.writestr("layout.pdf", b"fakepdf")
    return buf.getvalue()


@pytest.fixture
def pdf_path(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(FAKE_PDF)
    return p


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)


def _parser(**overrides):
    defaults = dict(strategy="mineru", mineru_endpoint=ENDPOINT)
    defaults.update(overrides)
    return PDFParser(PDFConfig(**defaults))


# ---------------------------------------------------------------------------
# v1 sync flavor


@pytest.mark.asyncio
async def test_v1_sync_mode_unchanged(monkeypatch, pdf_path, tmp_path):
    """Explicit sync mode: identical contract as before the task support."""
    client = _RoutedClient({FILE_PARSE_URL: [_FakeResponse(_sync_payload())]})
    parser = _parser(mineru_api_mode="sync")
    _patch_client(monkeypatch, client)
    storage = _FakeStorage(tmp_path)

    md, meta = await parser._convert_mineru(pdf_path, storage=storage, resource_name="doc")

    assert md.startswith("# Doc")
    assert meta["api_mode"] == "v1-sync"
    assert meta["backend"] == "pipeline"
    assert meta["images_saved"] == 1
    assert "doc-a.jpg" in md  # reference rewritten to the stored relative path
    assert "images/a.jpg" not in md
    assert client.gets == []  # no polling on the sync path


@pytest.mark.asyncio
async def test_auto_mode_picks_v1_for_inline_response(monkeypatch, pdf_path, tmp_path):
    client = _RoutedClient({FILE_PARSE_URL: [_FakeResponse(_sync_payload())]})
    parser = _parser()
    _patch_client(monkeypatch, client)

    md, meta = await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))

    assert meta["api_mode"] == "v1-sync"
    assert client.gets == []


@pytest.mark.asyncio
async def test_auth_header_sent_when_token_configured(monkeypatch, pdf_path, tmp_path):
    client = _RoutedClient({FILE_PARSE_URL: [_FakeResponse(_sync_payload())]})
    parser = _parser(mineru_token="tok123")
    _patch_client(monkeypatch, client)

    await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))

    url, kwargs = client.posts[0]
    assert url == FILE_PARSE_URL
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"


# ---------------------------------------------------------------------------
# v2-tasks flavor (self-hosted task API)


def _v2_routes(zip_content=None):
    return {
        TASKS_URL: [_FakeResponse(_v2_create_payload(), status_code=202)],
        "/status": [
            _FakeResponse({"status": "processing"}),
            _FakeResponse({"status": "completed"}),
        ],
        "/result": [_FakeResponse(content=zip_content or _result_zip())],
    }


@pytest.mark.asyncio
async def test_async_mode_uses_v2_tasks_flow(monkeypatch, pdf_path, tmp_path):
    """Explicit async mode: /tasks -> 202 -> poll status -> download result zip."""
    client = _split(_v2_routes())
    parser = _parser(mineru_api_mode="async", mineru_timeout=30)
    _patch_client(monkeypatch, client)
    storage = _FakeStorage(tmp_path)

    md, meta = await parser._convert_mineru(pdf_path, storage=storage, resource_name="doc")

    assert meta["api_mode"] == "v2-tasks"
    assert meta["task_id"] == "task-42"
    assert meta["images_saved"] == 1
    assert "doc-pic.jpg" in md
    assert "images/pic.jpg" not in md
    # upload went to /tasks with the v2 form defaults
    url, kwargs = client.posts[0]
    assert url == TASKS_URL
    assert kwargs["data"]["response_format_zip"] == "true"
    assert kwargs["data"]["return_md"] == "true"


@pytest.mark.asyncio
async def test_auto_mode_falls_back_to_tasks_on_404(monkeypatch, pdf_path, tmp_path):
    """auto: /file_parse 404 -> probe /tasks -> v2 flow."""
    routes = _v2_routes()
    routes[FILE_PARSE_URL] = [_FakeResponse(status_code=404)]
    client = _split(routes)
    parser = _parser()
    _patch_client(monkeypatch, client)

    md, meta = await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))

    assert meta["api_mode"] == "v2-tasks"
    assert md.startswith("# Online")


@pytest.mark.asyncio
async def test_auto_mode_accepts_task_shaped_file_parse_response(monkeypatch, pdf_path, tmp_path):
    """auto: /file_parse answering 200 with a task payload -> continue the task."""
    routes = {
        FILE_PARSE_URL: [_FakeResponse(_v2_create_payload())],
        "/status": [_FakeResponse({"status": "completed"})],
        "/result": [_FakeResponse(content=_result_zip())],
    }
    client = _split(routes)
    parser = _parser()
    _patch_client(monkeypatch, client)

    md, meta = await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))

    assert meta["api_mode"] == "v2-tasks"
    assert md.startswith("# Online")
    assert len(client.posts) == 1  # no re-upload on the fallback


@pytest.mark.asyncio
async def test_v2_task_failed_raises_with_detail(monkeypatch, pdf_path, tmp_path):
    routes = {
        TASKS_URL: [_FakeResponse(_v2_create_payload(), status_code=202)],
        "/status": [_FakeResponse({"status": "failed", "msg": "bad pdf"})],
    }
    client = _split(routes)
    parser = _parser(mineru_api_mode="async", mineru_timeout=5)
    _patch_client(monkeypatch, client)

    with pytest.raises(ValueError, match="bad pdf"):
        await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))


# ---------------------------------------------------------------------------
# online-batch flavor


def _online_routes(zip_content=None):
    poll = f"{ENDPOINT}/extract-results/batch/batch-77"
    done = {
        "code": 0,
        "data": {
            "extract_result": [
                {"file_name": "doc.pdf", "state": "done", "full_zip_url": "https://cdn.example/result.zip"}
            ]
        },
    }
    return {
        BATCH_URL: [_FakeResponse(_online_create_payload())],
        poll: [
            _FakeResponse({"code": 0, "data": {"extract_result": [{"state": "running"}]}}),
            _FakeResponse(done),
        ],
        "cdn.example/result.zip": [_FakeResponse(content=zip_content or _result_zip())],
    }


@pytest.mark.asyncio
async def test_async_mode_falls_through_to_online_batch(monkeypatch, pdf_path, tmp_path):
    """Explicit async mode: /tasks 404 -> online batch endpoint."""
    routes = _online_routes()
    routes[TASKS_URL] = [_FakeResponse(status_code=404)]
    client = _split(routes)
    parser = _parser(mineru_api_mode="async", mineru_timeout=30, mineru_token="tok")
    _patch_client(monkeypatch, client)
    storage = _FakeStorage(tmp_path)

    md, meta = await parser._convert_mineru(pdf_path, storage=storage, resource_name="doc")

    assert meta["api_mode"] == "online-batch"
    assert meta["task_id"] == "batch-77"
    assert meta["images_saved"] == 1
    assert "doc-pic.jpg" in md
    poll_urls = [u for u, _ in client.gets]
    assert any("/extract-results/batch/batch-77" in u for u in poll_urls)
    assert poll_urls[-1] == "https://cdn.example/result.zip"


@pytest.mark.asyncio
async def test_online_task_failed_raises_with_detail(monkeypatch, pdf_path, tmp_path):
    poll = f"{ENDPOINT}/extract-results/batch/batch-77"
    routes = {
        TASKS_URL: [_FakeResponse(status_code=404)],
        BATCH_URL: [_FakeResponse(_online_create_payload())],
        poll: [
            _FakeResponse(
                {"code": 0, "data": {"extract_result": [{"state": "failed", "err_msg": "quota exhausted"}]}}
            )
        ],
    }
    client = _split(routes)
    parser = _parser(mineru_api_mode="async", mineru_timeout=5)
    _patch_client(monkeypatch, client)

    with pytest.raises(ValueError, match="quota exhausted"):
        await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))


# ---------------------------------------------------------------------------
# failure surfaces


@pytest.mark.asyncio
async def test_async_timeout_raises(monkeypatch, pdf_path, tmp_path):
    routes = {
        TASKS_URL: [_FakeResponse(_v2_create_payload(), status_code=202)],
        "/status": [_FakeResponse({"status": "processing"})],
    }
    client = _split(routes)
    parser = _parser(mineru_api_mode="async", mineru_timeout=0.5)
    _patch_client(monkeypatch, client)

    real_sleep = __import__("asyncio").sleep

    async def fast_sleep(seconds):
        await real_sleep(0)

    monkeypatch.setattr("openviking.parse.parsers.pdf.asyncio.sleep", fast_sleep)

    with pytest.raises(TimeoutError):
        await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))


@pytest.mark.asyncio
async def test_async_no_task_endpoint_raises_helpful_error(monkeypatch, pdf_path, tmp_path):
    """Both task endpoints 404 -> actionable message mentioning the token."""
    routes = {
        TASKS_URL: [_FakeResponse(status_code=404)],
        BATCH_URL: [_FakeResponse(status_code=404)],
    }
    client = _split(routes)
    parser = _parser(mineru_api_mode="async")
    _patch_client(monkeypatch, client)

    with pytest.raises(ValueError, match="mineru_token"):
        await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))


@pytest.mark.asyncio
async def test_async_zip_without_markdown_raises(monkeypatch, pdf_path, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("layout.pdf", b"only")

    client = _split(_v2_routes(zip_content=buf.getvalue()))
    parser = _parser(mineru_api_mode="async", mineru_timeout=10)
    _patch_client(monkeypatch, client)

    with pytest.raises(ValueError, match="[Nn]o markdown"):
        await parser._convert_mineru(pdf_path, storage=_FakeStorage(tmp_path))


# ---------------------------------------------------------------------------
# config validation


def test_api_mode_validation_rejects_unknown_value():
    with pytest.raises(ValueError, match="mineru_api_mode"):
        PDFConfig(strategy="mineru", mineru_endpoint=ENDPOINT, mineru_api_mode="fast").validate()


def test_api_mode_accepts_all_documented_values():
    for mode in ("auto", "sync", "async"):
        PDFConfig(strategy="mineru", mineru_endpoint=ENDPOINT, mineru_api_mode=mode).validate()
