"""Regression coverage for Feishu source preparation and directory execution."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.accessors.feishu_accessor import FeishuAccessor, _FeishuWikiTreeNode
from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.feishu_import import FeishuImportPlan
from openviking.parse.parser_router import ParserRouter
from openviking.parse.parsers.directory import DirectoryParser
from openviking.parse.understanding_api import UnderstandingAPI
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError
from tests.parse.test_add_directory import FakeVikingFS
from tests.parse.test_directory_understanding_routing import _configure_understanding


@pytest.mark.parametrize(
    "path,options",
    [
        ("drive/folder/folder", {}),
        ("file/pdf", {}),
        ("wiki/root", {"feishu_recursive": True}),
    ],
)
def test_unsupported_direct_sources_require_preparation(path, options):
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    assert not api.can_submit_url_directly(
        f"https://example.feishu.cn/{path}", feishu_access_token="user-token", **options
    )


@pytest.mark.asyncio
async def test_downloaded_file_routes_by_extension_but_normalized_markdown_does_not(
    monkeypatch, tmp_path
):
    _configure_understanding(monkeypatch, ["pdf", "md"])
    api = SimpleNamespace(parse=AsyncMock(return_value=object()))
    registry = SimpleNamespace(parse=AsyncMock(return_value=object()))
    router = ParserRouter(registry)
    router._understanding_api = api
    for name, kind in [("doc.pdf", "file"), ("doc.md", "markdown")]:
        resource = LocalResource(
            tmp_path / name,
            SourceType.FEISHU,
            "https://example.feishu.cn/file/t",
            meta={"feishu_content_kind": kind},
        )
        await router.parse(resource)
    api.parse.assert_awaited_once()
    assert api.parse.await_args.args[0].endswith("doc.pdf")
    registry.parse.assert_awaited_once()


@pytest.mark.asyncio
async def test_folder_remote_doc_and_empty_directory(monkeypatch):
    accessor = FeishuAccessor()
    monkeypatch.setattr(accessor, "_drive_folder_display_name", lambda *a, **k: "Folder")
    monkeypatch.setattr(
        accessor,
        "_list_drive_folder_children",
        lambda token, **k: (
            [
                SimpleNamespace(
                    type="docx",
                    token="doc",
                    name="Document",
                    url="https://example.feishu.cn/docx/doc",
                ),
                SimpleNamespace(type="folder", token="sub", name="Empty", url=""),
            ]
            if token == "root"
            else []
        ),
    )
    monkeypatch.setattr(
        accessor, "_fetch_document", AsyncMock(side_effect=AssertionError("no Markdown conversion"))
    )
    resource = await accessor.access(
        "https://example.feishu.cn/drive/folder/root", _feishu_use_understanding=True
    )
    try:
        assert len(resource.feishu_plan.entries) == 1
        assert resource.feishu_plan.entries[0].kind == "url"
        assert (resource.path / "Empty").is_dir()
        assert not (resource.path / "Document.md").exists()
    finally:
        resource.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_directory_executes_url_with_auth_and_preserves_output_parent(
    monkeypatch, tmp_path, resume
):
    _configure_understanding(monkeypatch, ["pdf"])
    (tmp_path / "nested").mkdir()
    (tmp_path / "empty").mkdir()
    plan = FeishuImportPlan(tmp_path, use_understanding=True)
    path = tmp_path / "nested/Cloud.md"
    url = "https://example.feishu.cn/base/app?table=t&view=v"
    plan.add(path, url, "app", "url")
    fs = FakeVikingFS()
    parser = DirectoryParser()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(parser, "_create_temp_uri", lambda: "viking://temp/import")
    calls = []

    async def parse_api(self, source, **options):
        calls.append((source, options))
        await options["_response_checkpoint"]("response-1")
        await fs.mkdir("viking://temp/doc/Cloud", exist_ok=True)
        await fs.write("viking://temp/doc/Cloud/0.md", "body")
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title="Cloud"),
            source_path=source,
            source_format="docx",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = "viking://temp/doc"
        result.meta["response_id"] = "response-1"
        return result

    monkeypatch.setattr(UnderstandingAPI, "parse", parse_api)
    save = AsyncMock()
    result = await parser.parse(
        tmp_path,
        source_name="Folder",
        _feishu_import_plan=plan,
        feishu_access_token="secret",
        _feishu_checkpoint=(
            {plan.entries[0].checkpoint_key(plan.root): "old"} if resume else {},
            save,
        ),
    )
    assert calls[0][0] == url
    assert calls[0][1]["feishu_access_token"] == "secret"
    assert calls[0][1]["resource_name"] == "Cloud.md"
    assert calls[0][1].get("understanding_response_id") == ("old" if resume else None)
    save.assert_awaited_once_with(plan.entries[0].checkpoint_key(plan.root), "response-1")
    assert result.meta["failed_files"] == []
    assert "viking://temp/import/Folder/nested/Cloud/0.md" in fs.files
    assert "viking://temp/import/Folder/empty" not in fs.dirs
    assert "secret" not in str(result.meta)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "native", "understanding"])
async def test_directory_omits_source_directories_without_imported_content(
    monkeypatch, tmp_path, mode
):
    _configure_understanding(monkeypatch, ["pdf"])
    plan = FeishuImportPlan(tmp_path, use_understanding=mode == "understanding")
    (tmp_path / "empty/deep").mkdir(parents=True)
    (tmp_path / "kept/nested").mkdir(parents=True)
    (tmp_path / "kept/nested/data.json").write_text('{"body": "keep"}')
    for directory, name in [("failed", "Broken.md"), ("filtered", "Private.md")]:
        path = tmp_path / directory / name
        path.parent.mkdir()
        if mode != "understanding":
            path.write_text("document")
        plan.add(
            path,
            f"https://example.feishu.cn/docx/{directory}",
            directory,
            "url" if mode == "understanding" else "markdown",
        )
    fs = FakeVikingFS()
    parser = DirectoryParser()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(parser, "_create_temp_uri", lambda: "viking://temp/empty-filter")
    parse = AsyncMock(side_effect=ValueError("document parse failed"))
    monkeypatch.setattr(DirectoryParser, "_parse_file_with_parser", parse)
    result = await parser.parse(
        tmp_path,
        source_name="Folder",
        preserve_structure=True,
        exclude="Private.md",
        **({"_feishu_import_plan": plan} if mode != "legacy" else {}),
    )
    parse.assert_awaited_once()
    assert result.meta["file_count"] == 1
    assert len(result.meta["failed_files"]) == 1
    assert result.meta["failed_files"][0]["path"] == "failed/Broken.md"
    target = "viking://temp/empty-filter/Folder"
    assert fs.files[f"{target}/kept/nested/data.json"] == b'{"body": "keep"}'
    assert [entry["name"] for entry in await fs.ls(target)] == ["kept"]


@pytest.mark.asyncio
async def test_directory_virtual_url_obeys_include_filter(monkeypatch, tmp_path):
    _configure_understanding(monkeypatch, [])
    plan = FeishuImportPlan(tmp_path, use_understanding=True)
    plan.add(tmp_path / "Private.md", "https://example.feishu.cn/docx/private", "private", "url")
    parser = DirectoryParser()
    fs = FakeVikingFS()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(parser, "_create_temp_uri", lambda: "viking://temp/filter")
    parse = AsyncMock(side_effect=AssertionError("excluded URL was submitted"))
    monkeypatch.setattr(UnderstandingAPI, "parse", parse)
    result = await parser.parse(tmp_path, _feishu_import_plan=plan, include="*.pdf")
    parse.assert_not_called()
    assert result.meta["file_count"] == 0


@pytest.mark.asyncio
async def test_media_prepare_enables_url_plan_only_for_correct_backend(monkeypatch, tmp_path):
    _configure_understanding(monkeypatch, ["pdf"])
    processor = UnifiedResourceProcessor(vlm_processor=object())
    resource = LocalResource(
        tmp_path, SourceType.FEISHU, "https://example.feishu.cn/drive/folder/f", is_temporary=False
    )
    access = AsyncMock(return_value=resource)
    processor._accessor_registry = SimpleNamespace(access=access)
    await processor.prepare(resource.original_source, parser_backend="understanding")
    assert access.await_args.kwargs["_feishu_use_understanding"]
    await processor.prepare(resource.original_source, parser_backend="internal")
    assert not access.await_args.kwargs["_feishu_use_understanding"]
    await processor.prepare(
        resource.original_source, parser_backend="understanding", parse_mode="no_split"
    )
    assert not access.await_args.kwargs["_feishu_use_understanding"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,recursive", [("folder", False), ("wiki", True), ("file", False)])
async def test_async_source_plan_does_not_submit_collection_or_file_url(
    monkeypatch, kind, recursive
):
    from openviking.parse.mode import ParseMode
    from openviking.server.identity import RequestContext, Role
    from openviking.service.resource_service import ResourceService
    from openviking_cli.session.user_id import UserIdentifier

    _configure_understanding(monkeypatch, ["pdf"])
    router = ParserRouter(SimpleNamespace())
    router._understanding_api = UnderstandingAPI.__new__(UnderstandingAPI)
    submit = AsyncMock(side_effect=AssertionError("root URL must not be submitted"))
    service = ResourceService(
        viking_fs=SimpleNamespace(),
        skill_processor=SimpleNamespace(),
        resource_processor=SimpleNamespace(
            should_use_understanding_directly=router.should_use_understanding_directly,
            submit_understanding=submit,
        ),
    )
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            doc_type=kind,
            source_format="file" if kind == "file" else "directory",
            source_name="Root",
        )
    )
    monkeypatch.setattr(FeishuAccessor, "preflight_source", preflight)
    path = "drive/folder/root" if kind == "folder" else f"{kind}/root"
    plan = await service._prepare_standard_source_plan(
        path=f"https://example.feishu.cn/{path}",
        ctx=RequestContext(user=UserIdentifier("account", "user"), role=Role.USER),
        mode=ParseMode.DEFAULT,
        allow_local_path_resolution=False,
        processor_kwargs={"feishu_recursive": recursive, "feishu_access_token": "secret"},
        watch_auth_state=None,
    )
    submit.assert_not_awaited()
    assert plan.understanding_response_id is None
    assert plan.processor_args["_feishu_prepared_source"]
    assert "secret" not in str(plan.processor_args)
    assert plan.task_auth["access_token"] == "secret"
    if recursive:
        assert preflight.await_args.kwargs["feishu_recursive"]


@pytest.mark.asyncio
async def test_checkpoint_is_written_before_polling():
    from openviking.parse.understanding_api import UnderstandingAPIError
    from tests.parse.test_feishu_parser_api import _understanding_api_for_parse

    api = _understanding_api_for_parse()
    api._create_response_for_url = AsyncMock(return_value={"id": "response-1"})
    saved = []

    async def record(response_id):
        saved.append(response_id)

    async def poll(**kwargs):
        assert saved == ["response-1"]
        raise TimeoutError("waiting")

    api._poll_response = poll
    with pytest.raises(UnderstandingAPIError) as error:
        await api.parse(
            "https://example.feishu.cn/docx/doc",
            feishu_access_token="secret",
            _response_checkpoint=record,
        )
    assert error.value.meta["response_id"] == "response-1"


@pytest.mark.asyncio
async def test_different_extensions_cannot_share_an_output_name(monkeypatch, tmp_path):
    accessor = FeishuAccessor()
    monkeypatch.setattr(
        accessor,
        "_download_drive_file",
        lambda *a, **k: (b"%PDF-1.7", "application/pdf", "Report.pdf"),
    )
    plan = FeishuImportPlan(tmp_path, use_understanding=True)
    await accessor._write_import_content("docx", "doc", "Report", "", tmp_path, plan)
    await accessor._write_import_content("file", "pdf", "Report.pdf", "", tmp_path, plan)
    assert len({entry.path.stem for entry in plan.entries}) == 2


def test_pagination_budget_stops_before_next_page(monkeypatch):
    accessor = FeishuAccessor()
    page = Mock(
        return_value=([SimpleNamespace(token="a"), SimpleNamespace(token="b")], True, "next")
    )
    monkeypatch.setattr(accessor, "_fetch_drive_folder_children_page", page)
    assert len(accessor._list_drive_folder_children("root", max_items=1)) == 2
    page.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [None, "understanding"])
async def test_folder_processor_routes_each_content_and_keeps_tree(monkeypatch, backend):
    _configure_understanding(monkeypatch, ["pdf"])
    from openviking_cli.utils.config.open_viking_config import get_openviking_config

    get_openviking_config().parser_api.enable_feishu_url = True
    accessor = FeishuAccessor()
    monkeypatch.setattr(accessor, "_drive_folder_display_name", lambda *a, **k: "Root")
    monkeypatch.setattr(
        accessor,
        "_list_drive_folder_children",
        lambda token, **k: (
            [
                SimpleNamespace(type="docx", token="rootdoc", name="Root", url=""),
                SimpleNamespace(type="folder", token="nested", name="Report", url=""),
            ]
            if token == "root"
            else [
                SimpleNamespace(type="file", token="pdf", name="Report.pdf", url=""),
                SimpleNamespace(
                    type="bitable",
                    token="leaf",
                    name="Leaf",
                    url="https://example.larksuite.com/base/leaf?table=t&view=v",
                ),
            ]
        ),
    )
    monkeypatch.setattr(
        accessor,
        "_download_drive_file",
        lambda *a, **k: (b"%PDF-1.7", "application/pdf", "Report.pdf"),
    )
    monkeypatch.setattr(
        accessor,
        "_fetch_document",
        AsyncMock(side_effect=AssertionError("cloud docs must remain URLs")),
    )
    resources = []

    async def access(source, **options):
        resource = await accessor.access(source, **options)
        resources.append(resource)
        return resource

    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = SimpleNamespace(access=access)
    fs = FakeVikingFS()
    monkeypatch.setattr(DirectoryParser, "_get_viking_fs", lambda self: fs)
    monkeypatch.setattr(DirectoryParser, "_create_temp_uri", lambda self: "viking://temp/tree")
    calls = []

    async def parse_api(self, source, **options):
        calls.append((source, options))
        name = Path(options.get("resource_name") or source).stem
        temporary = f"viking://temp/content_{len(calls)}"
        await fs.mkdir(f"{temporary}/{name}", exist_ok=True)
        await fs.write(f"{temporary}/{name}/0.md", "body")
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=name),
            source_path=source,
            source_format="docx" if source.startswith("https:") else "pdf",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = temporary
        return result

    monkeypatch.setattr(UnderstandingAPI, "parse", parse_api)
    try:
        result = await processor.process(
            "https://example.larksuite.com/drive/folder/root",
            feishu_access_token="secret",
            parser_backend=backend,
        )
        assert result.meta["failed_files"] == []
        assert result.meta["file_count"] == 3
        cloud_calls = [
            (source, options) for source, options in calls if source.startswith("https:")
        ]
        assert {source for source, _ in cloud_calls} == {
            "https://example.larksuite.com/docx/rootdoc",
            "https://example.larksuite.com/base/leaf?table=t&view=v",
        }
        assert all(options["feishu_access_token"] == "secret" for _, options in cloud_calls)
        binary_options = next(
            options for source, options in calls if not source.startswith("https:")
        )
        assert "feishu_access_token" not in binary_options
        assert "lark_file" not in binary_options
        assert "viking://temp/tree/Root/Root/0.md" in fs.files
        assert "viking://temp/tree/Root/Report/Leaf/0.md" in fs.files
        assert "viking://temp/tree/Root/Report/Report/0.md" in fs.files
    finally:
        for resource in resources:
            resource.is_temporary = True
            resource.cleanup()


@pytest.mark.asyncio
async def test_background_collection_restores_auth_and_child_checkpoint(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.service.resource_service import ResourceService
    from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
    from openviking_cli.session.user_id import UserIdentifier

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(meta={"feishu_responses": {"entry": "old"}})),
        record_feishu_response=AsyncMock(),
    )
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    service = ResourceService()
    service._execute_resource_ingestion = AsyncMock(return_value={"status": "success"})
    msg = AddResourceMsg(
        task_id="task",
        path="https://example.feishu.cn/drive/folder/root",
        root_uri="viking://resources/Root",
        account_id="account",
        user_id="user",
        role="user",
        args={"_feishu_prepared_source": True},
    )
    service._restore_source_task_auth = Mock(return_value=({"feishu_access_token": "secret"}, None))
    await service.execute_add_resource_job(
        msg,
        ctx=RequestContext(user=UserIdentifier("account", "user"), role=Role.USER),
        resource_lock=None,
        stage_callback=AsyncMock(),
    )
    options = service._execute_resource_ingestion.await_args.kwargs
    assert options["parser_backend"] is None
    assert options["feishu_access_token"] == "secret"
    saved, record = options["_feishu_checkpoint"]
    assert saved == {"entry": "old"}
    await record("next", "new")
    tracker.record_feishu_response.assert_awaited_once_with(
        "task", "next", "new", "account", "user"
    )
    assert saved["next"] == "new"


@pytest.mark.asyncio
async def test_resume_polls_original_response_without_resubmitting():
    from openviking.parse.understanding_api import UnderstandingAPIError
    from tests.parse.test_feishu_parser_api import _understanding_api_for_parse

    api = _understanding_api_for_parse()
    api._create_response_for_url = AsyncMock(side_effect=AssertionError("duplicate submission"))
    api._resolve_lark_file = AsyncMock(side_effect=AssertionError("unnecessary auth resolution"))
    api._poll_response = AsyncMock(side_effect=TimeoutError("still waiting"))
    with pytest.raises(UnderstandingAPIError):
        await api.parse("https://example.feishu.cn/docx/doc", understanding_response_id="old")
    api._poll_response.assert_awaited_once_with(response_id="old")
    api._create_response_for_url.assert_not_awaited()
    api._resolve_lark_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_drive_wiki_reference_resolves_backing_content(monkeypatch, tmp_path):
    accessor = FeishuAccessor()
    monkeypatch.setattr(accessor, "_resolve_wiki_node", lambda *a: ("base", "app", "Table"))
    plan = FeishuImportPlan(tmp_path, use_understanding=True)
    url = "https://example.feishu.cn/wiki/ref?table=t&view=v"
    await accessor._write_import_content("wiki", "ref", "Table", url, tmp_path, plan)
    assert plan.entries[0].url == url
    assert plan.entries[0].token == "app"
    assert plan.entries[0].kind == "url"


@pytest.mark.asyncio
async def test_source_preparation_does_not_silently_ignore_explicit_lark_auth():
    with pytest.raises(InvalidArgumentError, match="feishu_access_token"):
        await FeishuAccessor().access(
            "https://example.feishu.cn/drive/folder/root",
            lark_file={"tenant_access_token": "secret"},
        )
    from openviking.service.resource_service import ResourceService

    assert "lark_file" not in ResourceService()._sanitize_watch_processor_kwargs(
        {"lark_file": {"tenant_access_token": "secret"}}
    )


@pytest.mark.parametrize("filter_kind", ["gitignore", "ignore_dirs"])
def test_virtual_content_uses_directory_filters(tmp_path, filter_kind):
    from openviking.parse.gitignore import GitignoreMatcher

    (tmp_path / "private").mkdir()
    options = {}
    if filter_kind == "gitignore":
        (tmp_path / ".gitignore").write_text("private/\n")
    else:
        options["ignore_dirs"] = {"private"}
    assert not DirectoryParser._include_feishu_path(
        tmp_path / "private/Document.md",
        tmp_path,
        options,
        gitignore=GitignoreMatcher(tmp_path),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["default", "no_split"])
async def test_single_feishu_file_no_split_uses_native_parser(monkeypatch, tmp_path, mode):
    _configure_understanding(monkeypatch, ["pdf"])
    pdf = tmp_path / "Report.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    source = "https://example.feishu.cn/file/pdf"
    resource = LocalResource(
        pdf,
        SourceType.FEISHU,
        source,
        meta={"feishu_content_kind": "file"},
        is_temporary=False,
    )
    registry = SimpleNamespace(parse=AsyncMock(return_value=object()))
    api = SimpleNamespace(parse=AsyncMock(return_value=object()))
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = SimpleNamespace(access=AsyncMock(return_value=resource))
    processor._parser_router = ParserRouter(registry)
    processor._parser_router._understanding_api = api
    await processor.process(source, parse_mode=mode)
    if mode == "no_split":
        registry.parse.assert_awaited_once()
        assert registry.parse.await_args.kwargs["split_content"] is False
        api.parse.assert_not_awaited()
    else:
        api.parse.assert_awaited_once()
        registry.parse.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("failure_stage", ["parse", "merge"])
async def test_directory_failure_cleans_unmerged_artifacts(
    monkeypatch, tmp_path, strict, failure_stage
):
    _configure_understanding(monkeypatch, ["pdf"])
    plan = FeishuImportPlan(tmp_path, use_understanding=True)
    for name in ("A", "B"):
        plan.add(tmp_path / f"{name}.md", f"https://example.feishu.cn/docx/{name}", name, "url")
    fs = FakeVikingFS()
    parser = DirectoryParser()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(parser, "_create_temp_uri", lambda: "viking://temp/import")

    async def parse_api(self, source, **options):
        name = source.rsplit("/", 1)[-1]
        if name == "A" and failure_stage == "parse":
            raise ValueError("denied")
        temporary = f"viking://temp/doc_{name}"
        await fs.mkdir(f"{temporary}/{name}", exist_ok=True)
        await fs.write(f"{temporary}/{name}/0.md", "body")
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=name),
            source_path=source,
            source_format="docx",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = temporary
        return result

    move_file = fs.move_file

    async def move_with_failure(source, target):
        if "/A/" in source:
            raise OSError("merge denied")
        await move_file(source, target)

    monkeypatch.setattr(fs, "move_file", move_with_failure)
    monkeypatch.setattr(UnderstandingAPI, "parse", parse_api)
    if strict:
        with pytest.raises(InvalidArgumentError, match="denied"):
            await parser.parse(tmp_path, _feishu_import_plan=plan, strict=True)
        assert not fs.files
        assert not fs.dirs
    else:
        result = await parser.parse(tmp_path, _feishu_import_plan=plan)
        assert result.meta["file_count"] == 1
        assert len(result.meta["failed_files"]) == 1
        assert any(path.endswith("/B/0.md") for path in fs.files)
        assert not any(path.startswith("viking://temp/doc_") for path in fs.files)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,extensions", [("url", ["pdf"]), ("url", []), ("file", ["pdf"])])
@pytest.mark.parametrize("depth", [1, 2])
async def test_feishu_collection_obeys_understanding_depth(
    monkeypatch, tmp_path, kind, extensions, depth
):
    from openviking_cli.utils.config.open_viking_config import get_openviking_config

    _configure_understanding(monkeypatch, extensions, max_depth=1)
    get_openviking_config().parser_api.enable_feishu_url = True
    parent = tmp_path.joinpath(*[f"level-{i}" for i in range(depth)])
    parent.mkdir(parents=True)
    plan = FeishuImportPlan(tmp_path, use_understanding=kind == "url")
    url = f"https://example.feishu.cn/{'docx' if kind == 'url' else 'file'}/cloud"
    path = parent / ("Cloud.md" if kind == "url" else "Cloud.pdf")
    if kind == "file":
        path.write_bytes(b"%PDF-1.7")
    plan.add(path, url, "cloud", kind)
    fs = FakeVikingFS()
    result = create_parse_result(
        root=ResourceNode(type=NodeType.ROOT, title="Cloud"),
        source_path=url,
        source_format="docx",
        parser_name="UnderstandingAPI",
    )
    result.temp_dir_path = "viking://temp/remote"
    await fs.mkdir("viking://temp/remote/Cloud", exist_ok=True)
    await fs.write("viking://temp/remote/Cloud/0.md", "cloud body")
    api_parse = AsyncMock(return_value=result)
    parser = DirectoryParser()
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(UnderstandingAPI, "parse", api_parse)
    if depth > 1:
        with pytest.raises(InvalidArgumentError, match="depth"):
            await parser.parse(tmp_path, _feishu_import_plan=plan, strict=True)
        api_parse.assert_not_awaited()
    else:
        result = await parser.parse(tmp_path, _feishu_import_plan=plan, strict=True)
        api_parse.assert_awaited_once()
        assert result.meta["file_count"] == 1
        assert result.meta["failed_files"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "no_split", "internal"])
async def test_native_feishu_collection_keeps_source_depth_limit(monkeypatch, tmp_path, mode):
    _configure_understanding(monkeypatch, ["md"], enabled=mode != "disabled", max_depth=1)
    parent = tmp_path / "a" / "b"
    parent.mkdir(parents=True)
    path = parent / "Document.md"
    path.write_text("body", encoding="utf-8")
    plan = FeishuImportPlan(tmp_path, use_understanding=False, max_depth=20)
    plan.add(path, "https://example.feishu.cn/docx/doc", "doc", "markdown")
    parser = DirectoryParser()
    fs = FakeVikingFS()
    native_parse = AsyncMock(return_value={"ok": True, "meta": {}, "error": None})
    api_parse = AsyncMock(side_effect=AssertionError("Understanding must not be submitted"))
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(parser, "_process_single_file", native_parse)
    monkeypatch.setattr(UnderstandingAPI, "parse", api_parse)

    result = await parser.parse(
        tmp_path,
        _feishu_import_plan=plan,
        split_content=mode != "no_split",
        parser_backend="internal" if mode == "internal" else None,
    )

    native_parse.assert_awaited_once()
    api_parse.assert_not_awaited()
    assert result.meta["file_count"] == 1
    assert result.meta["failed_files"] == []


@pytest.mark.asyncio
async def test_native_feishu_collection_keeps_binary_on_native_parser(monkeypatch, tmp_path):
    _configure_understanding(monkeypatch, ["pdf"])
    path = tmp_path / "Report.pdf"
    path.write_bytes(b"%PDF-1.7")
    plan = FeishuImportPlan(tmp_path)
    plan.add(path, "https://example.feishu.cn/file/report", "report", "file")
    parser = DirectoryParser()
    fs = FakeVikingFS()
    native_parse = AsyncMock(return_value={"ok": True, "meta": {}, "error": None})
    api_parse = AsyncMock(side_effect=AssertionError("Understanding must not be submitted"))
    monkeypatch.setattr(parser, "_get_viking_fs", lambda: fs)
    monkeypatch.setattr(parser, "_process_single_file", native_parse)
    monkeypatch.setattr(UnderstandingAPI, "parse", api_parse)

    result = await parser.parse(tmp_path, _feishu_import_plan=plan, parser_backend="internal")

    api_parse.assert_not_awaited()
    native_parse.assert_awaited_once()
    assert result.meta["processed_files"] == [
        {
            "path": "Report.pdf",
            "parser": "PDFParser",
            "source_url": "https://example.feishu.cn/file/report",
            "source_token": "report",
        }
    ]
    assert result.meta["failed_files"] == []


def wiki_accessor(monkeypatch):
    accessor = FeishuAccessor()
    root = _FeishuWikiTreeNode("root", "space", "Root", "docx", "rootdoc")
    pdf = _FeishuWikiTreeNode("pdf", "space", "Report.pdf", "file", "pdffile")
    leaf = _FeishuWikiTreeNode("leaf", "space", "Leaf", "docx", "leafdoc")
    nodes = {"root": [pdf], "pdf": [leaf], "leaf": []}
    monkeypatch.setattr(accessor, "_resolve_wiki_tree_root", lambda *a, **k: root)
    monkeypatch.setattr(
        accessor, "_list_wiki_node_children", lambda space, token, **k: nodes[token]
    )
    monkeypatch.setattr(
        accessor,
        "_download_drive_file",
        lambda *a, **k: (b"%PDF-1.7", "application/pdf", "Report.pdf"),
    )
    monkeypatch.setattr(
        accessor,
        "_fetch_document",
        AsyncMock(side_effect=AssertionError("cloud docs must remain URLs")),
    )
    return accessor, nodes


@pytest.mark.asyncio
async def test_recursive_wiki_plan_keeps_body_and_pdf_children_without_placeholder_files(
    monkeypatch,
):
    accessor, _ = wiki_accessor(monkeypatch)
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/root", feishu_recursive=True, _feishu_use_understanding=True
    )
    try:
        entries = resource.feishu_plan.entries
        assert {e.path.relative_to(resource.path).as_posix(): e.kind for e in entries} == {
            "Root.md": "url",
            "Report/Report.pdf": "file",
            "Report/Leaf.md": "url",
        }
        assert not list(resource.path.rglob("*.md"))
        assert (resource.path / "Report/Report.pdf").read_bytes() == b"%PDF-1.7"
        assert next(e for e in entries if e.token == "rootdoc").url.endswith("wiki/root")
        assert resource.meta["feishu_folder_skipped_items"] == []
    finally:
        resource.cleanup()


@pytest.mark.asyncio
async def test_wiki_content_failure_does_not_stop_children_and_strict_fails(monkeypatch):
    accessor, _ = wiki_accessor(monkeypatch)
    monkeypatch.setattr(accessor, "_download_drive_file", Mock(side_effect=ValueError("denied")))
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/root", feishu_recursive=True, _feishu_use_understanding=True
    )
    try:
        assert any(e.token == "leafdoc" for e in resource.feishu_plan.entries)
        assert "denied" in resource.meta["feishu_folder_skipped_items"][0]["reason"]
    finally:
        resource.cleanup()
    with pytest.raises(ValueError, match="denied"):
        await accessor.access(
            "https://example.feishu.cn/wiki/root",
            feishu_recursive=True,
            _feishu_use_understanding=True,
            strict=True,
        )


@pytest.mark.asyncio
async def test_wiki_unknown_root_type_still_imports_children(monkeypatch):
    accessor, _ = wiki_accessor(monkeypatch)
    monkeypatch.setattr(
        accessor,
        "_resolve_wiki_tree_root",
        lambda *a, **k: _FeishuWikiTreeNode("root", "space", "Root", "unknown", "unknown"),
    )
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/root", feishu_recursive=True, _feishu_use_understanding=True
    )
    try:
        assert any(entry.token == "leafdoc" for entry in resource.feishu_plan.entries)
        assert resource.meta["feishu_folder_skipped_items"]
    finally:
        resource.cleanup()


@pytest.mark.asyncio
async def test_wiki_failed_root_enumeration_is_not_an_empty_success(monkeypatch):
    accessor, _ = wiki_accessor(monkeypatch)
    monkeypatch.setattr(
        accessor, "_list_wiki_node_children", Mock(side_effect=ValueError("list denied"))
    )
    with pytest.raises(ValueError, match="list denied"):
        await accessor.access(
            "https://example.feishu.cn/wiki/root",
            feishu_recursive=True,
            _feishu_use_understanding=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [None, "understanding"])
async def test_recursive_wiki_processor_routes_each_content_and_keeps_tree(monkeypatch, backend):
    _configure_understanding(monkeypatch, ["pdf"])
    from openviking_cli.utils.config.open_viking_config import get_openviking_config

    get_openviking_config().parser_api.enable_feishu_url = True
    accessor, _ = wiki_accessor(monkeypatch)
    resources = []

    async def access(source, **options):
        resource = await accessor.access(source, **options)
        resources.append(resource)
        return resource

    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = SimpleNamespace(access=access)
    fs = FakeVikingFS()
    monkeypatch.setattr(DirectoryParser, "_get_viking_fs", lambda self: fs)
    monkeypatch.setattr(DirectoryParser, "_create_temp_uri", lambda self: "viking://temp/tree")
    calls = []

    async def parse_api(self, source, **options):
        calls.append((source, options))
        name = Path(options.get("resource_name") or source).stem
        temporary = f"viking://temp/content_{len(calls)}"
        await fs.mkdir(f"{temporary}/{name}", exist_ok=True)
        await fs.write(f"{temporary}/{name}/0.md", "body")
        result = create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=name),
            source_path=source,
            source_format="docx" if source.startswith("https:") else "pdf",
            parser_name="UnderstandingAPI",
        )
        result.temp_dir_path = temporary
        return result

    monkeypatch.setattr(UnderstandingAPI, "parse", parse_api)
    try:
        result = await processor.process(
            "https://example.larksuite.com/wiki/root?table=t&view=v",
            feishu_recursive=True,
            feishu_access_token="secret",
            parser_backend=backend,
        )
        assert result.meta["failed_files"] == []
        assert result.meta["file_count"] == 3
        cloud_calls = [
            (source, options) for source, options in calls if source.startswith("https:")
        ]
        assert {source for source, _ in cloud_calls} == {
            "https://example.larksuite.com/wiki/root?table=t&view=v",
            "https://example.larksuite.com/wiki/leaf",
        }
        assert all(options["feishu_access_token"] == "secret" for _, options in cloud_calls)
        binary_options = next(
            options for source, options in calls if not source.startswith("https:")
        )
        assert "feishu_access_token" not in binary_options
        assert "lark_file" not in binary_options
        assert "viking://temp/tree/Root/Root/0.md" in fs.files
        assert "viking://temp/tree/Root/Report/Leaf/0.md" in fs.files
        assert "viking://temp/tree/Root/Report/Report/0.md" in fs.files
    finally:
        for resource in resources:
            resource.is_temporary = True
            resource.cleanup()


@pytest.mark.asyncio
async def test_wiki_cycle_is_reported_and_sibling_content_survives(monkeypatch):
    accessor, nodes = wiki_accessor(monkeypatch)
    nodes["leaf"] = [_FeishuWikiTreeNode("root", "space", "Root", "docx", "rootdoc")]
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/root", feishu_recursive=True, _feishu_use_understanding=True
    )
    try:
        assert len(resource.feishu_plan.entries) == 3
        assert any(
            "Cyclic" in item["reason"] for item in resource.meta["feishu_folder_skipped_items"]
        )
    finally:
        resource.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_wiki_leaf_at_depth_limit_keeps_leaf_layout(monkeypatch, strict):
    accessor, nodes = wiki_accessor(monkeypatch)
    nodes["root"] = nodes["pdf"]
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/root",
        feishu_recursive=True,
        _feishu_use_understanding=True,
        feishu_max_depth=1,
        strict=strict,
    )
    try:
        assert {
            entry.path.relative_to(resource.path).as_posix()
            for entry in resource.feishu_plan.entries
        } == {"Root.md", "Leaf.md"}
        assert not resource.meta["feishu_folder_skipped_items"]
    finally:
        resource.cleanup()


@pytest.mark.asyncio
async def test_wiki_depth_limit_reports_actual_truncation(monkeypatch):
    accessor, _ = wiki_accessor(monkeypatch)
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/root",
        feishu_recursive=True,
        _feishu_use_understanding=True,
        feishu_max_depth=1,
    )
    try:
        assert {
            entry.path.relative_to(resource.path).as_posix()
            for entry in resource.feishu_plan.entries
        } == {"Root.md", "Report/Report.pdf"}
        failures = resource.meta["feishu_folder_skipped_items"]
        assert len(failures) == 1 and "depth" in failures[0]["reason"]
    finally:
        resource.cleanup()
    with pytest.raises(ValueError, match="depth limit"):
        await accessor.access(
            "https://example.feishu.cn/wiki/root",
            feishu_recursive=True,
            _feishu_use_understanding=True,
            feishu_max_depth=1,
            strict=True,
        )


@pytest.mark.parametrize("value", ["false", "true", 1, None])
def test_recursive_wiki_flag_requires_boolean(value):
    api = UnderstandingAPI.__new__(UnderstandingAPI)
    with pytest.raises(InvalidArgumentError, match="boolean"):
        api.can_submit_url_directly(
            "https://example.feishu.cn/wiki/root",
            feishu_recursive=value,
            feishu_access_token="user-token",
        )


@pytest.mark.asyncio
async def test_recursive_wiki_preflight_keeps_unknown_root_as_directory(monkeypatch):
    accessor, _ = wiki_accessor(monkeypatch)
    monkeypatch.setattr(
        accessor,
        "_resolve_wiki_tree_root",
        lambda *a, **k: _FeishuWikiTreeNode("root", "space", "Root", "unknown", "unknown"),
    )
    monkeypatch.setattr(accessor, "_probe_document_permission", Mock(side_effect=AssertionError))
    result = await accessor.preflight_source(
        "https://example.feishu.cn/wiki/root", feishu_recursive=True
    )
    assert result.source_format == "directory"
    assert result.source_name == "Root"


@pytest.mark.asyncio
@pytest.mark.parametrize("content_kind", ["url", "file"])
async def test_recursive_leaf_wiki_keeps_routable_resource(monkeypatch, content_kind):
    accessor, nodes = wiki_accessor(monkeypatch)
    node = (
        _FeishuWikiTreeNode("leaf", "space", "Leaf", "docx", "leafdoc")
        if content_kind == "url"
        else _FeishuWikiTreeNode("leaf", "space", "Report.pdf", "file", "pdffile")
    )
    monkeypatch.setattr(accessor, "_resolve_wiki_tree_root", lambda *a, **k: node)
    resource = await accessor.access(
        "https://example.feishu.cn/wiki/leaf?table=t&view=v",
        feishu_recursive=True,
        _feishu_use_understanding=True,
    )
    try:
        assert resource.path.exists()
        assert len(resource.feishu_plan.entries) == 1
        entry = resource.feishu_plan.entries[0]
        assert entry.kind == content_kind
        if content_kind == "url":
            assert resource.path.is_dir()
            assert not entry.path.exists()
            assert entry.url.endswith("/wiki/leaf?table=t&view=v")
        else:
            assert resource.path.is_file()
            assert resource.meta["feishu_content_kind"] == "file"
    finally:
        resource.cleanup()


@pytest.mark.asyncio
async def test_recursive_wiki_rejects_direct_only_lark_auth():
    with pytest.raises(InvalidArgumentError, match="not lark_file"):
        await FeishuAccessor().access(
            "https://example.feishu.cn/wiki/root",
            feishu_recursive=True,
            lark_file={"user_access_token": "secret"},
        )
