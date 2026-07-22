import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.understanding_api import PREPARED_RESPONSE_ID_ARG, UnderstandingAPI
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_tracker import TaskStatus
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


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
        defer_target_resolution=True,
        understanding_response_id="response-1",
    )

    restored = AddResourceMsg.from_dict(msg.to_dict())

    assert restored.args == {}
    assert "feishu_access_token" not in json.dumps(restored.to_dict())
    assert restored.defer_target_resolution is True
    assert restored.understanding_response_id == "response-1"


@pytest.mark.asyncio
async def test_uat_producer_payload_reaches_worker_without_persisting_token(monkeypatch):
    source = "https://example.larkoffice.com/docx/doxcnToken"
    root_uri = "viking://resources/lark/doxcnToken"
    submit_understanding = AsyncMock(return_value="response-1")
    resource_processor = SimpleNamespace(
        should_use_understanding_directly=lambda _source, **_kwargs: True,
        submit_understanding=submit_understanding,
        tree_builder=SimpleNamespace(
            resolve_target_uri=AsyncMock(return_value=(root_uri, root_uri))
        ),
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
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        Mock(return_value=SimpleNamespace()),
    )

    service = ResourceService(
        viking_fs=SimpleNamespace(),
        resource_processor=resource_processor,
        skill_processor=SimpleNamespace(),
    )
    service._should_use_connector = lambda *_args, **_kwargs: False
    monkeypatch.setattr("openviking.service.resource_service.is_git_repo_url", lambda _path: False)
    monkeypatch.setattr("openviking.service.resource_service.uuid4", Mock(return_value="task-1"))
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

    assert initial_result == {"status": "success", "task_id": "task-1"}
    assert task_tracker.create.await_args.kwargs["resource_id"] is None
    submit_understanding.assert_awaited_once_with(
        source,
        feishu_access_token="u-secret",
        custom_option="forwarded",
    )
    payload = queue_manager.enqueue.await_args.args[1]
    assert "u-secret" not in json.dumps(payload)
    assert payload["understanding_response_id"] == "response-1"
    assert payload["args"] == {
        "custom_option": "forwarded",
        "parser_backend": "understanding",
    }

    queued_msg = AddResourceMsg.from_dict(payload)
    service.add_resource = AsyncMock(
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

    call = service.add_resource.await_args
    assert call.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert call.kwargs["args"] == {"custom_option": "forwarded"}
    assert call.kwargs["parser_backend"] == "understanding"


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
    lock_lease = SimpleNamespace(
        to_handoff=Mock(
            return_value=SimpleNamespace(
                to_dict=Mock(
                    return_value={
                        "handle_id": "lock-1",
                        "lock_paths": ["/resources/fixed"],
                    }
                )
            )
        ),
        handoff=handoff,
        close=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        Mock(return_value=queue_manager),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        Mock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.OwnedLockLease.acquire_tree",
        AsyncMock(return_value=lock_lease),
    )

    service = ResourceService(
        viking_fs=SimpleNamespace(_uri_to_path=lambda _uri, ctx: "/resources/fixed"),
        resource_processor=resource_processor,
        skill_processor=SimpleNamespace(),
    )
    service._should_use_connector = lambda *_args, **_kwargs: False
    monkeypatch.setattr("openviking.service.resource_service.is_git_repo_url", lambda _path: False)
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

    lock_lease.close.assert_awaited_once_with()
    task_tracker.create.assert_not_awaited()
    task_tracker.fail.assert_not_awaited()
    if cancel_stage == "submit":
        enqueue.assert_not_awaited()
    else:
        enqueue.assert_awaited_once()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (PREPARED_RESPONSE_ID_ARG, "response-1"),
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
    service.add_resource = AsyncMock(
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

    call = service.add_resource.await_args
    assert call.kwargs["to"] is None
    assert call.kwargs["parent"] == "viking://resources/lark"
    assert call.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert call.kwargs["args"] == {}
    assert result["root_uri"] == "viking://resources/真实文档标题"


@pytest.mark.asyncio
async def test_add_resource_job_expands_parser_args():
    service = ResourceService()
    service.add_resource = AsyncMock(
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

    call = service.add_resource.await_args
    assert call.kwargs["to"] == "viking://resources/doxcnToken"
    assert call.kwargs["parent"] is None
    assert call.kwargs["args"] == {"custom_option": "forwarded"}


@pytest.mark.asyncio
async def test_add_resource_processor_persists_final_resource_uri(monkeypatch):
    final_uri = "viking://resources/真实文档标题"
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(
            return_value={"status": "success", "root_uri": final_uri}
        )
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    processor = AddResourceProcessor(service, asyncio.get_running_loop())
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

    await processor._process(msg, msg.to_dict())

    task_tracker.create.assert_awaited_once_with(
        "add_resource",
        resource_id=None,
        account_id="account-1",
        user_id="user-1",
        task_id="task-1",
    )
    task_tracker.complete.assert_awaited_once_with(
        "task-1",
        {"status": "success", "root_uri": final_uri},
        account_id="account-1",
        user_id="user-1",
        resource_id=final_uri,
    )


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
