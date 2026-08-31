import asyncio
import concurrent.futures
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.understanding_api import (
    PREPARED_FILE_ID_ARG,
    PREPARED_RESPONSE_ID_ARG,
    UnderstandingAPI,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_tracker import TaskStatus
from openviking.service.task_work_index import TASK_WORK_ID_FIELD
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg, AddResourcePhase
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.telemetry import get_current_telemetry, resolve_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_add_resource_processor_cancelled_context_preserves_group_ids(monkeypatch):
    lock = {"lease_ref": "lock-1"}
    cleanup = AsyncMock(return_value=True)
    service = SimpleNamespace(_cleanup_reserved_target_if_empty=cleanup)
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_adopt=AsyncMock(return_value=lock),
            pathlock_release=AsyncMock(),
        ),
        delete_temp=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        asyncio.get_running_loop(),
        QueueManager.ADD_RESOURCE,
        viking_fs,
    )
    msg = AddResourceMsg(
        task_id="task-cancelled",
        path="report.zip",
        root_uri="viking://resources/report",
        account_id="account-1",
        user_id="user-1",
        group_ids=["writers", "reviewers"],
        role="user",
        lock_handoff={"handle_id": "lock-1"},
        cleanup_empty_target_on_failure=True,
    )

    def run_on_current_loop(coro, _loop):
        task = asyncio.create_task(coro)
        future: concurrent.futures.Future[None] = concurrent.futures.Future()

        def complete(completed: asyncio.Task) -> None:
            if completed.cancelled():
                future.cancel()
                return
            error = completed.exception()
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(completed.result())

        task.add_done_callback(complete)
        return future

    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.asyncio.run_coroutine_threadsafe",
        run_on_current_loop,
    )

    await processor.on_cancelled({"data": json.dumps(msg.to_dict())})

    cleanup.assert_awaited_once()
    cleanup_ctx = cleanup.await_args.kwargs["ctx"]
    assert cleanup_ctx.group_ids == ("writers", "reviewers")
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)


@pytest.mark.asyncio
async def test_feishu_parser_api_bypasses_accessor():
    result = object()
    router = SimpleNamespace(
        should_use_understanding_directly=Mock(return_value=True),
        parse=AsyncMock(return_value=result),
    )
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    processor._accessor_registry = SimpleNamespace(
        access=AsyncMock(side_effect=AssertionError("accessor should not be called"))
    )
    source = "https://example.larkoffice.com/docx/doxcnToken"

    actual = await processor.process(source, feishu_access_token=" u-test ")

    assert actual is result
    router.parse.assert_awaited_once()
    (call_source,) = router.parse.await_args.args
    assert call_source == source
    assert router.parse.await_args.kwargs["feishu_access_token"] == " u-test "
    assert "resource_name" not in router.parse.await_args.kwargs


@pytest.mark.asyncio
async def test_feishu_parser_api_uses_app_credentials_for_tenant_token(monkeypatch):
    oauth_client = SimpleNamespace(get_tenant_access_token=AsyncMock(return_value="t-test"))
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.FeishuOAuthClient.from_config",
        Mock(return_value=oauth_client),
    )
    api = _understanding_api_for_parse()

    auth = await api._resolve_lark_file({})

    assert auth == {"tenant_access_token": "t-test"}
    oauth_client.get_tenant_access_token.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_prepared_feishu_response_bypasses_accessor_without_credentials(monkeypatch):
    result = object()
    router = SimpleNamespace(
        should_use_understanding_api=Mock(
            side_effect=AssertionError("prepared response must not re-evaluate routing config")
        ),
        parse=AsyncMock(return_value=result),
    )
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    processor._accessor_registry = SimpleNamespace(
        access=AsyncMock(side_effect=AssertionError("accessor should not be called"))
    )
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.load_feishu_app_credentials",
        Mock(side_effect=ValueError("missing credentials")),
    )

    actual = await processor.process(
        "https://example.larkoffice.com/docx/doxcnToken",
        **{PREPARED_RESPONSE_ID_ARG: "response-1"},
    )

    assert actual is result
    assert router.parse.await_args.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert router.parse.await_args.kwargs["parser_backend"] == "understanding"
    assert "lark_file" not in router.parse.await_args.kwargs


