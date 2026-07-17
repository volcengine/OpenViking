import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.parse.understanding_api import PREPARED_RESPONSE_ID_ARG, UnderstandingAPI
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.storage.queuefs.understanding_parse_msg import UnderstandingParseMsg
from openviking.storage.queuefs.understanding_parse_processor import UnderstandingParseProcessor
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_feishu_parser_api_bypasses_accessor():
    result = object()
    router = SimpleNamespace(
        should_use_understanding_api=lambda source: True,
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
    assert router.parse.await_args.kwargs["lark_file"] == {"user_access_token": "u-test"}
    assert "resource_name" not in router.parse.await_args.kwargs


@pytest.mark.asyncio
async def test_feishu_parser_api_uses_app_credentials_for_tenant_token(monkeypatch):
    result = object()
    router = SimpleNamespace(
        should_use_understanding_api=lambda source: True,
        parse=AsyncMock(return_value=result),
    )
    oauth_client = SimpleNamespace(get_tenant_access_token=AsyncMock(return_value="t-test"))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    processor._accessor_registry = SimpleNamespace(
        access=AsyncMock(side_effect=AssertionError("accessor should not be called"))
    )
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.load_feishu_app_credentials",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.FeishuOAuthClient.from_config",
        Mock(return_value=oauth_client),
    )

    actual = await processor.process("https://example.larkoffice.com/docx/doxcnToken")

    assert actual is result
    assert router.parse.await_args.kwargs["lark_file"] == {"tenant_access_token": "t-test"}
    oauth_client.get_tenant_access_token.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_prepared_feishu_response_bypasses_accessor_without_credentials(monkeypatch):
    result = object()
    router = SimpleNamespace(
        should_use_understanding_api=lambda source: True,
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
    api._create_response_for_url = AsyncMock(return_value={"id": "response-1"})
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

    api._create_response_for_url.assert_awaited_once_with(
        url="https://example.larkoffice.com/docx/doxcnToken",
        doc_type="unknown",
        lark_file=None,
    )


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


def test_external_parse_message_round_trips_internal_fields():
    msg = UnderstandingParseMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        defer_target_resolution=True,
        understanding_response_id="response-1",
    )

    restored = UnderstandingParseMsg.from_dict(msg.to_dict())

    assert restored.args == {}
    assert "feishu_access_token" not in restored.to_json()
    assert restored.defer_target_resolution is True
    assert restored.understanding_response_id == "response-1"


@pytest.mark.asyncio
async def test_uat_producer_payload_reaches_worker_without_persisting_token(monkeypatch):
    source = "https://example.larkoffice.com/docx/doxcnToken"
    root_uri = "viking://resources/lark/doxcnToken"
    parser_router = SimpleNamespace(
        should_use_understanding_api=lambda _source: True,
        submit_url=AsyncMock(return_value="response-1"),
    )
    resource_processor = SimpleNamespace(
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
        "openviking.storage.queuefs.understanding_parse_processor.get_task_tracker",
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
    service._parser_router = parser_router
    monkeypatch.setattr(service, "_is_feishu_url", Mock(return_value=True))
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
    parser_router.submit_url.assert_awaited_once_with(
        source,
        feishu_access_token="u-secret",
    )
    payload = queue_manager.enqueue.await_args.args[1]
    assert "u-secret" not in json.dumps(payload)
    assert payload["understanding_response_id"] == "response-1"
    assert payload["args"] == {"custom_option": "forwarded"}

    await UnderstandingParseProcessor(resource_processor).on_dequeue(payload)

    call = resource_processor.process_resource.await_args
    assert call.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert call.kwargs["custom_option"] == "forwarded"
    assert "args" not in call.kwargs


def test_prepared_response_id_is_reserved_from_public_args():
    service = ResourceService()

    with pytest.raises(InvalidArgumentError, match=PREPARED_RESPONSE_ID_ARG):
        service._normalize_add_resource_args(
            {PREPARED_RESPONSE_ID_ARG: "response-1"},
            watch_interval=0,
        )


@pytest.mark.asyncio
async def test_external_parse_worker_defers_target_and_expands_prepared_response(monkeypatch):
    resource_processor = SimpleNamespace(
        process_resource=AsyncMock(
            return_value={
                "status": "success",
                "root_uri": "viking://resources/真实文档标题",
            }
        )
    )
    task_tracker = SimpleNamespace(
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.understanding_parse_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    processor = UnderstandingParseProcessor(resource_processor)
    msg = UnderstandingParseMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/lark/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        defer_target_resolution=True,
        understanding_response_id="response-1",
    )

    await processor.on_dequeue(msg.to_dict())

    call = resource_processor.process_resource.await_args
    assert call.kwargs["to"] is None
    assert call.kwargs["parent"] == "viking://resources/lark"
    assert call.kwargs[PREPARED_RESPONSE_ID_ARG] == "response-1"
    assert "create_parent" not in call.kwargs
    assert "args" not in call.kwargs
    task_tracker.complete.assert_awaited_once()
    completed_result = task_tracker.complete.await_args.args[1]
    assert completed_result["root_uri"] == "viking://resources/真实文档标题"
    assert task_tracker.complete.await_args.kwargs["resource_id"] == (
        "viking://resources/真实文档标题"
    )


@pytest.mark.asyncio
async def test_external_parse_worker_expands_parser_args(monkeypatch):
    resource_processor = SimpleNamespace(
        process_resource=AsyncMock(
            return_value={
                "status": "success",
                "root_uri": "viking://resources/doxcnToken",
            }
        )
    )
    task_tracker = SimpleNamespace(
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.understanding_parse_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    processor = UnderstandingParseProcessor(resource_processor)
    msg = UnderstandingParseMsg(
        task_id="task-1",
        path="https://example.larkoffice.com/docx/doxcnToken",
        root_uri="viking://resources/doxcnToken",
        account_id="account-1",
        user_id="user-1",
        role="user",
        args={"custom_option": "forwarded"},
    )

    await processor.on_dequeue(msg.to_dict())

    call = resource_processor.process_resource.await_args
    assert call.kwargs["to"] == "viking://resources/doxcnToken"
    assert call.kwargs["parent"] is None
    assert call.kwargs["custom_option"] == "forwarded"
    assert "args" not in call.kwargs
    task_tracker.complete.assert_awaited_once()


def test_feishu_bypass_requires_configured_auth(monkeypatch):
    router = SimpleNamespace(
        should_use_understanding_api=lambda source: True,
    )
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._parser_router = router
    monkeypatch.setattr(
        "openviking.resource.feishu_watch_auth.load_feishu_app_credentials",
        Mock(side_effect=ValueError("missing credentials")),
    )

    assert not processor._should_bypass_feishu_accessor(
        "https://example.larkoffice.com/docx/doxcnToken",
        {},
    )


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
