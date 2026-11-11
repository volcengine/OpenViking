"""Wiki binary targets and Slides must use the ordinary file parser pipeline."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import httpx
import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.accessors.feishu_accessor import FeishuAccessor, FeishuSourcePreflight
from openviking.parse.feishu_import import FeishuImportPlan
from openviking.parse.parser_router import ParserRouter
from openviking.parse.understanding_api import UnderstandingAPI
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError


def office_bytes(marker="ppt/presentation.xml"):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(marker, "<document/>")
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "slides"])
@pytest.mark.parametrize("wiki", [False, True])
async def test_binary_source_is_materialized_before_parsing(monkeypatch, kind, wiki):
    accessor = FeishuAccessor()
    source = f"https://example.feishu.cn/{'wiki/node' if wiki else kind + '/target'}"
    monkeypatch.setattr(
        accessor, "_resolve_wiki_node", Mock(return_value=(kind, "target", "Title"))
    )
    content = office_bytes() if kind == "slides" else b"%PDF-1.7 fixture"
    extension = ".pptx" if kind == "slides" else ".pdf"
    download = Mock(return_value=(content, None, "Title" + extension))
    export = AsyncMock(return_value=(content, None, "Title.pptx"))
    monkeypatch.setattr(accessor, "_download_drive_file", download)
    monkeypatch.setattr(accessor, "_export_slides", export, raising=False)
    resource = await accessor.access(source, feishu_access_token="test-user")
    try:
        assert resource.path.suffix == extension
        assert resource.path.read_bytes() == content
        assert resource.original_source == source
        assert resource.meta["feishu_content_kind"] == "file"
        assert resource.meta["feishu_doc_type"] == kind
        assert resource.meta["feishu_token"] == "target"
        assert (export.await_count if kind == "slides" else download.call_count) == 1
    finally:
        resource.cleanup()


@pytest.mark.parametrize(
    "path,options",
    [
        ("slides/slides-token", {}),
        ("wiki/node", {}),
        ("wiki/node", {"_feishu_resolved_type": "file"}),
        ("wiki/node", {"_feishu_resolved_type": "slides"}),
    ],
)
def test_binary_and_unresolved_wiki_cannot_bypass_accessor(path, options):
    assert not UnderstandingAPI.can_submit_url_directly(
        f"https://example.feishu.cn/{path}", lark_file={"user_access_token": "test"}, **options
    )


@pytest.mark.parametrize(
    "marker,extension",
    [
        ("ppt/presentation.xml", ".pptx"),
        ("word/document.xml", ".docx"),
        ("xl/workbook.xml", ".xlsx"),
    ],
)
def test_extensionless_office_download_keeps_its_format(marker, extension):
    assert (
        FeishuAccessor._drive_file_name(
            "token", office_bytes(marker), "application/octet-stream", filename_hint="Report"
        )
        == "Report" + extension
    )


@pytest.mark.parametrize(
    "mime,extension",
    [
        ("application/msword", ".doc"),
        ("application/vnd.ms-excel", ".xls"),
        ("application/vnd.ms-powerpoint", ".ppt"),
        ("application/octet-stream", ".bin"),
    ],
)
def test_extensionless_legacy_office_is_not_always_word(mime, extension):
    assert (
        FeishuAccessor._drive_file_name(
            "token", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", mime, filename_hint="Report"
        )
        == "Report" + extension
    )


@pytest.mark.asyncio
async def test_recursive_slides_are_files_even_with_understanding(monkeypatch, tmp_path):
    accessor = FeishuAccessor()
    monkeypatch.setattr(
        accessor,
        "_export_slides",
        AsyncMock(return_value=(office_bytes(), None, "Deck.pptx")),
        raising=False,
    )
    plan = FeishuImportPlan(tmp_path, use_understanding=True)
    path = await accessor._write_import_content(
        "slides", "token", "Deck", "https://example.feishu.cn/slides/token", tmp_path, plan
    )
    assert path.suffix == ".pptx"
    assert path.is_file()
    assert plan.entries[0].kind == "file"
    assert plan.downloaded_bytes == path.stat().st_size


def sdk_response(**data):
    return SimpleNamespace(success=lambda: True, data=SimpleNamespace(**data))


@pytest.mark.asyncio
async def test_slides_export_waits_for_success_and_downloads_pptx(monkeypatch):
    accessor = FeishuAccessor()
    client = Mock()
    monkeypatch.setattr(accessor, "_get_client", Mock(return_value=client))
    content = office_bytes()
    responses = [sdk_response(ticket="ticket")]
    responses += [sdk_response(result=SimpleNamespace(job_status=status)) for status in (1, 2)]
    responses.append(
        sdk_response(result=SimpleNamespace(job_status=0, file_token="exported", file_name="Deck"))
    )
    download = AsyncMock(
        return_value=SimpleNamespace(success=lambda: True, raw=SimpleNamespace(content=content))
    )
    monkeypatch.setattr(accessor, "_download_export_file", download)
    call = Mock(side_effect=responses)
    monkeypatch.setattr(accessor, "_call_api", call)
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.parse.accessors.feishu_accessor.asyncio.sleep", sleep)
    actual, _, name = await accessor._download_binary_source(
        "slides", "source", feishu_access_token="test-user", filename_hint=None, max_bytes=512
    )
    assert actual == content
    assert name == "Deck.pptx"
    assert sleep.await_count == 2
    requests = [item.args[1] for item in call.call_args_list]
    assert requests[0].request_body.type == "slides"
    assert requests[0].request_body.token == "source"
    assert requests[0].request_body.file_extension == "pptx"
    assert all(
        request.token == "source" and request.ticket == "ticket" for request in requests[1:4]
    )
    assert download.call_args.args[1].file_token == "exported"
    assert download.call_args.args[2] == "test-user"
    assert download.call_args.kwargs["max_bytes"] == 512
    assert all(item.args[2] == "test-user" for item in call.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (3, "INTERNAL"),
        (107, "INVALID_ARGUMENT"),
        (108, "DEADLINE_EXCEEDED"),
        (109, "PERMISSION_DENIED"),
        (110, "PERMISSION_DENIED"),
        (111, "NOT_FOUND"),
    ],
)
async def test_failed_export_is_not_downloaded(monkeypatch, status, code):
    accessor = FeishuAccessor()
    monkeypatch.setattr(accessor, "_get_client", Mock(return_value=Mock()))
    call = Mock(
        side_effect=[
            sdk_response(ticket="ticket"),
            sdk_response(result=SimpleNamespace(job_status=status)),
        ]
    )
    monkeypatch.setattr(accessor, "_call_api", call)
    with pytest.raises(OpenVikingError) as caught:
        await accessor._download_binary_source(
            "slides", "source", feishu_access_token="test-user", filename_hint=None
        )
    assert caught.value.code == code
    assert caught.value.details["ticket"] == "ticket"
    assert call.call_count == 2


@pytest.mark.asyncio
async def test_export_timeout_has_distinct_error(monkeypatch):
    accessor = FeishuAccessor()
    monkeypatch.setattr(accessor, "_export_slides", AsyncMock(side_effect=asyncio.TimeoutError()))
    with pytest.raises(OpenVikingError) as caught:
        await accessor._download_binary_source(
            "slides", "source", feishu_access_token=None, filename_hint=None
        )
    assert caught.value.code == "DEADLINE_EXCEEDED"


@pytest.mark.asyncio
async def test_download_budget_checked_before_writing(monkeypatch):
    accessor = FeishuAccessor()
    monkeypatch.setattr(
        accessor,
        "_download_drive_file",
        Mock(side_effect=InvalidArgumentError("Feishu import download byte limit exceeded")),
    )
    write = Mock()
    monkeypatch.setattr(accessor, "_write_temp_drive_file", write)
    with pytest.raises(InvalidArgumentError, match="byte limit"):
        await accessor.access("https://example.feishu.cn/file/token", feishu_max_download_bytes=100)
    write.assert_not_called()


@pytest.mark.asyncio
async def test_export_permission_failure_is_not_retried(monkeypatch):
    accessor = FeishuAccessor()
    monkeypatch.setattr(accessor, "_get_client", Mock(return_value=Mock()))
    call = Mock(
        return_value=SimpleNamespace(
            success=lambda: False,
            code=1069902,
            msg="no export permission",
            raw=SimpleNamespace(status_code=403),
        )
    )
    monkeypatch.setattr(accessor, "_call_api", call)
    with pytest.raises(OpenVikingError) as caught:
        await accessor._download_binary_source(
            "slides", "source", feishu_access_token="test-user", filename_hint=None
        )
    assert caught.value.code == "PERMISSION_DENIED"
    assert call.call_count == 1


@pytest.mark.asyncio
async def test_cancelling_folder_export_cleans_temporary_directory(monkeypatch, tmp_path):
    accessor = FeishuAccessor()
    work = tmp_path / "download"
    work.mkdir()
    monkeypatch.setattr(
        "openviking.parse.accessors.feishu_accessor.tempfile.mkdtemp", lambda **kwargs: str(work)
    )
    monkeypatch.setattr(accessor, "_drive_folder_display_name", lambda *a, **k: "Folder")
    monkeypatch.setattr(
        accessor,
        "_list_drive_folder_children",
        lambda *a, **k: [SimpleNamespace(type="slides", token="slide", name="Deck")],
    )
    started = asyncio.Event()

    async def export(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(accessor, "_export_slides", export)
    task = asyncio.create_task(accessor.access("https://example.feishu.cn/drive/folder/root"))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not work.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extension,content,supported", [(".bin", b"\x00\xff", False), (".txt", b"hello", True)]
)
async def test_unknown_drive_binary_is_not_parsed_as_text(tmp_path, extension, content, supported):
    path = tmp_path / ("download" + extension)
    path.write_bytes(content)
    registry = SimpleNamespace(get_parser_for_file=Mock(return_value=None), parse=AsyncMock())
    resource = LocalResource(
        path,
        SourceType.FEISHU,
        "https://example.feishu.cn/file/token",
        meta={"feishu_content_kind": "file"},
    )
    router = ParserRouter(registry)
    if supported:
        await router.parse(resource, parser_backend="internal")
        registry.parse.assert_awaited_once()
    else:
        with pytest.raises(InvalidArgumentError, match="Unsupported Drive file format"):
            await router.parse(resource, parser_backend="internal")
        registry.parse.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,extension", [("file", ".pdf"), ("slides", ".pptx")])
@pytest.mark.parametrize("backend", [None, "internal", "understanding"])
async def test_synchronous_wiki_routes_resolved_binary_to_selected_backend(
    monkeypatch, tmp_path, kind, extension, backend
):
    source = "https://example.feishu.cn/wiki/node"
    path = tmp_path / ("Report" + extension)
    path.write_bytes(b"%PDF-1.7" if extension == ".pdf" else office_bytes())
    preflight = AsyncMock(return_value=FeishuSourcePreflight(kind, "obj", "Report", "file"))
    monkeypatch.setattr(FeishuAccessor, "preflight_source", preflight)
    resource = LocalResource(
        path, SourceType.FEISHU, source, meta={"feishu_content_kind": "file"}, is_temporary=False
    )
    api = SimpleNamespace(parse=AsyncMock(return_value=object()))
    registry = SimpleNamespace(
        parse=AsyncMock(return_value=object()), get_parser_for_file=Mock(return_value=object())
    )
    router = ParserRouter(registry)
    router._understanding_api = api
    monkeypatch.setattr(router, "should_use_understanding_api", Mock(return_value=False))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    processor._accessor_registry = SimpleNamespace(access=AsyncMock(return_value=resource))
    await processor.process(source, parser_backend=backend, feishu_access_token="test-user")
    if backend == "understanding":
        preflight.assert_awaited_once()
    else:
        preflight.assert_not_awaited()
    processor._accessor_registry.access.assert_awaited_once()
    if backend == "understanding":
        api.parse.assert_awaited_once()
        assert api.parse.await_args.args[0] == str(path)
        assert "feishu_access_token" not in api.parse.await_args.kwargs
        registry.parse.assert_not_awaited()
    else:
        registry.parse.assert_awaited_once()
        api.parse.assert_not_awaited()


@pytest.mark.parametrize("title", ["产品方案 v1.2", "2026.11.06", "Report"])
@pytest.mark.parametrize(
    "content,mime,extension",
    [
        (b"%PDF-1.7 fixture", "application/pdf", ".pdf"),
        (office_bytes(), "application/octet-stream", ".pptx"),
    ],
)
def test_dotted_title_does_not_override_detected_format(title, content, mime, extension):
    assert (
        FeishuAccessor._drive_file_name("token", content, mime, filename_hint=title)
        == title + extension
    )


@pytest.mark.parametrize("name", ["Deck.pptx", "Document.DOCX", "Notes.txt", "part.step"])
def test_explicit_file_extension_is_preserved(name):
    assert (
        FeishuAccessor._drive_file_name(
            "token", b"opaque", "application/octet-stream", filename_hint=name
        )
        == name
    )


@pytest.mark.parametrize("name", ["report.md", "main.go", "view.tsx", "data.jsonl"])
def test_text_extension_survives_missing_system_mime_mapping(monkeypatch, name):
    monkeypatch.setattr(
        "openviking.parse.accessors.feishu_accessor.mimetypes.guess_type",
        lambda *args: (None, None),
    )
    assert FeishuAccessor._drive_file_name(
        "token", b"# Content", "text/plain", filename_hint=name
    ) == name


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "slides"])
@pytest.mark.parametrize("backend", [None, "understanding"])
async def test_queued_wiki_reuses_worker_resolution_and_keeps_binary_format(
    monkeypatch, kind, backend
):
    from openviking.parse.mode import ParseMode
    from openviking.server.identity import RequestContext, Role
    from openviking.service.resource_service import ResourceService
    from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
    from openviking_cli.session.user_id import UserIdentifier
    from openviking_cli.utils.config.parser_config import FeishuConfig

    title = "产品方案 v1.2"
    source = "https://example.feishu.cn/wiki/node"
    config = SimpleNamespace(
        parser_api=SimpleNamespace(enable=True, enable_feishu_url=True, extensions=["pdf", "pptx"])
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config", lambda: config
    )
    monkeypatch.setattr(
        "openviking.config.feishu.get_effective_feishu_config",
        AsyncMock(return_value=FeishuConfig()),
    )
    resolve = Mock(side_effect=[(kind, "queued-obj", title)] + [(kind, "worker-obj", title)] * 2)
    monkeypatch.setattr(FeishuAccessor, "_resolve_wiki_node", resolve)
    monkeypatch.setattr(FeishuAccessor, "_probe_document_permission", lambda *args, **kwargs: None)
    download = Mock(return_value=(b"%PDF-1.7 fixture", "application/pdf", title))
    export = AsyncMock(return_value=(office_bytes(), None, title + ".pptx"))
    monkeypatch.setattr(FeishuAccessor, "_download_drive_file", download)
    monkeypatch.setattr(FeishuAccessor, "_export_slides", export)
    api = SimpleNamespace(parse=AsyncMock(return_value=object()))
    registry = SimpleNamespace(parse=AsyncMock(), get_parser_for_file=Mock(return_value=object()))
    router = ParserRouter(registry)
    router._understanding_api = api
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    processor._accessor_registry = SimpleNamespace(access=FeishuAccessor().access)
    service = ResourceService(
        viking_fs=SimpleNamespace(),
        skill_processor=SimpleNamespace(),
        resource_processor=processor,
        runtime_config_manager=SimpleNamespace(),
    )
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    plan = await service._prepare_standard_source_plan(
        path=source,
        ctx=ctx,
        mode=ParseMode.DEFAULT,
        allow_local_path_resolution=False,
        processor_kwargs={"feishu_access_token": "test-user", "parser_backend": backend},
        watch_auth_state=None,
    )
    assert plan.source_identity.source_name == title
    assert plan.processor_args["_feishu_prepared_source"]
    assert resolve.call_count == 1

    async def ingest(*, path, **options):
        await processor.process(path, **options)
        return {"status": "success"}

    monkeypatch.setattr(service, "_execute_resource_ingestion", ingest)
    tracker = SimpleNamespace(get=AsyncMock(return_value=None), patch_task=AsyncMock())
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    msg = AddResourceMsg(
        task_id="task",
        path=source,
        root_uri="viking://resources/Report",
        account_id="account",
        user_id="user",
        role="user",
        source_name=plan.source_identity.source_name,
        args=plan.processor_args,
    )
    await service.execute_add_resource_job(
        msg, ctx=ctx, resource_lock=None, stage_callback=AsyncMock(), task_auth=plan.task_auth
    )
    assert resolve.call_count == 2  # One at enqueue, one fresh lookup in the worker.
    binary_call = export.await_args if kind == "slides" else download.call_args
    assert binary_call.args[0] == "worker-obj"
    assert binary_call.kwargs["filename_hint"] == title
    assert binary_call.kwargs["feishu_access_token"] == "test-user"
    api.parse.assert_awaited_once()
    registry.parse.assert_not_awaited()
    extension = ".pptx" if kind == "slides" else ".pdf"
    assert api.parse.await_args.args[0].endswith(extension)
    assert api.parse.await_args.kwargs["resolved_extension"] == extension
    assert api.parse.await_args.kwargs["source_name"] == title
    assert "_feishu_resolved_wiki" not in api.parse.await_args.kwargs
    assert "_feishu_resolved_wiki" not in msg.args


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["user_access_token", "tenant_access_token"])
async def test_wiki_preflight_uses_lark_file_identity_without_app_credentials(
    monkeypatch, identity
):
    import json

    import lark_oapi as lark
    from lark_oapi.core.http import Transport
    from lark_oapi.core.model import RawResponse

    from openviking_cli.utils.config.parser_config import FeishuConfig

    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    calls = []

    def execute(config, request, option):
        calls.append(request.uri)
        assert getattr(option, identity) == "explicit-test-token"
        assert not getattr(
            option,
            "tenant_access_token" if identity == "user_access_token" else "user_access_token",
        )
        assert request.token_types == {
            lark.AccessTokenType.USER
            if identity == "user_access_token"
            else lark.AccessTokenType.TENANT
        }
        raw = RawResponse()
        raw.status_code = 200
        raw.headers = {"Content-Type": "application/json"}
        raw.content = json.dumps(
            {
                "code": 0,
                "data": {
                    "node": {"obj_type": "docx", "obj_token": "doc", "title": "Title"},
                    "document": {"title": "Title"},
                },
            }
        ).encode()
        return raw

    monkeypatch.setattr(Transport, "execute", execute)
    result = await FeishuAccessor().preflight_source(
        "https://example.feishu.cn/wiki/node",
        feishu_config=FeishuConfig(app_id="", app_secret=""),
        feishu_access_token="ignored-when-lark-file-is-present",
        lark_file={identity: " explicit-test-token "},
    )
    assert result.doc_type == "docx"
    assert len(calls) == 2  # Wiki identity plus document permission, both using explicit auth.


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["user_access_token", "tenant_access_token"])
async def test_synchronous_wiki_forwards_lark_file_to_preflight_and_parser(monkeypatch, identity):
    preflight = AsyncMock(return_value=FeishuSourcePreflight("docx", "obj", "Report", "file"))
    monkeypatch.setattr(FeishuAccessor, "preflight_source", preflight)
    api = SimpleNamespace(parse=AsyncMock(return_value=object()))
    router = ParserRouter(SimpleNamespace())
    router._understanding_api = api
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    auth = {identity: "test-token"}
    await processor.process(
        "https://example.feishu.cn/wiki/node", parser_backend="understanding", lark_file=auth
    )
    assert preflight.await_args.kwargs["lark_file"] == auth
    assert api.parse.await_args.kwargs["lark_file"] == auth


class StreamingResponse:
    def __init__(self, chunks, *, length=None, status=200):
        self.status_code = status
        self.headers = {"Content-Length": length} if length is not None else {}
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        assert chunk_size == 64 * 1024
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


class ExportStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, pause=False):
        self.chunks = chunks
        self.pause = pause
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.read_count = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk
            if self.pause:
                self.started.set()
                await self.release.wait()

    async def aclose(self):
        self.closed = True


def export_http_client(monkeypatch, stream, *, length=None, status=200):
    from lark_oapi.api.drive.v1 import DownloadExportTaskRequest

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-user"
        assert request.url.path == "/open-apis/drive/v1/export_tasks/file/exported/download"
        headers = {"Content-Length": length} if length is not None else {}
        return httpx.Response(status, headers=headers, stream=stream)

    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs)
    )
    accessor = FeishuAccessor()._new_operation("https://example.feishu.cn/slides/source")
    client = accessor._get_client(use_user_token=True)
    request = DownloadExportTaskRequest.builder().file_token("exported").build()
    return accessor, client, request


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["timeout", "cancel"])
async def test_export_deadline_or_cancellation_closes_download(monkeypatch, termination):
    stream = ExportStream([b"a" * 65536, b"must-not-read"], pause=True)
    accessor, _, _ = export_http_client(monkeypatch, stream)
    monkeypatch.setattr(
        accessor,
        "_call_api",
        Mock(side_effect=[
            sdk_response(ticket="ticket"),
            sdk_response(result=SimpleNamespace(job_status=0, file_token="exported")),
        ]),
    )
    wait_for = asyncio.wait_for
    if termination == "timeout":
        async def expire_during_download(coro, timeout):
            assert timeout == 120
            task = asyncio.create_task(coro)
            await wait_for(stream.started.wait(), timeout=2)
            return await wait_for(task, timeout=0.01)

        monkeypatch.setattr(asyncio, "wait_for", expire_during_download)

    task = asyncio.create_task(accessor._download_binary_source(
        "slides", "source", feishu_access_token="test-user", filename_hint=None
    ))
    if termination == "cancel":
        await wait_for(stream.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(OpenVikingError) as error:
            await task
        assert error.value.code == "DEADLINE_EXCEEDED"
    assert stream.closed
    stream.release.set()
    await asyncio.sleep(0)
    assert stream.read_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("length,reads", [("65537", 0), (None, 2), ("1", 2)])
async def test_export_stream_keeps_download_limit(monkeypatch, length, reads):
    stream = ExportStream([b"a" * 65536, b"b" * 65536, b"must-not-read"])
    accessor, client, request = export_http_client(monkeypatch, stream, length=length)
    with pytest.raises(InvalidArgumentError, match="byte limit"):
        await accessor._download_export_file(client, request, "test-user", max_bytes=65536)
    assert stream.closed
    assert stream.read_count == reads


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content,limit", [(200, b"exact", 5), (403, b"permission denied", 1)])
async def test_export_stream_preserves_success_and_http_error(monkeypatch, status, content, limit):
    stream = ExportStream([content])
    accessor, client, request = export_http_client(
        monkeypatch, stream, length=str(len(content)), status=status
    )
    response = await accessor._download_export_file(client, request, "test-user", max_bytes=limit)
    assert response.success() == (status == 200)
    assert response.raw.status_code == status
    assert response.raw.content == content
    assert stream.closed


def stream_request():
    import lark_oapi as lark
    from lark_oapi.core.model import RequestOption

    req = (
        lark.BaseRequest.builder()
        .http_method(lark.HttpMethod.GET)
        .uri("/download")
        .token_types({lark.AccessTokenType.USER})
        .build()
    )
    return (
        SimpleNamespace(domain="https://example.feishu.cn", timeout=30),
        req,
        RequestOption.builder().user_access_token("test-user").build(),
    )


@pytest.mark.parametrize("length,expected_reads", [("11", 0), (None, 2), ("1", 2)])
def test_stream_aborts_oversize_response_and_closes_connection(monkeypatch, length, expected_reads):
    import requests

    response = StreamingResponse([b"a" * 6, b"b" * 6, b"must-not-read"], length=length)
    get = Mock(return_value=response)
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(InvalidArgumentError, match="byte limit"):
        FeishuAccessor._stream_download(*stream_request(), max_bytes=10)
    assert response.read_count == expected_reads
    assert response.closed
    assert get.call_args.kwargs["stream"] is True
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer test-user"


def test_stream_allows_exact_budget(monkeypatch):
    import requests

    response = StreamingResponse([b"abc", b"defg"], length="7")
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    result = FeishuAccessor._stream_download(*stream_request(), max_bytes=7)
    assert result.content == b"abcdefg"
    assert response.closed


def test_stream_preserves_http_error_with_a_small_download_budget(monkeypatch):
    import requests

    response = StreamingResponse([b"permission denied"], length="17", status=403)
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    result = FeishuAccessor._stream_download(*stream_request(), max_bytes=1)
    assert result.status_code == 403
    assert result.content == b"permission denied"
    assert response.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "slides"])
async def test_collection_passes_remaining_budget_and_stops_after_overflow(
    monkeypatch, tmp_path, kind
):
    from openviking.parse.accessors.feishu_accessor import _FeishuDownloadLimitError

    accessor = FeishuAccessor()
    download = AsyncMock(side_effect=_FeishuDownloadLimitError())
    monkeypatch.setattr(accessor, "_download_binary_source", download)
    plan = FeishuImportPlan(
        tmp_path,
        max_bytes=100,
        downloaded_bytes=80,
        source_url="https://example.feishu.cn/wiki/root",
    )
    with pytest.raises(InvalidArgumentError, match="byte limit"):
        await accessor._write_import_content(kind, "token", "Deck", "", tmp_path, plan)
    assert download.await_args.kwargs["max_bytes"] == 20
    assert plan.download_exhausted
    with pytest.raises(ValueError, match="byte limit"):
        await accessor._write_import_content(kind, "next", "Next", "", tmp_path, plan)
    assert download.await_count == 1
    assert not list(tmp_path.iterdir())