@pytest.mark.asyncio
async def test_feishu_without_understanding_auth_falls_back_to_accessor(monkeypatch, tmp_path):
    markdown_path = tmp_path / "document.md"
    markdown_path.write_text("content", encoding="utf-8")
    resource = LocalResource(
        path=markdown_path,
        source_type=SourceType.FEISHU,
        original_source="https://example.larkoffice.com/docx/doxcnToken",
        meta={"extension": ".md"},
        is_temporary=False,
    )
    result = object()
    router = SimpleNamespace(
        should_use_understanding_directly=Mock(return_value=False),
        parse=AsyncMock(return_value=result),
    )
    accessor = SimpleNamespace(access=AsyncMock(return_value=resource))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    processor._accessor_registry = accessor
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.load_feishu_app_credentials",
        Mock(side_effect=ValueError("missing credentials")),
    )

    actual = await processor.process(resource.original_source)

    assert actual is result
    accessor.access.assert_awaited_once_with(resource.original_source)
    router.parse.assert_awaited_once()
    assert router.parse.await_args.args[0] is resource
    assert "lark_file" not in router.parse.await_args.kwargs


@pytest.mark.asyncio
async def test_prepared_feishu_resource_is_not_bypassed(monkeypatch, tmp_path):
    markdown_path = tmp_path / "document.md"
    markdown_path.write_text("content", encoding="utf-8")
    resource = LocalResource(
        path=markdown_path,
        source_type=SourceType.FEISHU,
        original_source="https://example.larkoffice.com/docx/doxcnToken",
        meta={"resolved_extension": ".md"},
        is_temporary=False,
    )
    result = object()
    router = SimpleNamespace(
        should_use_understanding_api=Mock(
            side_effect=AssertionError("prepared resources must not be routed before parsing")
        ),
        parse=AsyncMock(return_value=result),
    )
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router

    actual = await processor.process(
        resource.original_source,
        prepared_resource=resource,
        feishu_access_token="u-test",
    )

    assert actual is result
    assert router.parse.await_args.args[0] is resource
    assert "lark_file" not in router.parse.await_args.kwargs


def _understanding_api_for_parse() -> UnderstandingAPI:
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    api._video_exts = {"mp4"}
    api._audio_exts = {"mp3"}
    api._image_exts = {"png"}
    return api


def test_single_zip_root_name_repairs_utf8_name_without_flag(tmp_path: Path):
    zip_path = tmp_path / "result.zip"
    member_name = "真实文档标题/0.md"
    placeholder = "x" * len(member_name.encode("utf-8"))
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(placeholder, "content")

    archive = zip_path.read_bytes()
    placeholder_bytes = placeholder.encode("ascii")
    assert archive.count(placeholder_bytes) == 2
    zip_path.write_bytes(archive.replace(placeholder_bytes, member_name.encode("utf-8")))

    assert UnderstandingAPI._single_zip_root_name(zip_path) == "真实文档标题"


@pytest.mark.asyncio
async def test_feishu_parse_sends_uat_and_uses_artifact_root(tmp_path: Path):
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("真实文档标题/0.md", "content")

    api = _understanding_api_for_parse()
    api._create_response_for_url = AsyncMock(return_value={"id": "response-1"})
    api._poll_response = AsyncMock(
        return_value={"status": "completed", "result": {"zip_url": "https://tos/result.zip"}}
    )
    api._download_zip = AsyncMock(return_value=zip_path)
    api._unpack_zip_to_temp_dir = AsyncMock(return_value="viking://temp/result")

    result = await api.parse(
        "https://example.larkoffice.com/wiki/wikicnToken",
        feishu_access_token="u-test",
    )

    api._create_response_for_url.assert_awaited_once_with(
        url="https://example.larkoffice.com/wiki/wikicnToken",
        doc_type="wiki",
        lark_file={"user_access_token": "u-test"},
    )
    api._unpack_zip_to_temp_dir.assert_awaited_once_with(
        zip_path=zip_path,
        resource_name="真实文档标题",
    )
    assert result.root.title == "真实文档标题"
    assert result.meta == {"response_id": "response-1"}
    assert "u-test" not in repr(result.meta)


@pytest.mark.asyncio
async def test_feishu_parse_resumes_prepared_response_without_lark_auth(tmp_path: Path):
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("真实文档标题/0.md", "content")

    api = _understanding_api_for_parse()
    api._create_response_for_url = AsyncMock(
        side_effect=AssertionError("prepared response must not be submitted again")
    )
    api._poll_response = AsyncMock(
        return_value={"status": "completed", "result": {"zip_url": "https://tos/result.zip"}}
    )
    api._download_zip = AsyncMock(return_value=zip_path)
    api._unpack_zip_to_temp_dir = AsyncMock(return_value="viking://temp/result")

    result = await api.parse(
        "https://example.larkoffice.com/docx/doxcnToken",
        **{PREPARED_RESPONSE_ID_ARG: "response-1"},
    )

    api._poll_response.assert_awaited_once_with(response_id="response-1")
    assert result.root.title == "真实文档标题"
    assert result.meta == {"response_id": "response-1"}


