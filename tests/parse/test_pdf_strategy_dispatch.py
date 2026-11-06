"""Tests for PDF strategy dispatch.

``auto`` uses whichever remote parser is configured and falls back to the local one,
so the contract worth protecting is:

- with a remote service configured, it is tried *before* local;
- if it fails, the document is still converted, by local;
- with nothing configured, local runs and no remote call is made.

``mineru`` stays strict: a failure is the result.
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.utils.config.parser_config import PDFConfig

ENDPOINT = "http://127.0.0.1:8000"
MINERU_MD = "# From MinerU"
LOCAL_MD = "# From local pdfplumber"


@pytest.fixture
def pdf_path(tmp_path) -> Path:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 minimal")
    return p


def _parser(**overrides) -> PDFParser:
    defaults = {"strategy": "auto"}
    defaults.update(overrides)
    return PDFParser(PDFConfig(**defaults))


def _stub_converters(monkeypatch, parser, *, mineru):
    """Replace both conversion paths so the test exercises dispatch, not HTTP."""
    calls = {"mineru": 0, "local": 0}

    async def fake_mineru(self, pdf_path, storage=None, resource_name=None):
        calls["mineru"] += 1
        if isinstance(mineru, Exception):
            raise mineru
        return mineru

    async def fake_local(self, pdf_path, storage=None, resource_name=None):
        calls["local"] += 1
        return LOCAL_MD, {"strategy": "local"}

    monkeypatch.setattr(PDFParser, "_convert_mineru", fake_mineru)
    monkeypatch.setattr(PDFParser, "_convert_local", fake_local)
    return calls


# ---------------------------------------------------------------------------
# auto: configured remote first, local as the fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_prefers_configured_mineru(monkeypatch, pdf_path):
    parser = _parser(mineru_endpoint=ENDPOINT)
    calls = _stub_converters(monkeypatch, parser, mineru=(MINERU_MD, {"strategy": "mineru"}))

    md, meta = await parser._convert_to_markdown(pdf_path)

    assert md == MINERU_MD
    assert meta["strategy"] == "mineru"
    # Remote first means local is not even called when the remote succeeds.
    assert calls == {"mineru": 1, "local": 0}


@pytest.mark.asyncio
async def test_auto_falls_back_to_local_when_mineru_raises(monkeypatch, pdf_path):
    """The behaviour that matters: a remote failure degrades, it does not fail."""
    parser = _parser(mineru_endpoint=ENDPOINT)
    calls = _stub_converters(
        monkeypatch, parser, mineru=RuntimeError("MinerU endpoint unreachable")
    )

    md, meta = await parser._convert_to_markdown(pdf_path)

    assert md == LOCAL_MD
    assert meta["strategy"] == "local"
    assert calls == {"mineru": 1, "local": 1}


@pytest.mark.asyncio
async def test_auto_falls_back_to_local_on_timeout(monkeypatch, pdf_path):
    parser = _parser(mineru_endpoint=ENDPOINT)
    _stub_converters(monkeypatch, parser, mineru=TimeoutError("timed out"))

    md, meta = await parser._convert_to_markdown(pdf_path)

    assert md == LOCAL_MD
    assert meta["strategy"] == "local"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [404, 405, 429, 500, 503],
)
def test_auto_falls_back_on_any_http_error(monkeypatch, pdf_path, status):
    """Every remote failure mode must degrade to local, not fail the import.

    404/405 matter in practice: the hosted MinerU service has no ``/file_parse``
    route at all (its contract is the async batch flow), so a user who points
    ``mineru_endpoint`` at it under ``auto`` hits 404 on every single document.
    """
    import httpx as _httpx

    parser = _parser(mineru_endpoint=ENDPOINT)

    async def boom(self, pdf_path, storage=None, resource_name=None):
        raise _httpx.HTTPStatusError(f"HTTP {status}", request=None, response=None)

    async def ok_local(self, pdf_path, storage=None, resource_name=None):
        return LOCAL_MD, {"strategy": "local"}

    monkeypatch.setattr(PDFParser, "_convert_mineru", boom)
    monkeypatch.setattr(PDFParser, "_convert_local", ok_local)

    md, meta = asyncio.run(parser._convert_to_markdown(pdf_path))

    assert md == LOCAL_MD
    assert meta["strategy"] == "local"


@pytest.mark.asyncio
async def test_auto_uses_local_when_nothing_configured(monkeypatch, pdf_path):
    """No endpoint means no remote service is in play; local must not be bypassed."""
    parser = _parser()
    calls = _stub_converters(monkeypatch, parser, mineru=(MINERU_MD, {"strategy": "mineru"}))

    md, meta = await parser._convert_to_markdown(pdf_path)

    assert md == LOCAL_MD
    assert calls == {"mineru": 0, "local": 1}


def test_remote_converters_only_lists_configured_services():
    assert _parser()._remote_converters() == []
    providers = _parser(mineru_endpoint=ENDPOINT)._remote_converters()
    assert [name for name, _ in providers] == ["MinerU"]


def test_mineru_enabled_is_the_single_source_of_truth():
    """Both the parser dispatch and the startup preflight read this one predicate."""
    assert PDFConfig(strategy="auto").mineru_enabled is False
    assert PDFConfig(strategy="auto", mineru_endpoint="").mineru_enabled is False
    assert PDFConfig(strategy="auto", mineru_endpoint=ENDPOINT).mineru_enabled is True
    # A token alone does not enable remote parsing: it is a credential, not an endpoint.
    assert PDFConfig(strategy="auto", mineru_token="t").mineru_enabled is False


@pytest.mark.asyncio
async def test_local_strategy_never_calls_the_remote(monkeypatch, pdf_path):
    parser = _parser(strategy="local", mineru_endpoint=ENDPOINT)
    calls = _stub_converters(monkeypatch, parser, mineru=(MINERU_MD, {"strategy": "mineru"}))

    md, _ = await parser._convert_to_markdown(pdf_path)

    assert md == LOCAL_MD
    assert calls == {"mineru": 0, "local": 1}


# ---------------------------------------------------------------------------
# mineru: strict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mineru_strategy_does_not_fall_back(monkeypatch, pdf_path):
    parser = _parser(strategy="mineru", mineru_endpoint=ENDPOINT)
    calls = _stub_converters(
        monkeypatch, parser, mineru=RuntimeError("MinerU endpoint unreachable")
    )

    with pytest.raises(RuntimeError):
        await parser._convert_to_markdown(pdf_path)

    assert calls == {"mineru": 1, "local": 0}


# ---------------------------------------------------------------------------
# config validation
# ---------------------------------------------------------------------------


def test_strategy_is_accepted():
    for strategy in ("local", "auto"):
        PDFConfig(strategy=strategy).validate()
    PDFConfig(strategy="mineru", mineru_endpoint=ENDPOINT).validate()


def test_endpoint_is_required_for_mineru():
    with pytest.raises(ValueError, match="mineru_endpoint"):
        PDFConfig(strategy="mineru").validate()


def test_endpoint_is_optional_for_auto():
    PDFConfig(strategy="auto").validate()


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="Invalid strategy"):
        PDFConfig(strategy="mineru_first", mineru_endpoint=ENDPOINT).validate()


# ---------------------------------------------------------------------------
# hosted-service auth
# ---------------------------------------------------------------------------


def test_token_is_sent_as_bearer(monkeypatch, pdf_path):
    """The hosted service needs a bearer token; verify it reaches the request."""
    captured = {}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers") or {}
        raise RuntimeError("stop here - only the request shape matters")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    parser = _parser(strategy="mineru", mineru_endpoint=ENDPOINT, mineru_token="secret-token")
    assert parser._mineru_headers() == {"Authorization": "Bearer secret-token"}

    # _convert_mineru logs and re-raises transport failures; we only care that the
    # call went out carrying the token.
    with pytest.raises(RuntimeError, match="only the request shape matters"):
        asyncio.run(parser._convert_mineru(pdf_path))

    assert captured["headers"] == {"Authorization": "Bearer secret-token"}
    assert captured["url"].endswith("/file_parse")


def test_no_token_means_no_auth_header():
    parser = _parser(mineru_endpoint=ENDPOINT)
    assert parser._mineru_headers() == {}


# ---------------------------------------------------------------------------
# protocol selection: a token means the hosted API, no token means self-hosted
# ---------------------------------------------------------------------------


def test_token_selects_the_hosted_batch_protocol(monkeypatch):
    """The hosted service has no /file_parse, so a token must pick the batch flow."""
    seen = []

    async def fake_batch(self, client, meta, pdf_path, storage=None, resource_name=None):
        seen.append("batch")
        return MINERU_MD, {"strategy": "mineru", "api_mode": "v4-batch"}

    async def fake_sync(self, client, meta, pdf_path, storage=None, resource_name=None):
        seen.append("sync")
        return MINERU_MD, {"strategy": "mineru", "api_mode": "file-parse"}

    monkeypatch.setattr(PDFParser, "_mineru_hosted_batch", fake_batch)
    monkeypatch.setattr(PDFParser, "_mineru_selfhosted", fake_sync)

    parser = _parser(strategy="mineru", mineru_endpoint=ENDPOINT, mineru_token="t")
    _, meta = asyncio.run(parser._convert_to_markdown(Path("x.pdf")))

    assert seen == ["batch"]
    assert meta["api_mode"] == "v4-batch"


def test_no_token_keeps_the_self_hosted_route(monkeypatch):
    """Backward compatibility: a tokenless endpoint keeps using /file_parse."""
    seen = []

    async def fake_batch(self, client, meta, pdf_path, storage=None, resource_name=None):
        seen.append("batch")
        return MINERU_MD, {"strategy": "mineru"}

    async def fake_sync(self, client, meta, pdf_path, storage=None, resource_name=None):
        seen.append("sync")
        return MINERU_MD, {"strategy": "mineru", "api_mode": "file-parse"}

    monkeypatch.setattr(PDFParser, "_mineru_hosted_batch", fake_batch)
    monkeypatch.setattr(PDFParser, "_mineru_selfhosted", fake_sync)

    parser = _parser(strategy="mineru", mineru_endpoint=ENDPOINT)
    _, meta = asyncio.run(parser._convert_to_markdown(Path("x.pdf")))

    assert seen == ["sync"]
    assert meta["api_mode"] == "file-parse"


def test_hosted_batch_failure_falls_back_to_self_hosted(monkeypatch):
    """A rejected token must not sink the parse if the endpoint still speaks v1."""
    seen = []

    async def refuse(self, client, base, pdf_path):
        raise RuntimeError("token rejected")

    async def fake_sync(self, client, meta, pdf_path, storage=None, resource_name=None):
        seen.append("sync")
        return MINERU_MD, {"strategy": "mineru", "api_mode": "file-parse"}

    monkeypatch.setattr(PDFParser, "_mineru_submit_file_batch", refuse)
    monkeypatch.setattr(PDFParser, "_mineru_selfhosted", fake_sync)

    parser = _parser(strategy="mineru", mineru_endpoint=ENDPOINT, mineru_token="t")
    _, meta = asyncio.run(parser._convert_to_markdown(Path("x.pdf")))

    assert seen == ["sync"]
    assert meta["api_mode"] == "file-parse"


# ---------------------------------------------------------------------------
# regressions raised in review: default-config startup, credential isolation,
# and image-reference rewriting
# ---------------------------------------------------------------------------


def test_default_pdfconfig_reports_no_remote_provider():
    """A deployment that never configured MinerU must still start.

    ``remote_enabled`` is read on the default ``auto`` path during service start-up,
    so it has to cope with the default ``None`` endpoint instead of raising.
    """
    from openviking_cli.utils.config.parser_config import PDFConfig

    config = PDFConfig()
    assert config.mineru_endpoint is None
    assert config.remote_enabled is False


def test_remote_enabled_true_only_for_a_configured_endpoint():
    from openviking_cli.utils.config.parser_config import PDFConfig

    config = PDFConfig()
    config.mineru_endpoint = "https://mineru.example"
    assert config.remote_enabled is True

    config.mineru_endpoint = ""
    assert config.remote_enabled is False


def test_presigned_zip_download_carries_no_mineru_token(monkeypatch):
    """The archive lives on the storage host, so the MinerU token must not go there."""
    from openviking_cli.utils.config.parser_config import PDFConfig

    parser = PDFParser(PDFConfig(mineru_endpoint=ENDPOINT, mineru_token="secret-token"))
    seen = {}

    class _Resp:
        content = b"zip-bytes"

        def raise_for_status(self):
            return None

    class _Client:
        async def get(self, url, **kwargs):
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            seen["kwargs"] = kwargs
            return _Resp()

    zip_url = "https://oss.storage.example/batch/abc.zip?signature=deadbeef"
    asyncio.run(parser._mineru_download_zip(_Client(), zip_url))

    assert seen["url"] == zip_url
    assert "headers" not in seen["kwargs"], "a token-bearing header must not be sent"
    # The API headers still carry the token; they are simply not used for the
    # cross-host download.
    assert parser._mineru_headers()["Authorization"] == "Bearer secret-token"


def test_self_hosted_images_are_saved_and_references_rewritten(monkeypatch, pdf_path):
    """A saved image whose markdown still points at the provider path is broken."""
    from openviking_cli.utils.config.parser_config import PDFConfig

    monkeypatch.setattr(
        PDFParser,
        "_mineru_base_url",
        lambda self: ENDPOINT,
    )
    monkeypatch.setattr(
        PDFParser,
        "_mineru_file_parse_url",
        lambda self: ENDPOINT + "/file_parse",
    )

    markdown = "see ![fig](images/figure.png) for detail"
    # /file_parse answers with results keyed by file name
    payload = {
        "results": {
            pdf_path.name: {
                "md_content": markdown,
                "images": {"images/figure.png": "ZmFrZQ=="},
            }
        }
    }

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class _Client:
        async def post(self, *a, **kw):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    saved = {}

    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            target = Path("/media") / resource_name / (filename + extension)
            saved["path"] = target
            saved["blob"] = blob
            return target

    monkeypatch.setattr(
        "openviking.parse.parsers.pdf.lazy_import",
        lambda name: __import__("httpx") if name == "httpx" else None,
        raising=False,
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: _Client())

    parser = PDFParser(PDFConfig(strategy="mineru", mineru_endpoint=ENDPOINT))
    text, meta = asyncio.run(parser._mineru_selfhosted(_Client(), {}, pdf_path, _Storage(), "math"))

    assert meta["images_saved"] == 1
    assert saved["path"].name == "figure.png"
    # the reference must now resolve to where the image was actually written
    assert "images/figure.png" not in text
    assert "math/figure.png" in text


# ---------------------------------------------------------------------------
# hosted v4 batch protocol: upload handshake, polling, archive unpacking
# ---------------------------------------------------------------------------


async def _no_sleep(_seconds):
    """Poll without waiting, so the timeout test does not spend real seconds."""
    return None


def _zip_bytes(entries):
    """Build an in-memory zip from {name: text-bytes}."""
    import io as _io
    import zipfile

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in entries.items():
            zf.writestr(name, blob)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _HostedClient:
    """Replays the hosted v4 conversation: reserve url, PUT, poll, download."""

    def __init__(self, polls, zip_url="https://oss.example/result.zip", zip_payload=b""):
        self.polls = list(polls)
        self.zip_url = zip_url
        self.zip_payload = zip_payload
        self.calls = []

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _FakeResponse(
            {
                "code": 0,
                "data": {
                    "batch_id": "batch-1",
                    "file_urls": ["https://oss.example/upload/doc.pdf"],
                },
            }
        )

    async def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        return _FakeResponse()

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if "/extract-results/" in url:
            if not self.polls:
                raise AssertionError("polled more times than the test scripted")
            return _FakeResponse(self.polls.pop(0))
        return _FakeResponse(content=self.zip_payload)


def _hosted_parser(timeout=30.0):
    from openviking_cli.utils.config.parser_config import PDFConfig

    return PDFParser(
        PDFConfig(
            strategy="mineru",
            mineru_endpoint="https://mineru.example/api",
            mineru_token="tok",
            mineru_timeout=timeout,
        )
    )


def test_hosted_batch_reserves_uploads_polls_and_unpacks(monkeypatch, pdf_path):
    archive = _zip_bytes(
        {
            "full.md": b"# Title\n\n![fig](images/fig-1.png)",
            "images/fig-1.png": b"\x89PNG-bytes",
        }
    )
    client = _HostedClient(
        polls=[
            {"code": 0, "data": {"extract_result": [{"state": "waiting-file"}]}},
            {
                "code": 0,
                "data": {
                    "extract_result": [
                        {"state": "done", "full_zip_url": "https://oss.example/result.zip"}
                    ]
                },
            },
        ],
        zip_payload=archive,
    )

    saved = {}

    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            saved[(resource_name, filename, extension)] = blob
            return Path("/media") / resource_name / (filename + extension)

    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: client)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    parser = _hosted_parser()
    meta = {}
    text, meta = asyncio.run(parser._mineru_hosted_batch(client, meta, pdf_path, _Storage(), "doc"))

    assert text.startswith("# Title")
    # the image reference must point at where the file was written
    assert "images/fig-1.png" not in text
    assert "doc/fig-1.png" in text
    assert meta["api_version"] == "task-zip"
    assert meta["images_saved"] == 1
    assert meta["batch_id"] == "batch-1"
    assert saved[("doc", "fig-1", ".png")] == b"\x89PNG-bytes"

    verbs = [c[0] for c in client.calls]
    assert verbs[0] == "POST" and "/file-urls/batch" in client.calls[0][1]
    assert verbs[1] == "PUT"
    # the pre-signed upload carries no Content-Type and no MinerU bearer
    assert client.calls[1][2]["headers"] == {"Content-Type": ""}
    # the archive download must not carry the token to the storage host
    zip_get = [c for c in client.calls if c[0] == "GET" and "result.zip" in c[1]][0]
    assert "headers" not in zip_get[2]


def test_hosted_batch_times_out_without_a_result(monkeypatch, pdf_path):
    client = _HostedClient(polls=[{"data": {"extract_result": [{"state": "running"}]}}] * 3)
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: client)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    parser = _hosted_parser(timeout=5.0)
    try:
        asyncio.run(parser._mineru_hosted_batch(client, {}, pdf_path, None, "doc"))
    except TimeoutError as e:
        assert "did not finish" in str(e)
    else:
        raise AssertionError("expected TimeoutError")


def test_hosted_batch_reports_a_failed_batch(monkeypatch, pdf_path):
    client = _HostedClient(
        polls=[{"data": {"extract_result": [{"state": "failed", "err_msg": "bad pdf"}]}}]
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: client)

    parser = _hosted_parser()
    try:
        asyncio.run(parser._mineru_hosted_batch(client, {}, pdf_path, None, "doc"))
    except ValueError as e:
        assert "err_msg=bad pdf" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_hosted_batch_done_without_zip_url_is_an_error(monkeypatch, pdf_path):
    client = _HostedClient(polls=[{"data": {"extract_result": [{"state": "done"}]}}])
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: client)

    parser = _hosted_parser()
    try:
        asyncio.run(parser._mineru_hosted_batch(client, {}, pdf_path, None, "doc"))
    except ValueError as e:
        assert "without a result zip" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_submit_file_batch_rejects_a_reply_without_urls(monkeypatch, pdf_path):
    class _NoUrls(_HostedClient):
        async def post(self, url, **kw):
            return _FakeResponse({"code": 0, "data": {"file_urls": []}})

    parser = _hosted_parser()
    try:
        asyncio.run(
            parser._mineru_submit_file_batch(_NoUrls([]), "https://mineru.example/api", pdf_path)
        )
    except ValueError as e:
        assert "no file_urls" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_post_json_raises_on_a_business_error_code():
    import httpx

    from openviking_cli.utils.config.parser_config import PDFConfig

    parser = PDFParser(PDFConfig(mineru_endpoint="https://x", mineru_token="t"))

    class _Client:
        async def post(self, *a, **kw):
            return _FakeResponse({"code": 4001, "msg": "quota exceeded"})

    try:
        asyncio.run(parser._mineru_post_json(_Client(), "https://x/y", {}))
    except RuntimeError as e:
        assert "code=4001" in str(e) and "quota exceeded" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
    assert httpx is not None


def test_pick_markdown_prefers_full_md(tmp_path):
    (tmp_path / "full.md").write_text("full", encoding="utf-8")
    (tmp_path / "other.md").write_text("other", encoding="utf-8")
    nested = tmp_path / "deep" / "deeper"
    nested.mkdir(parents=True)
    (nested / "x.md").write_text("x", encoding="utf-8")

    assert PDFParser._mineru_pick_markdown(tmp_path).name == "full.md"


def test_pick_markdown_returns_the_only_candidate_and_none_for_empty(tmp_path):
    assert PDFParser._mineru_pick_markdown(tmp_path) is None
    only = tmp_path / "doc.md"
    only.write_text("x", encoding="utf-8")
    assert PDFParser._mineru_pick_markdown(tmp_path) == only


def test_pick_markdown_falls_back_to_the_shallowest_largest(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "big.md").write_text("x" * 50, encoding="utf-8")
    (tmp_path / "small.md").write_text("y", encoding="utf-8")
    assert PDFParser._mineru_pick_markdown(tmp_path).parent == tmp_path


def test_unpack_zip_without_markdown_is_an_error(pdf_path):
    parser = _hosted_parser()
    try:
        parser._mineru_unpack_zip(_zip_bytes({"images/a.png": b"x"}), pdf_path, {})
    except ValueError as e:
        assert "No markdown file found" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_unpack_zip_handles_empty_markdown(pdf_path):
    parser = _hosted_parser()
    text, meta = parser._mineru_unpack_zip(_zip_bytes({"full.md": b""}), pdf_path, {})
    assert text == ""
    assert meta["api_version"] == "task-zip"


def test_online_state_helpers():
    entry = lambda **kw: {"data": {"extract_result": [kw]}}  # noqa: E731
    assert PDFParser._mineru_online_done(entry(state="done"))
    assert not PDFParser._mineru_online_done(entry(state="running"))
    assert not PDFParser._mineru_online_done({})
    for busy in ("waiting", "waiting-file", "uploading", "pending", "running", "converting"):
        assert PDFParser._mineru_online_busy(entry(state=busy)), busy
    assert not PDFParser._mineru_online_busy(entry(state="done"))
    assert PDFParser._mineru_online_result_entry({"extract_result": []}) is None
    assert PDFParser._mineru_online_result_entry({"extract_result": [1]}) is None


def test_online_zip_url_and_failure_detail():
    good = {"extract_result": [{"full_zip_url": "https://a/b.zip"}]}
    assert PDFParser._mineru_online_zip_url(good) == "https://a/b.zip"
    assert PDFParser._mineru_online_zip_url({"extract_result": [{"zip_url": "ftp://x"}]}) is None
    assert PDFParser._mineru_online_zip_url({}) is None
    assert (
        PDFParser._mineru_online_failure_detail({"extract_result": [{"err_no": "E9"}]})
        == "err_no=E9"
    )
    # with nothing on the entry, the generic failure detail still yields text
    assert PDFParser._mineru_online_failure_detail({"msg": "nope"})


def test_extract_markdown_collects_only_image_files(tmp_path):
    (tmp_path / "full.md").write_text("# t", encoding="utf-8")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"p")
    (tmp_path / "images" / "b.txt").write_bytes(b"t")

    from openviking_cli.utils.config.parser_config import PDFConfig

    parser = PDFParser(PDFConfig(mineru_endpoint="https://x"))
    text, images = parser._mineru_extract_markdown(tmp_path / "full.md", tmp_path)
    assert text == "# t"
    assert list(images) == ["images/a.png"]


def test_decode_inline_images_skips_bad_payloads():
    decoded = PDFParser._mineru_decode_inline_images(
        {"ok.png": "data:image/png;base64,ZmFrZQ==", "bad.png": "!!!not base64!!!"}
    )
    assert decoded == {"ok.png": b"fake"}
    assert PDFParser._mineru_decode_inline_images(None) == {}


def test_save_images_returns_empty_and_survives_a_failing_store():
    from openviking_cli.utils.config.parser_config import PDFConfig

    parser = PDFParser(PDFConfig(mineru_endpoint="https://x"))
    assert asyncio.run(parser._mineru_save_images({}, None, None)) == {}

    class _Broken:
        media_dir = Path("/media")

        def save_image(self, *a, **kw):
            raise OSError("disk full")

    assert asyncio.run(parser._mineru_save_images({"a.png": b"x"}, _Broken(), "doc")) == {}


def test_convert_mineru_requires_an_endpoint():

    parser = _hosted_parser()
    parser.config.mineru_endpoint = ""
    try:
        asyncio.run(parser._convert_mineru(Path("x.pdf")))
    except ValueError as e:
        assert "endpoint not configured" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_payload_unwrapping_and_sync_entry_edges(tmp_path):
    assert PDFParser._mineru_payload_data({"data": {"k": 1}}) == {"k": 1}
    assert PDFParser._mineru_payload_data({"k": 1}) == {"k": 1}
    assert PDFParser._mineru_payload_data(["not", "a", "dict"]) == ["not", "a", "dict"]
    # a truthy non-dict results value must be rejected, not iterated
    assert PDFParser._mineru_sync_entry({"results": "oops"}, tmp_path / "a.pdf") is None
    assert PDFParser._mineru_sync_entry({"results": []}, tmp_path / "a.pdf") is None
    assert PDFParser._mineru_sync_entry({"results": {"a.pdf": "s"}}, tmp_path / "a.pdf") is None
    assert PDFParser._mineru_sync_entry(
        {"results": {"b.pdf": {"md_content": "x"}}}, tmp_path / "a.pdf"
    ) == {"md_content": "x"}


def test_http_status_reads_the_status_off_an_exception():
    import httpx

    req = httpx.Request("GET", "https://x")
    resp = httpx.Response(503, request=req)
    exc = httpx.HTTPStatusError("boom", request=req, response=resp)
    assert PDFParser._http_status(exc) == 503
    assert PDFParser._http_status(ValueError("no response")) is None


def test_failure_detail_falls_back_when_the_reply_says_nothing():
    assert PDFParser._mineru_online_failure_detail({}) == "no failure detail reported"
    # a reason on the batch envelope still surfaces when the entry is silent
    assert PDFParser._mineru_online_failure_detail({"data": {"msg": "quota"}}) == "msg=quota"


def test_unpack_zip_defaults_resource_name_and_skips_subdirectories(pdf_path):
    archive = _zip_bytes(
        {
            "full.md": b"![a](images/a.png)",
            "images/a.png": b"png",
            "images/nested-dir/x": b"junk",
        }
    )
    saved = {}

    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            saved["resource"] = resource_name
            return Path("/media") / resource_name / (filename + extension)

    parser = _hosted_parser()
    # resource_name=None must fall back to the pdf stem
    text, meta = parser._mineru_unpack_zip(archive, pdf_path, {}, _Storage(), None)
    assert saved["resource"] == pdf_path.stem
    assert meta["images_saved"] == 1
    assert "images/a.png" not in text


def test_unpack_zip_uses_the_default_storage_when_none_is_given(pdf_path, monkeypatch):
    archive = _zip_bytes({"full.md": b"![a](images/a.png)", "images/a.png": b"png"})

    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            return Path("/media") / resource_name / (filename + extension)

    monkeypatch.setattr(
        "openviking_cli.utils.storage.get_storage", lambda: _Storage(), raising=False
    )
    parser = _hosted_parser()
    text, meta = parser._mineru_unpack_zip(archive, pdf_path, {}, None, "doc")
    assert meta["images_saved"] == 1
    assert "doc/a.png" in text


def test_selfhosted_without_markdown_is_an_error(pdf_path):
    class _Client:
        async def post(self, *a, **kw):
            return _FakeResponse({"results": {}})

    parser = _parser(strategy="mineru", mineru_endpoint="https://mineru.example")
    try:
        asyncio.run(parser._mineru_selfhosted(_Client(), {}, pdf_path, None, "doc"))
    except ValueError as e:
        assert "returned no markdown" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_save_images_falls_back_to_the_default_storage(monkeypatch):
    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            return Path("/media") / resource_name / (filename + extension)

    monkeypatch.setattr(
        "openviking_cli.utils.storage.get_storage", lambda: _Storage(), raising=False
    )
    parser = _hosted_parser()
    repl = asyncio.run(parser._mineru_save_images({"images/a.png": b"png"}, None, "doc"))
    assert repl == {"images/a.png": "doc/a.png"}


def test_parse_records_the_effective_strategy(monkeypatch, pdf_path):
    """`auto` records which converter ran, not only what was requested."""
    from openviking.parse.base import NodeType, ResourceNode, create_parse_result

    class _MD:
        async def parse_content(self, content, **kw):
            return create_parse_result(
                root=ResourceNode(type=NodeType.ROOT),
                source_path=kw.get("source_path", ""),
                source_format="pdf",
                parser_name="MarkdownParser",
                parse_time=0.0,
            )

    class _Storage:
        media_dir = Path("/media")

    parser = _hosted_parser()
    parser.config.strategy = "auto"

    async def _remote(pdf, resource_name=None):
        return "remote markdown", {"strategy": "mineru"}

    monkeypatch.setattr(type(parser), "_remote_converters", lambda self: [("mineru", _remote)])
    monkeypatch.setattr(type(parser), "_get_markdown_parser", lambda self: _MD())
    monkeypatch.setattr(
        "openviking_cli.utils.storage.get_storage", lambda: _Storage(), raising=False
    )

    result = asyncio.run(parser.parse(pdf_path))
    assert result.meta["pdf_strategy"] == "auto"
    assert result.meta["pdf_strategy_effective"] == "mineru"


# ---------------------------------------------------------------------------
# review round 3: hosted preflight, self-hosted image keys, failure status
# ---------------------------------------------------------------------------


def test_hosted_preflight_accepts_the_missing_health_route(monkeypatch):
    """The hosted API has no /health; a 404 there must not fail startup.

    Probing it with the self-hosted contract would reject a service that is up and
    correctly configured, which is what blocked strategy="mineru" at boot.
    """
    import openviking.service.mineru_preflight as pf

    seen = {}

    class _Resp:
        status_code = 404

        def json(self):
            return {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            seen["url"] = url
            seen["headers"] = kw.get("headers")
            return _Resp()

    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kw: _Client())
    asyncio.run(
        pf.wait_for_mineru_ready(
            "https://mineru.example/api/v4", timeout=1, hosted=True, token="tok"
        )
    )
    # a real route was probed, not /health
    assert "/health" not in seen["url"]
    assert seen["headers"] == {"Authorization": "Bearer tok"}


def test_hosted_preflight_still_rejects_a_rejected_token(monkeypatch):
    import openviking.service.mineru_preflight as pf

    class _Resp:
        status_code = 401

        def json(self):
            return {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            return _Resp()

    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kw: _Client())
    try:
        asyncio.run(
            pf.wait_for_mineru_ready(
                "https://mineru.example/api/v4", timeout=0.2, hosted=True, token="bad"
            )
        )
    except RuntimeError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("a rejected token must fail the preflight")


def test_selfhosted_preflight_still_demands_the_health_contract(monkeypatch):
    import openviking.service.mineru_preflight as pf

    class _Resp:
        status_code = 200

        def json(self):
            return {"protocol_version": "1.0", "status": "healthy"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            return _Resp()

    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kw: _Client())
    asyncio.run(pf.wait_for_mineru_ready("http://127.0.0.1:8000", timeout=1))


def test_save_images_falls_back_to_the_image_stem_without_a_resource_name():
    """resource_name=None must not hand None to the storage layer."""
    parser = _parser(mineru_endpoint="https://x")

    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            # a real StoragePath rejects None here
            assert resource_name, "resource_name must not be None"
            return Path("/media") / resource_name / (filename + extension)

    repl = asyncio.run(parser._mineru_save_images({"images/figure.png": b"png"}, _Storage(), None))
    assert repl == {"images/figure.png": "figure/figure.png"}


def test_image_refs_match_a_bare_key_against_a_prefixed_reference():
    """A bare key with an ``images/`` reference must not become images/doc/images/."""
    out = PDFParser._mineru_apply_image_refs(
        "see ![f](images/figure.png)", {"figure.png": "doc/figure.png"}
    )
    assert out == "see ![f](doc/figure.png)"
    assert "images/doc" not in out


def test_image_refs_match_a_prefixed_key_against_a_bare_reference():
    out = PDFParser._mineru_apply_image_refs(
        "see ![f](figure.png)", {"images/figure.png": "doc/figure.png"}
    )
    assert out == "see ![f](doc/figure.png)"


def test_image_refs_leave_similar_names_alone():
    out = PDFParser._mineru_apply_image_refs(
        "myfigure.png figure.pngx figure.png", {"figure.png": "doc/figure.png"}
    )
    assert out.startswith("myfigure.png figure.pngx ")
    assert out.endswith("doc/figure.png")


def test_selfhosted_failure_status_raises_even_with_partial_markdown(tmp_path):
    """status="failed" plus partial md_content must not be returned as success."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    parser = _parser(strategy="mineru", mineru_endpoint="https://mineru.example")

    payload = {
        "results": {
            "doc.pdf": {
                "status": "failed",
                "md_content": "half a doc",
            }
        }
    }

    class _Client:
        async def post(self, *a, **kw):
            return _FakeResponse(payload)

    try:
        asyncio.run(parser._mineru_selfhosted(_Client(), {}, pdf, None, "doc"))
    except ValueError as exc:
        assert "failed to convert" in str(exc)
    else:
        raise AssertionError("expected ValueError for a failed entry")