@pytest.mark.asyncio
async def test_feishu_parse_keeps_explicit_resource_name(tmp_path: Path):
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("真实文档标题/0.md", "content")

    api = _understanding_api_for_parse()
    api._create_response_for_url = AsyncMock(return_value={"id": "response-1"})
    api._poll_response = AsyncMock(
        return_value={"status": "completed", "result": {"zip_url": "https://tos/result.zip"}}
    )
    api._download_zip = AsyncMock(return_value=zip_path)
    api._unpack_zip_to_temp_dir = AsyncMock(return_value="viking://temp/result")

    result = await api.parse(
        "https://example.larkoffice.com/docx/doxcnToken",
        resource_name="用户名称",
        feishu_access_token="u-test",
    )

    api._unpack_zip_to_temp_dir.assert_awaited_once_with(
        zip_path=zip_path,
        resource_name="用户名称",
    )
    assert result.root.title == "用户名称"


@pytest.mark.asyncio
async def test_legacy_accessor_output_does_not_enable_lark_protocol(tmp_path: Path):
    markdown_path = tmp_path / "document.md"
    markdown_path.write_text("content", encoding="utf-8")
    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("parsed/0.md", "content")

    api = _understanding_api_for_parse()
    api._create_file = AsyncMock(return_value={"id": "file-1"})
    api._create_response_for_file = AsyncMock(return_value={"id": "response-1"})
    api._create_response_for_url = AsyncMock(
        side_effect=AssertionError("accessor output must stay local")
    )
    api._poll_response = AsyncMock(
        return_value={"status": "completed", "result": {"zip_url": "https://tos/result.zip"}}
    )
    api._download_zip = AsyncMock(return_value=zip_path)
    api._unpack_zip_to_temp_dir = AsyncMock(return_value="viking://temp/result")

    await api.parse(
        markdown_path,
        original_source="https://example.larkoffice.com/docx/doxcnToken",
        feishu_access_token="u-test",
    )

    api._create_file.assert_awaited_once_with(local_path=markdown_path)
    api._create_response_for_file.assert_awaited_once_with(file_id="file-1")
    api._create_response_for_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_parse_requires_lark_auth():
    api = _understanding_api_for_parse()

    with pytest.raises(ValueError, match="user or tenant access token is required"):
        await api.parse("https://example.larkoffice.com/docx/doxcnToken")


@pytest.mark.asyncio
async def test_create_response_payload_contains_lark_file(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "response-1"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, content, headers):
            seen["url"] = url
            seen["payload"] = json.loads(content)
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(
        "openviking.parse.understanding_api.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )
    api = _understanding_api_for_parse()
    api._api_base = "https://parser.example/api/v3"
    api._api_key = "parser-key"
    api._http_timeout_sec = 10

    result = await api._create_response_for_url(
        url="https://example.larkoffice.com/base/bascnToken?table=tblToken",
        doc_type="bitable",
        lark_file={"user_access_token": "u-test"},
    )

    assert result == {"id": "response-1"}
    assert seen["payload"]["input"][0]["content"] == [
        {
            "type": "input_file",
            "file_url": "https://example.larkoffice.com/base/bascnToken?table=tblToken",
            "lark_file": {"user_access_token": "u-test"},
        }
    ]


@pytest.mark.asyncio
async def test_submit_url_returns_response_id_without_persisting_auth():
    api = _understanding_api_for_parse()
    api._create_response_for_url = AsyncMock(return_value={"id": "response-1"})

    response_id = await api.submit_url(
        "https://example.larkoffice.com/docx/doxcnToken",
        feishu_access_token="u-test",
    )

    assert response_id == "response-1"
    api._create_response_for_url.assert_awaited_once_with(
        url="https://example.larkoffice.com/docx/doxcnToken",
        doc_type="docx",
        lark_file={"user_access_token": "u-test"},
    )


def test_add_resource_message_round_trips_internal_fields():
    msg = AddResourceMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        bypass_acl=True,
        defer_target_resolution=True,
        cleanup_empty_target_on_failure=True,
        understanding_response_id="response-1",
        internal_task=True,
    )

    restored = AddResourceMsg.from_dict(msg.to_dict())

    assert restored.args == {}
    assert "feishu_access_token" not in json.dumps(restored.to_dict())
    assert restored.bypass_acl is True
    assert restored.defer_target_resolution is True
    assert restored.cleanup_empty_target_on_failure is True
    assert restored.understanding_response_id == "response-1"
    assert restored.job_phase is AddResourcePhase.SOURCE
    assert restored.internal_task is True


def test_add_resource_message_round_trips_processing_mode():
    msg = AddResourceMsg(
        task_id="task-1",
        path="https://example.com/demo.md",
        root_uri="viking://resources/demo",
        account_id="account-1",
        user_id="user-1",
        role="user",
        processing_mode="vectors_only",
    )

    restored = AddResourceMsg.from_dict(msg.to_dict())

    assert restored.processing_mode == "vectors_only"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "preflight_name"),
    [
        ("https://example.larkoffice.com/docx/doxcnToken", None),
        ("https://example.larkoffice.com/sheets/shtcnToken", "Sheet Title"),
        ("https://example.larkoffice.com/base/appToken?table=tblSales", "tblSales"),
    ],
)
async def test_uat_producer_payload_reaches_worker_without_persisting_token(
    monkeypatch,
    source,
    preflight_name,
):
    root_uri = "viking://resources/lark/doxcnToken"
    submit_understanding = AsyncMock(return_value="response-1")
    resource_processor = SimpleNamespace(
        should_use_understanding_directly=lambda _source, **_kwargs: True,
        submit_understanding=submit_understanding,
        process_resource=AsyncMock(
            return_value={
                "status": "success",
                "root_uri": "viking://resources/lark/真实文档标题",
            }
        ),
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    queue_manager = SimpleNamespace(enqueue=AsyncMock())
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        Mock(return_value=queue_manager),
    )

    service = ResourceService(
        viking_fs=SimpleNamespace(),
        resource_processor=resource_processor,
        skill_processor=SimpleNamespace(),
    )
    monkeypatch.setattr(
        service,
        "_connector_delegate",
        SimpleNamespace(should_delegate=Mock(return_value=False)),
    )
    monkeypatch.setattr(
        "openviking.service.resource_service.is_git_repo_url",
        Mock(return_value=False),
    )
    monkeypatch.setattr("openviking.service.resource_service.uuid4", Mock(return_value="task-1"))

    async def preflight(_self, _source, *, feishu_access_token=None):
        assert feishu_access_token == "u-secret"
        return SimpleNamespace(source_name=preflight_name, source_format="file")

    async def plan_source_job_target(*, source_info, **_kwargs):
        return root_uri, None, source_info.source_name is None, False

    monkeypatch.setattr(
        "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
        preflight,
    )
    service._plan_source_job_target = AsyncMock(side_effect=plan_source_job_target)
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    initial_result = await service.add_resource(
        path=source,
        ctx=ctx,
        parent="viking://resources/lark",
        wait=False,
        allow_local_path_resolution=False,
        args={"feishu_access_token": "u-secret", "custom_option": "forwarded"},
    )

    expected_initial = {"status": "success", "task_id": "task-1"}
    if preflight_name:
        expected_initial["root_uri"] = root_uri
    assert initial_result == expected_initial
    assert task_tracker.create.await_args.kwargs["resource_id"] == (
        None if preflight_name is None else root_uri
    )
    assert task_tracker.create.await_args.kwargs["meta"] == {"source_path": source}
    submit_understanding.assert_awaited_once_with(
        source,
        feishu_access_token="u-secret",
        custom_option="forwarded",
    )
    payload = queue_manager.enqueue.await_args.args[1]
    assert queue_manager.enqueue.await_args.args[0] == QueueManager.EXTERNAL_PARSE
    assert "u-secret" not in json.dumps(payload)
    assert payload["understanding_response_id"] == "response-1"
    assert payload["args"] == {"custom_option": "forwarded"}

    queued_msg = AddResourceMsg.from_dict(payload)
    service._execute_resource_ingestion = AsyncMock(
        return_value={
            "status": "success",
            "root_uri": "viking://resources/lark/真实文档标题",
        }
    )
    await service.execute_add_resource_job(
        queued_msg,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    call = service._execute_resource_ingestion.await_args
    assert call.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert call.kwargs["custom_option"] == "forwarded"
    assert call.kwargs["parser_backend"] == "understanding"


@pytest.mark.asyncio
async def test_local_source_job_stages_snapshot_before_enqueue(monkeypatch, tmp_path):
    root_uri = "viking://resources/script"
    source_file = tmp_path / "script.md"
    source_file.write_bytes(b"# durable source")
    prepared_source = LocalResource(
        path=source_file,
        source_type=SourceType.LOCAL,
        original_source=str(source_file),
        is_temporary=False,
    )
    resource_processor = SimpleNamespace(
        prepare_durable_source=AsyncMock(return_value=prepared_source),
        should_use_understanding_api=Mock(return_value=False),
        process_resource=AsyncMock(),
    )
    viking_fs = SimpleNamespace(
        create_temp_uri=Mock(return_value="viking://temp/account-1/source-task"),
        write_file_bytes=AsyncMock(),
        delete_temp=AsyncMock(),
    )

    service = ResourceService(
        viking_fs=viking_fs,
        resource_processor=resource_processor,
        skill_processor=SimpleNamespace(),
    )
    service._enqueue_add_resource_job = AsyncMock(return_value=SimpleNamespace(task_id="task-1"))
    service._plan_source_job_target = AsyncMock(return_value=(root_uri, None, False, False))
    monkeypatch.setattr(
        service,
        "_connector_delegate",
        SimpleNamespace(should_delegate=Mock(return_value=False)),
    )
    monkeypatch.setattr(
        "openviking.service.resource_service.is_git_repo_url",
        Mock(return_value=False),
    )
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    result = await service.add_resource(
        path=str(source_file),
        ctx=ctx,
        to=root_uri,
        wait=False,
    )

    assert result["task_id"] == "task-1"
    call = service._enqueue_add_resource_job.await_args
    assert call.kwargs["queue_name"] == QueueManager.ADD_RESOURCE
    message = call.args[0]
    assert message.job_phase is AddResourcePhase.SOURCE
    assert message.prepared is None
    assert message.staged_source["original_source"] == str(source_file)
    viking_fs.write_file_bytes.assert_awaited_once_with(
        message.staged_source["source_uri"],
        b"# durable source",
        ctx=ctx,
    )
    resource_processor.process_resource.assert_not_awaited()

    viking_fs.tree = AsyncMock(
        return_value=[
            {
                "rel_path": "script.md",
                "isDir": False,
                "uri": message.staged_source["source_uri"],
            }
        ]
    )
    viking_fs.read_file_bytes = AsyncMock(return_value=b"# durable source")
    service._execute_resource_ingestion = AsyncMock(
        return_value={"status": "success", "root_uri": root_uri}
    )

    await service.execute_add_resource_job(
        message,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    materialized = service._execute_resource_ingestion.await_args.kwargs["prepared_resource"]
    try:
        assert materialized.path.read_bytes() == b"# durable source"
        assert materialized.original_source == str(source_file)
    finally:
        materialized.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_stage", ["submit", "enqueue", "handoff"])
async def test_uat_producer_cancellation_respects_queue_ownership(
    monkeypatch,
    cancel_stage,
):
    source = "https://example.larkoffice.com/docx/doxcnToken"
    root_uri = "viking://resources/fixed"
    submit_url = AsyncMock(return_value="response-1")
    enqueue = AsyncMock()
    handoff = AsyncMock()
    release = AsyncMock()
    if cancel_stage == "submit":
        submit_url.side_effect = asyncio.CancelledError
    elif cancel_stage == "enqueue":
        enqueue.side_effect = asyncio.CancelledError
    else:
        handoff.side_effect = asyncio.CancelledError

    resource_processor = SimpleNamespace(
        should_use_understanding_directly=lambda _source, **_kwargs: True,
        submit_understanding=submit_url,
        tree_builder=SimpleNamespace(resolve_target_uri=AsyncMock(return_value=(root_uri, None))),
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        update_stage=AsyncMock(),
        fail=AsyncMock(),
    )
    queue_manager = SimpleNamespace(enqueue=enqueue)
    lock_lease = {"lease_ref": "lock-1"}
    agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value=lock_lease),
        pathlock_to_handoff=AsyncMock(
            return_value={
                "handle_id": "lock-1",
                "lock_paths": ["/resources/fixed"],
            }
        ),
        pathlock_handoff=handoff,
        pathlock_release=release,
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        Mock(return_value=queue_manager),
    )

    service = ResourceService(
        viking_fs=SimpleNamespace(
            exists=AsyncMock(return_value=False),
            stat=AsyncMock(return_value={"isDir": True}),
            ls=AsyncMock(return_value=[]),
            rm=AsyncMock(),
            _uri_to_path=lambda _uri, ctx: "/resources/fixed",
            _async_agfs=agfs,
            _ensure_access=AsyncMock(),
        ),
        resource_processor=resource_processor,
        skill_processor=SimpleNamespace(),
    )
    monkeypatch.setattr(
        service,
        "_connector_delegate",
        SimpleNamespace(should_delegate=Mock(return_value=False)),
    )
    monkeypatch.setattr(
        "openviking.service.resource_service.is_git_repo_url",
        Mock(return_value=False),
    )

    async def preflight(_self, _source, *, feishu_access_token=None):
        assert feishu_access_token == "u-secret"
        return SimpleNamespace(source_name=None, source_format="file")

    monkeypatch.setattr(
        "openviking.parse.accessors.feishu_accessor.FeishuAccessor.preflight_source",
        preflight,
    )
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.add_resource(
            path=source,
            ctx=ctx,
            to=root_uri,
            wait=False,
            allow_local_path_resolution=False,
            args={"feishu_access_token": "u-secret"},
        )

    if cancel_stage == "submit":
        release.assert_not_awaited()
        task_tracker.create.assert_not_awaited()
        task_tracker.fail.assert_not_awaited()
        enqueue.assert_not_awaited()
    else:
        release.assert_awaited_once_with(lock_lease)
        task_tracker.create.assert_awaited_once()
        enqueue.assert_awaited_once()
        assert enqueue.await_args.args[0] == QueueManager.EXTERNAL_PARSE
        if cancel_stage == "enqueue":
            task_tracker.fail.assert_awaited_once()
        else:
            task_tracker.fail.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (PREPARED_RESPONSE_ID_ARG, "response-1"),
        (PREPARED_FILE_ID_ARG, "file-1"),
        ("parser_backend", "understanding"),
        ("resolved_extension", ".pdf"),
    ],
)
def test_internal_parser_fields_are_reserved_from_public_args(field, value):
    service = ResourceService()

    with pytest.raises(InvalidArgumentError, match=field):
        service._normalize_add_resource_args(
            {field: value},
            watch_interval=0,
        )