def test_selfhosted_error_field_raises_even_when_markdown_is_present(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    parser = _parser(strategy="mineru", mineru_endpoint="https://mineru.example")

    payload = {"results": {"doc.pdf": {"md_content": "half a doc", "err_msg": "corrupt page tree"}}}

    class _Client:
        async def post(self, *a, **kw):
            return _FakeResponse(payload)

    try:
        asyncio.run(parser._mineru_selfhosted(_Client(), {}, pdf, None, "doc"))
    except ValueError as exc:
        assert "corrupt page tree" in str(exc)
    else:
        raise AssertionError("expected ValueError when err_msg is set")


def test_selfhosted_success_status_still_returns_markdown(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    parser = _parser(strategy="mineru", mineru_endpoint="https://mineru.example")

    payload = {"results": {"doc.pdf": {"status": "done", "md_content": "# ok"}}}

    class _Client:
        async def post(self, *a, **kw):
            return _FakeResponse(payload)

    text, _meta = asyncio.run(parser._mineru_selfhosted(_Client(), {}, pdf, None, "doc"))
    assert text == "# ok"


def test_parser_rejects_a_strategy_it_cannot_dispatch(monkeypatch, pdf_path):
    """The parser's own dispatch guard, past config validation."""
    monkeypatch.setattr(PDFConfig, "validate", lambda self: None)
    parser = _parser(strategy="nonsense")
    try:
        asyncio.run(parser._convert_to_markdown(pdf_path))
    except ValueError as exc:
        assert "Unknown strategy" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_failure_message_falls_back_when_no_reason_is_reported(tmp_path):
    """A failed entry with nothing to say still has to name the document."""
    entry = {"status": "failed"}
    msg = PDFParser._mineru_failure_message(entry, tmp_path / "report.pdf")
    assert "report.pdf" in msg
    assert "status=failed" in msg


def test_entry_failure_detects_the_documented_states():
    for state in ("failed", "FAILED", " error ", "aborted", "cancelled", "timeout"):
        assert PDFParser._mineru_entry_failed({"status": state}), state
    assert PDFParser._mineru_entry_failed({"state": "rejected"})
    assert PDFParser._mineru_entry_failed({"err_no": "E42"})
    # a completed or silent entry is not a failure
    for ok in ({"status": "done"}, {"status": "success"}, {}, {"md_content": "x"}):
        assert not PDFParser._mineru_entry_failed(ok), ok


def test_local_image_rasteriser_returns_png_bytes():
    """The local path renders an image box to real PNG bytes."""
    parser = _parser(strategy="local")
    saved = {}

    class _Img:
        def save(self, buffer, format):
            saved["format"] = format
            buffer.write(b"\x89PNG")

    class _Crop:
        def to_image(self, resolution=None):
            saved["resolution"] = resolution
            return _Img()

    class _Page:
        width = 100
        height = 200

        def crop(self, bbox):
            saved["bbox"] = bbox
            return _Crop()

    out = parser._extract_image_from_page(_Page(), {"x0": 5, "top": 10, "x1": 50, "bottom": 60})
    assert out == b"\x89PNG"
    assert saved["format"] == "PNG"
    assert saved["bbox"] == (5, 10, 50, 60)


def test_local_image_rasteriser_swallows_a_render_failure():
    """A broken image box costs that image, not the document."""
    parser = _parser(strategy="local")

    class _Page:
        width = 100
        height = 200

        def crop(self, bbox):
            raise RuntimeError("cannot rasterise")

    assert (
        parser._extract_image_from_page(_Page(), {"x0": 0, "top": 0, "x1": 9, "bottom": 9}) is None
    )


def test_local_image_rasteriser_skips_a_degenerate_box():
    parser = _parser(strategy="local")

    class _Page:
        width = 100
        height = 200

        def crop(self, bbox):
            raise AssertionError("a zero-area box must not be cropped")

    assert (
        parser._extract_image_from_page(_Page(), {"x0": 10, "top": 10, "x1": 10, "bottom": 40})
        is None
    )


def test_selfhosted_preflight_rejects_a_response_that_is_not_json(monkeypatch):
    """A 200 that is not the health contract must still fail the preflight."""
    import openviking.service.mineru_preflight as pf

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            return _Resp()

    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kw: _Client())
    try:
        asyncio.run(pf.wait_for_mineru_ready("http://127.0.0.1:8000", timeout=0.2))
    except RuntimeError as exc:
        assert "preflight failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_zip_image_save_failure_is_logged_and_skipped(pdf_path):
    """One unwritable image must not lose the others or the document."""
    archive = _zip_bytes(
        {
            "full.md": b"![a](images/a.png) ![b](images/b.png)",
            "images/a.png": b"one",
            "images/b.png": b"two",
        }
    )

    class _Storage:
        media_dir = Path("/media")

        def save_image(self, resource_name, blob, filename, extension):
            if filename == "a":
                raise OSError("no space left")
            return Path("/media") / resource_name / (filename + extension)

    parser = _hosted_parser()
    text, meta = parser._mineru_unpack_zip(archive, pdf_path, {}, _Storage(), "doc")
    assert meta["images_saved"] == 1
    assert "doc/b.png" in text
    assert "images/a.png" in text  # the one that failed is left as-is, not mis-linked


def test_failure_message_without_any_reason_just_names_the_file(tmp_path):
    """A failed entry that reports nothing usable still names the document."""
    msg = PDFParser._mineru_failure_message({"failed": True}, tmp_path / "x.pdf")
    assert msg == "MinerU failed to convert x.pdf"


def test_image_refs_skip_a_key_without_a_file_name():
    """A key that is only a directory has nothing to match on."""
    out = PDFParser._mineru_apply_image_refs("text images/ stays", {"images/": "doc/"})
    assert out == "text images/ stays"


# ---------------------------------------------------------------------------
# startup preflight wiring
# ---------------------------------------------------------------------------


def test_preflight_settings_treat_a_token_as_the_hosted_api():
    from openviking.service.core import mineru_preflight_settings

    cfg = PDFConfig(
        strategy="mineru",
        mineru_endpoint="https://mineru.example/api/v4",
        mineru_token="hosted-token",
    )
    endpoint, hosted, token = mineru_preflight_settings(cfg)
    assert (endpoint, hosted, token) == ("https://mineru.example/api/v4", True, "hosted-token")


def test_preflight_settings_keep_the_selfhosted_contract_without_a_token():
    from openviking.service.core import mineru_preflight_settings

    cfg = PDFConfig(strategy="mineru", mineru_endpoint="http://127.0.0.1:8000")
    endpoint, hosted, token = mineru_preflight_settings(cfg)
    assert (endpoint, hosted, token) == ("http://127.0.0.1:8000", False, None)


def test_selfhosted_preflight_rejects_a_json_body_that_is_not_an_object(monkeypatch):
    """HTTP 200 with a non-object body is not the health contract."""
    import openviking.service.mineru_preflight as pf

    class _Resp:
        status_code = 200

        def json(self):
            return ["not", "an", "object"]

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            return _Resp()

    monkeypatch.setattr(pf.httpx, "AsyncClient", lambda **kw: _Client())
    try:
        asyncio.run(pf.wait_for_mineru_ready("http://127.0.0.1:8000", timeout=0.2))
    except RuntimeError as exc:
        assert "unexpected response" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


# ---------------------------------------------------------------------------
# regressions: image directory collisions and test isolation
# ---------------------------------------------------------------------------


class _RecordingStorage:
    """Storage that remembers every (resource_name, filename) it was asked to save."""

    def __init__(self):
        self.media_dir = Path("/media")
        self.saved = {}

    def save_image(self, resource_name, blob, filename, extension):
        target = Path("/media") / resource_name / (filename + extension)
        # a later document writing the same path overwrites the earlier bytes
        self.saved[target.as_posix()] = blob
        return target


def _b64(payload: bytes) -> str:
    import base64 as _b

    return "data:image/png;base64," + _b.b64encode(payload).decode("ascii")


def _selfhosted_client(markdown, image_bytes):
    """A /file_parse reply carrying one inline image."""
    import base64 as _b

    encoded = "data:image/png;base64," + _b.b64encode(image_bytes).decode("ascii")

    class _Client:
        async def post(self, *a, **kw):
            return _FakeResponse(
                {
                    "results": {
                        "doc.pdf": {
                            "md_content": markdown,
                            "images": {"images/figure.png": encoded},
                        }
                    }
                }
            )

    return _Client()


def test_two_pdfs_with_the_same_image_name_do_not_overwrite(tmp_path):
    """Without a resource_name each PDF's images must land in its own directory.

    Both documents carry an images/figure.png. Falling back to the image name would
    send both to figure/ and the second would overwrite the first.
    """
    storage = _RecordingStorage()
    parser = _parser(strategy="mineru", mineru_endpoint="https://mineru.example")

    results = {}
    for name, payload in (("alpha", b"alpha-bytes"), ("beta", b"beta-bytes")):
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        text, _meta = asyncio.run(
            parser._mineru_selfhosted(
                _selfhosted_client("![f](images/figure.png)", payload),
                {},
                pdf,
                storage,
                None,  # no resource_name: the parser must choose one
            )
        )
        results[name] = text

    # each markdown points into its own document directory
    assert "alpha/figure.png" in results["alpha"]
    assert "beta/figure.png" in results["beta"]
    assert "images/figure.png" not in results["alpha"]

    # and neither document clobbered the other's bytes
    assert storage.saved["/media/alpha/figure.png"] == b"alpha-bytes"
    assert storage.saved["/media/beta/figure.png"] == b"beta-bytes"


def test_an_explicit_resource_name_still_wins(tmp_path):
    storage = _RecordingStorage()
    parser = _parser(strategy="mineru", mineru_endpoint="https://mineru.example")
    pdf = tmp_path / "gamma.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    text, _meta = asyncio.run(
        parser._mineru_selfhosted(
            _selfhosted_client("![f](images/figure.png)", b"bytes"),
            {},
            pdf,
            storage,
            "chosen",
        )
    )
    assert "chosen/figure.png" in text
    assert list(storage.saved) == ["/media/chosen/figure.png"]


def test_preflight_tests_do_not_leak_a_fake_http_client():
    """The preflight tests must restore the real httpx.AsyncClient.

    ``pf.httpx`` is the shared httpx module, so assigning to it replaces the client
    for every later test in the process.
    """
    import openviking.service.mineru_preflight as pf

    assert getattr(pf.httpx.AsyncClient, "__module__", "") == "httpx", (
        "httpx.AsyncClient is still patched after the preflight tests ran"
    )


def test_preflight_tests_do_not_leak_a_fake_client_object():
    """A second guard: the attribute must be httpx's own, not a lambda from a test."""
    import httpx

    import openviking.service.mineru_preflight as pf

    assert pf.httpx.AsyncClient is httpx.AsyncClient