@pytest.mark.asyncio
async def test_add_resource_job_defers_target_and_expands_prepared_response():
    service = ResourceService()
    service._execute_resource_ingestion = AsyncMock(
        return_value={
            "status": "success",
            "root_uri": "viking://resources/真实文档标题",
        }
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/lark/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        defer_target_resolution=True,
        understanding_response_id="response-1",
    )
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    result = await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    call = service._execute_resource_ingestion.await_args
    assert call.kwargs["to"] is None
    assert call.kwargs["parent"] == "viking://resources/lark"
    assert call.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert result["root_uri"] == "viking://resources/真实文档标题"


@pytest.mark.asyncio
async def test_add_resource_job_expands_prepared_file_id():
    service = ResourceService()
    service._execute_resource_ingestion = AsyncMock(
        return_value={
            "status": "success",
            "root_uri": "viking://resources/uploaded",
        }
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="/tmp/upload_already_cleaned.pdf",
        source_name="uploaded.pdf",
        root_uri="viking://resources/uploaded",
        account_id="account-1",
        user_id="user-1",
        role="user",
        understanding_file_id="file-1",
    )
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    result = await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    call = service._execute_resource_ingestion.await_args
    assert call.kwargs[PREPARED_FILE_ID_ARG] == "file-1"
    assert call.kwargs["parser_backend"] == "understanding"
    assert result["root_uri"] == "viking://resources/uploaded"


@pytest.mark.asyncio
async def test_add_resource_job_expands_parser_args():
    service = ResourceService()
    service._execute_resource_ingestion = AsyncMock(
        return_value={
            "status": "success",
            "root_uri": "viking://resources/doxcnToken",
        }
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        args={"custom_option": "forwarded"},
        processing_mode="vectors_only",
    )
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    call = service._execute_resource_ingestion.await_args
    assert call.kwargs["to"] == "viking://resources/doxcnToken"
    assert call.kwargs["parent"] is None
    assert call.kwargs["custom_option"] == "forwarded"
    assert call.kwargs["processing_mode"] == "vectors_only"


@pytest.mark.asyncio
async def test_prepared_add_resource_job_forwards_processing_mode():
    resource_processor = SimpleNamespace(
        finish_prepared_resource=AsyncMock(
            return_value={"status": "success", "root_uri": "viking://resources/demo"}
        )
    )
    service = ResourceService(resource_processor=resource_processor)
    msg = AddResourceMsg(
        task_id="task-1",
        path="/tmp/demo.md",
        root_uri="viking://resources/demo",
        account_id="account-1",
        user_id="user-1",
        role="user",
        prepared={"root_uri": "viking://resources/demo"},
        processing_mode="vectors_only",
    )
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    await service.execute_add_resource_job(
        msg,
        ctx=ctx,
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    call = resource_processor.finish_prepared_resource.await_args
    assert call.kwargs["processing_mode"] == "vectors_only"


@pytest.mark.asyncio
async def test_add_resource_processor_persists_final_uri_and_cleans_staged_source(
    monkeypatch,
):
    final_uri = "viking://resources/真实文档标题"
    telemetry_id = "telemetry-final-resource"
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(),
    )

    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(
            return_value={"status": "success", "root_uri": final_uri}
        ),
        _link_resource_reason_memory=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    queue_manager = SimpleNamespace(enqueue=AsyncMock())
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        Mock(return_value=queue_manager),
    )
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(pathlock_release=AsyncMock()),
        delete_temp=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        asyncio.get_running_loop(),
        QueueManager.ADD_RESOURCE,
        viking_fs,
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/lark/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        bypass_acl=True,
        telemetry_id=telemetry_id,
        defer_target_resolution=True,
        internal_task=True,
        staged_source={
            "temp_uri": "viking://temp/account-1/task-1",
            "source_uri": "viking://temp/account-1/task-1/source/document.md",
            "source_type": "local",
            "original_source": "/tmp/document.md",
            "meta": {},
        },
    )

    wait_tracker = get_request_wait_tracker()
    wait_tracker.register_request(telemetry_id)
    for index in range(9):
        embedding_id = f"embedding-{index}"
        wait_tracker.register_embedding_root(telemetry_id, embedding_id)
        wait_tracker.mark_embedding_done(telemetry_id, embedding_id, vector_written=True)
    data = msg.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"
    await processor._process(msg, data)

    assert service.execute_add_resource_job.await_args.kwargs["ctx"].bypass_acl is True
    task_tracker.create.assert_awaited_once_with(
        "add_resource",
        resource_id=None,
        account_id="account-1",
        user_id="user-1",
        task_id="task-1",
        meta={"source_path": "", "internal": True},
    )
    assert task_tracker.complete.await_count == 2
    first_complete = task_tracker.complete.await_args_list[0]
    assert first_complete.args == (
        "task-1",
        {"status": "success", "root_uri": final_uri},
    )
    assert first_complete.kwargs == {
        "account_id": "account-1",
        "user_id": "user-1",
        "resource_id": final_uri,
    }
    final_complete = task_tracker.complete.await_args_list[-1]
    assert final_complete.args == (
        "task-1",
        {
            "status": "success",
            "root_uri": final_uri,
            "context_count": 9,
            "queue_status": {
                "Semantic": {
                    "processed": 0,
                    "requeue_count": 0,
                    "error_count": 0,
                    "errors": [],
                },
                "Embedding": {
                    "processed": 9,
                    "requeue_count": 0,
                    "error_count": 0,
                    "errors": [],
                },
            },
        },
    )
    assert final_complete.kwargs == {
        "account_id": "account-1",
        "user_id": "user-1",
        "resource_id": final_uri,
    }
    viking_fs.delete_temp.assert_awaited_once()
    cleanup_call = viking_fs.delete_temp.await_args
    assert cleanup_call.args == ("viking://temp/account-1/task-1",)
    assert cleanup_call.kwargs["ctx"].account_id == "account-1"
    assert cleanup_call.kwargs["ctx"].user.user_id == "user-1"
    assert await processor._requeue_lock_handoff(msg, RuntimeError("stale lock"))
    assert queue_manager.enqueue.await_args.args[0] == QueueManager.ADD_RESOURCE


@pytest.mark.asyncio
@pytest.mark.parametrize("telemetry_id", [None, "telemetry-unregistered-resource"])
async def test_add_resource_processor_collects_stats_without_registered_telemetry(
    monkeypatch,
    telemetry_id: str | None,
):
    final_uri = "viking://resources/unregistered"
    observed_telemetry_ids = []
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )

    async def execute_add_resource_job(*_args, **_kwargs):
        telemetry = get_current_telemetry()
        effective_telemetry_id = telemetry.telemetry_id
        observed_telemetry_ids.append(effective_telemetry_id)
        if telemetry_id is not None:
            assert effective_telemetry_id == telemetry_id
        assert resolve_telemetry(effective_telemetry_id) is telemetry
        assert telemetry.enabled
        telemetry.record_token_usage("embedding", 21)
        telemetry.add_token_usage(13, 5)

        wait_tracker = get_request_wait_tracker()
        wait_tracker.register_embedding_root(effective_telemetry_id, "embedding-1")
        wait_tracker.mark_embedding_done(
            effective_telemetry_id,
            "embedding-1",
            vector_written=True,
        )
        return {"status": "success", "root_uri": final_uri}

    processor = AddResourceProcessor(
        SimpleNamespace(
            execute_add_resource_job=AsyncMock(side_effect=execute_add_resource_job),
            _link_resource_reason_memory=AsyncMock(),
        ),
        asyncio.get_running_loop(),
        QueueManager.ADD_RESOURCE,
        SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock())),
    )
    msg = AddResourceMsg(
        task_id="task-unregistered",
        path="https://example.com/document.pdf",
        root_uri=final_uri,
        account_id="account-1",
        user_id="user-1",
        role="user",
        telemetry_id=telemetry_id,
    )
    data = msg.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"

    await processor._process(msg, data)

    task_tracker.fail.assert_not_awaited()
    final_result = task_tracker.complete.await_args_list[-1].args[1]
    assert final_result["context_count"] == 1
    assert final_result["usage"]["tokens"]["embedding"]["total"] == 21
    assert final_result["usage"]["tokens"]["llm"] == {
        "input": 13,
        "output": 5,
        "total": 18,
    }
    assert len(observed_telemetry_ids) == 1
    assert resolve_telemetry(observed_telemetry_ids[0]) is None


@pytest.mark.asyncio
async def test_add_resource_processor_replay_skips_lock_adopt_when_result_exists(monkeypatch):
    final_uri = "viking://resources/replayed"
    telemetry_id = "telemetry-replayed-resource"
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(
            return_value={"status": "success", "root_uri": "viking://resources/duplicated"}
        ),
        _link_resource_reason_memory=AsyncMock(),
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(
            return_value=SimpleNamespace(
                status=TaskStatus.RUNNING,
                result={"status": "success", "root_uri": final_uri, "context_count": 4},
            )
        ),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    async_agfs = SimpleNamespace(
        pathlock_adopt=AsyncMock(return_value={"lease": "duplicate"}),
        pathlock_release=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        asyncio.get_running_loop(),
        QueueManager.ADD_RESOURCE,
        SimpleNamespace(_async_agfs=async_agfs),
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="/tmp/demo.md",
        root_uri=final_uri,
        account_id="account-1",
        user_id="user-1",
        role="user",
        telemetry_id=telemetry_id,
        lock_handoff={"owner_id": "owner-1", "lock_paths": ["/resource/.path.ovlock"]},
    )
    data = msg.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"

    await processor._process(msg, data)

    async_agfs.pathlock_adopt.assert_not_awaited()
    service.execute_add_resource_job.assert_not_awaited()
    task_tracker.start.assert_not_awaited()
    task_tracker.wait_for_descendants.assert_awaited_once_with("task-1", "work-1")
    task_tracker.complete.assert_awaited_once()
    completed_result = task_tracker.complete.await_args.args[1]
    assert completed_result["root_uri"] == final_uri
    assert completed_result["context_count"] == 4


@pytest.mark.asyncio
async def test_add_resource_processor_reports_zero_vectors(monkeypatch):
    final_uri = "viking://resources/no-vectors"
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(
            return_value={"status": "success", "root_uri": final_uri}
        ),
        _link_resource_reason_memory=AsyncMock(),
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    processor = AddResourceProcessor(
        service,
        asyncio.get_running_loop(),
        QueueManager.ADD_RESOURCE,
        SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock())),
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="/tmp/demo.md",
        root_uri=final_uri,
        account_id="account-1",
        user_id="user-1",
        role="user",
    )
    data = msg.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"

    await processor._process(msg, data)

    task_tracker.fail.assert_not_awaited()
    final_result = task_tracker.complete.await_args.args[1]
    assert final_result["root_uri"] == final_uri
    assert final_result["context_count"] == 0


def test_feishu_direct_submission_requires_configured_auth(monkeypatch):
    api = _understanding_api_for_parse()
    source = "https://example.larkoffice.com/docx/doxcnToken"

    assert api.can_submit_url_directly(source, feishu_access_token="u-test")

    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.load_feishu_app_credentials",
        Mock(side_effect=ValueError("missing credentials")),
    )

    assert not api.can_submit_url_directly(source)


def test_normalize_lark_file_accepts_exactly_one_token():
    assert UnderstandingAPI._normalize_lark_file(
        {"lark_file": {"tenant_access_token": " t-test "}}
    ) == {"tenant_access_token": "t-test"}

    with pytest.raises(ValueError, match="exactly one"):
        UnderstandingAPI._normalize_lark_file(
            {
                "lark_file": {
                    "user_access_token": "u-test",
                    "tenant_access_token": "t-test",
                }
            }
        )
