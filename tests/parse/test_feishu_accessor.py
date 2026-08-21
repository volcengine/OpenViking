# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for FeishuAccessor supported types, user token, and image handling."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openviking.parse.accessors.feishu_accessor import (
    _MAX_MEDIA_DOWNLOAD_CONTEXTS,
    FeishuAccessor,
)


class _SuccessResponse:
    def __init__(self, data):
        self.data = data
        self.code = 0
        self.msg = ""

    @staticmethod
    def success():
        return True


class _FakeRequestOption:
    def __init__(self):
        self.user_access_token = None

    @staticmethod
    def builder():
        return _FakeRequestOptionBuilder()


class _FakeRequestOptionBuilder:
    def __init__(self):
        self._option = _FakeRequestOption()

    def user_access_token(self, token):
        self._option.user_access_token = token
        return self

    def build(self):
        return self._option


class _FakeBaseRequest:
    @staticmethod
    def builder():
        return _FakeBaseRequestBuilder()


class _FakeBaseRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(http_method=None, uri=None, token_types=None)
        self._request.queries = {}
        self._request.add_query = self._request.queries.__setitem__

    def http_method(self, method):
        self._request.http_method = method
        return self

    def uri(self, uri):
        self._request.uri = uri
        return self

    def token_types(self, token_types):
        self._request.token_types = token_types
        return self

    def build(self):
        return self._request


class _FakeRawResponse:
    def __init__(self, content=b"image-bytes", status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}


class _FakeMediaResponse:
    def __init__(
        self,
        content=b"image-bytes",
        success=True,
        code=0,
        msg="",
        headers=None,
        status_code=200,
    ):
        self.raw = _FakeRawResponse(content, status_code=status_code, headers=headers)
        self.code = code
        self.msg = msg
        self._success = success

    def success(self):
        return self._success


class _FakeListDocumentBlockRequest:
    @staticmethod
    def builder():
        return _FakeListDocumentBlockRequestBuilder()


class _FakeListDocumentBlockRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(document_id=None)

    def document_id(self, document_id):
        self._request.document_id = document_id
        return self

    def page_size(self, _page_size):
        return self

    def document_revision_id(self, _revision_id):
        return self

    def build(self):
        return self._request


class _FakeTypedRequest:
    @staticmethod
    def builder():
        return _FakeTypedRequestBuilder()


class _FakeTypedRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace()

    def __getattr__(self, name):
        def _set(value):
            setattr(self._request, name, value)
            return self

        return _set

    def build(self):
        return self._request


def _install_fake_lark_modules(monkeypatch):
    lark = ModuleType("lark_oapi")
    lark.BaseRequest = _FakeBaseRequest
    lark.HttpMethod = SimpleNamespace(GET="GET")
    lark.AccessTokenType = SimpleNamespace(TENANT="tenant", USER="user")
    docx_v1 = ModuleType("lark_oapi.api.docx.v1")
    docx_v1.ListDocumentBlockRequest = _FakeListDocumentBlockRequest
    wiki_v2 = ModuleType("lark_oapi.api.wiki.v2")
    wiki_v2.GetNodeSpaceRequest = _FakeTypedRequest
    bitable_v1 = ModuleType("lark_oapi.api.bitable.v1")
    bitable_v1.ListAppTableRequest = _FakeTypedRequest
    bitable_v1.ListAppTableFieldRequest = _FakeTypedRequest
    bitable_v1.ListAppTableRecordRequest = _FakeTypedRequest
    core_model = ModuleType("lark_oapi.core.model")
    core_model.RequestOption = _FakeRequestOption
    monkeypatch.setitem(sys.modules, "lark_oapi", lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.docx.v1", docx_v1)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.wiki.v2", wiki_v2)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.bitable.v1", bitable_v1)
    monkeypatch.setitem(sys.modules, "lark_oapi.core.model", core_model)


def test_feishu_api_lists_paginated_content_with_user_token(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    list_blocks = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(items=[], has_more=False, page_token=None),
        )
    )
    list_drive = MagicMock(
        side_effect=[
            _SuccessResponse(
                SimpleNamespace(
                    files=[SimpleNamespace(token="doc_token")],
                    has_more=True,
                    next_page_token="page-2",
                )
            ),
            _SuccessResponse(
                SimpleNamespace(
                    files=[SimpleNamespace(token="file_token")],
                    has_more=False,
                    next_page_token=None,
                )
            ),
        ]
    )
    accessor = FeishuAccessor()
    accessor._user_token_client = SimpleNamespace(
        docx=SimpleNamespace(v1=SimpleNamespace(document_block=SimpleNamespace(list=list_blocks))),
        request=list_drive,
    )

    blocks = accessor._fetch_all_blocks("doc_token", feishu_access_token="u-test")
    children = accessor._list_drive_folder_children(
        "folder_token",
        feishu_access_token="u-test",
    )

    assert blocks == []
    assert [child.token for child in children] == ["doc_token", "file_token"]
    request, option = list_blocks.call_args.args
    assert request.document_id == "doc_token"
    assert option.user_access_token == "u-test"
    assert list_drive.call_args_list[1].args[0].queries["page_token"] == "page-2"
    assert all(call.args[1].user_access_token == "u-test" for call in list_drive.call_args_list)


def test_resolve_image_refs_respects_download_images_disabled():
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=False)
    markdown = "![screenshot](feishu://image/img_token_123)"

    updated, images = accessor._resolve_image_refs(markdown)

    assert updated == markdown
    assert images == {}


def test_resolve_image_refs_downloads_media_and_rewrites_markdown(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(return_value=_FakeMediaResponse(b"\x89PNG\r\n"))
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)

    updated, images = accessor._resolve_image_refs(
        "before ![screenshot](feishu://image/img_token_123) after"
    )

    assert updated == "before ![screenshot](images/img_token_123.png) after"
    assert images == {"images/img_token_123.png": b"\x89PNG\r\n"}
    request = request_media.call_args.args[0]
    assert request.http_method == "GET"
    assert request.uri == "/open-apis/drive/v1/medias/img_token_123/download"


def test_resolve_image_refs_uses_content_type_extension(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(
        return_value=_FakeMediaResponse(
            b"\xff\xd8\xff\xe0jpeg-bytes",
            headers={"Content-Type": "image/jpeg"},
        )
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)

    updated, images = accessor._resolve_image_refs("![j](feishu://image/img_token_jpeg)")

    assert updated == "![j](images/img_token_jpeg.jpg)"
    assert images == {"images/img_token_jpeg.jpg": b"\xff\xd8\xff\xe0jpeg-bytes"}


def test_resolve_image_refs_falls_back_to_byte_magic_extension(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    # No usable Content-Type header; extension must come from WebP byte magic.
    webp_bytes = b"RIFF\x00\x00\x00\x00WEBPfake"
    request_media = MagicMock(return_value=_FakeMediaResponse(webp_bytes, headers={}))
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)

    updated, images = accessor._resolve_image_refs("![w](feishu://image/img_token_webp)")

    assert updated == "![w](images/img_token_webp.webp)"
    assert images == {"images/img_token_webp.webp": webp_bytes}


def test_resolve_image_refs_tries_distinct_permission_contexts(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(
        side_effect=[
            _FakeMediaResponse(success=False, code=400, status_code=400),
            _FakeMediaResponse(b"\x89PNG\r\n"),
        ]
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)

    updated, images = accessor._resolve_image_refs(
        "![s](feishu://image/shared-token)",
        media_download_extras={"shared-token": ["context-one", "context-two", None]},
    )

    assert updated == "![s](images/shared-token.png)"
    assert images == {"images/shared-token.png": b"\x89PNG\r\n"}
    requests = [call.args[0] for call in request_media.call_args_list]
    assert [request.queries["extra"] for request in requests] == [
        "context-one",
        "context-two",
    ]


@pytest.mark.parametrize(
    ("feishu_access_token", "token_type"),
    [(None, "tenant"), ("u-test", "user")],
)
def test_resolve_image_refs_falls_back_to_token_only(
    monkeypatch,
    feishu_access_token,
    token_type,
):
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(
        side_effect=[
            _FakeMediaResponse(success=False, code=400, status_code=400),
            _FakeMediaResponse(b"\x89PNG\r\n"),
        ]
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)
    accessor._user_token_client = SimpleNamespace(request=request_media)

    updated, images = accessor._resolve_image_refs(
        "![s](feishu://image/shared-token)",
        feishu_access_token=feishu_access_token,
        media_download_extras={"shared-token": ["permission-context"]},
    )

    assert updated == "![s](images/shared-token.png)"
    assert images == {"images/shared-token.png": b"\x89PNG\r\n"}
    requests = [call.args[0] for call in request_media.call_args_list]
    assert [request.queries for request in requests] == [
        {"extra": "permission-context"},
        {},
    ]
    assert all(request.token_types == {token_type} for request in requests)
    if feishu_access_token:
        assert all(
            call.args[1].user_access_token == feishu_access_token
            for call in request_media.call_args_list
        )
    else:
        assert all(len(call.args) == 1 for call in request_media.call_args_list)


def test_resolve_image_refs_bounds_permission_context_attempts(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(
        return_value=_FakeMediaResponse(success=False, code=400, status_code=400)
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)
    contexts = [f"context-{index}" for index in range(_MAX_MEDIA_DOWNLOAD_CONTEXTS + 2)]

    updated, images = accessor._resolve_image_refs(
        "![s](feishu://image/shared-token)",
        media_download_extras={"shared-token": contexts},
    )

    assert updated == "![s](feishu://image/shared-token)"
    assert images == {}
    requests = [call.args[0] for call in request_media.call_args_list]
    assert [request.queries.get("extra") for request in requests] == [
        *contexts[:_MAX_MEDIA_DOWNLOAD_CONTEXTS],
        None,
    ]


def test_download_image_uses_tenant_token_without_user_token(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(return_value=_FakeMediaResponse(b"\x89PNG\r\n"))
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._client = SimpleNamespace(request=request_media)

    accessor._download_image("img_token_123")

    request = request_media.call_args.args[0]
    assert request.token_types == {"tenant"}


def test_download_image_advertises_user_token_when_provided(monkeypatch):
    """With a user access token the media request must advertise USER, or
    lark-oapi never injects it and the download silently fails."""
    _install_fake_lark_modules(monkeypatch)
    request_media = MagicMock(return_value=_FakeMediaResponse(b"\x89PNG\r\n"))
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)
    accessor._user_token_client = SimpleNamespace(request=request_media)

    accessor._download_image("img_token_123", feishu_access_token="u-test")

    args = request_media.call_args.args
    request = args[0]
    assert request.token_types == {"user"}
    # The user access token option must also be forwarded on the call.
    assert len(args) == 2
    assert args[1].user_access_token == "u-test"


def test_access_downloads_drive_file_with_user_token(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request = MagicMock(
        return_value=_FakeMediaResponse(
            b"%PDF-1.7",
            headers={"content-type": "application/pdf"},
        )
    )
    accessor = FeishuAccessor()
    accessor._user_token_client = SimpleNamespace(request=request)
    url = "https://bytedance.larkoffice.com/file/file_token"

    assert accessor.can_handle(url)
    resource = asyncio.run(accessor.access(url, feishu_access_token="u-test"))
    try:
        assert resource.path.read_bytes() == b"%PDF-1.7"
        assert resource.path.name == "file_token.pdf"
        assert resource.meta["feishu_doc_type"] == "file"
        assert resource.meta["feishu_token"] == "file_token"
        raw_request, option = request.call_args.args
        assert raw_request.uri == "/open-apis/drive/v1/files/file_token/download"
        assert option.user_access_token == "u-test"
    finally:
        resource.cleanup()


def test_access_materializes_drive_folder_contract(monkeypatch):
    from openviking.parse.accessors.feishu_accessor import FeishuDocument

    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=False)
    long_name = "文" * 100
    children = {
        "root_folder": [
            SimpleNamespace(token="doc_one", name=long_name, type="docx", url=""),
            SimpleNamespace(token="doc_legacy", name="Legacy", type="doc", url=""),
            SimpleNamespace(token="doc_two", name=long_name, type="docx", url=""),
            SimpleNamespace(token="nested", name="Nested", type="folder", url=""),
            SimpleNamespace(token="design", name="Design.pdf", type="file", url=""),
            SimpleNamespace(token="blocked", name="Blocked.pptx", type="file", url=""),
        ],
        "nested": [SimpleNamespace(token="sheet", name="Metrics", type="sheet", url="")],
    }

    async def fake_fetch_document(url, **_kwargs):
        doc_type, token = accessor._parse_feishu_url(url)
        if token == "doc_legacy":
            assert doc_type == "doc"
        return FeishuDocument(
            doc_type=doc_type,
            token=token,
            markdown_content=f"# {token}",
            title=token,
            meta={},
        )

    def fake_download(file_token, **_kwargs):
        if file_token == "blocked":
            raise RuntimeError("HTTP 403")
        return b"%PDF-1.7", "application/pdf", "Design.pdf"

    monkeypatch.setattr(
        accessor, "_get_drive_folder_name", lambda *_args, **_kwargs: "Product Docs"
    )
    monkeypatch.setattr(
        accessor,
        "_list_drive_folder_children",
        lambda folder_token, **_kwargs: children[folder_token],
    )
    monkeypatch.setattr(accessor, "_fetch_document", fake_fetch_document)
    monkeypatch.setattr(accessor, "_download_drive_file", fake_download)
    url = "https://bytedance.larkoffice.com/drive/folder/root_folder"

    assert accessor.can_handle(url)
    resource = asyncio.run(accessor.access(url, feishu_access_token="u-test"))
    try:
        markdown_files = sorted(resource.path.glob("*.md"))
        assert {path.read_text(encoding="utf-8") for path in markdown_files} == {
            "# doc_one",
            "# doc_legacy",
            "# doc_two",
        }
        assert all(len(path.name.encode("utf-8")) <= 240 for path in markdown_files)
        assert any(" (2).md" in path.name for path in markdown_files)
        assert (resource.path / "Nested" / "Metrics.md").read_text(encoding="utf-8") == "# sheet"
        assert (resource.path / "Design.pdf").read_bytes() == b"%PDF-1.7"
        assert resource.meta["original_filename"] == "Product Docs"
        assert resource.meta["feishu_doc_type"] == "folder"
        skipped = resource.meta["feishu_folder_skipped_items"]
        assert [(item["name"], item["token"], item["reason"]) for item in skipped] == [
            ("Blocked.pptx", "blocked", "HTTP 403")
        ]
    finally:
        resource.cleanup()


def test_access_offloads_synchronous_download_to_thread(monkeypatch):
    """access() must not run the synchronous _resolve_image_refs on the event loop."""
    import threading

    _install_fake_lark_modules(monkeypatch)
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)

    async def fake_fetch_document(*_args, **_kwargs):
        from openviking.parse.accessors.feishu_accessor import FeishuDocument

        return FeishuDocument(
            doc_type="docx",
            token="doc_token",
            markdown_content="![s](feishu://image/img_token_123)",
            title="Test Doc",
            meta={},
            media_download_extras={"img_token_123": ["permission-context"]},
        )

    monkeypatch.setattr(accessor, "_fetch_document", fake_fetch_document)

    main_thread = threading.get_ident()
    ran_on = {}

    def fake_resolve(markdown, **_):
        ran_on["thread"] = threading.get_ident()
        ran_on["extras"] = _["media_download_extras"]
        return (
            "![s](images/img_token_123.png)",
            {"images/img_token_123.png": b"\x89PNG\r\n"},
        )

    monkeypatch.setattr(accessor, "_resolve_image_refs", fake_resolve)

    resource = asyncio.run(accessor.access("https://example.feishu.cn/docx/doc_token"))
    try:
        assert "thread" in ran_on, "_resolve_image_refs was never called"
        assert ran_on["thread"] != main_thread, (
            "_resolve_image_refs ran on the event-loop thread; "
            "it must be offloaded via asyncio.to_thread"
        )
        assert ran_on["extras"] == {"img_token_123": ["permission-context"]}
        assert "permission-context" not in str(resource.meta)
    finally:
        resource.cleanup()


def test_access_writes_downloaded_images_next_to_markdown(monkeypatch):
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=True)

    async def fake_fetch_document(*_args, **_kwargs):
        from openviking.parse.accessors.feishu_accessor import FeishuDocument

        return FeishuDocument(
            doc_type="docx",
            token="doc_token",
            markdown_content="![screenshot](feishu://image/img_token_123)",
            title="Test Doc",
            meta={},
        )

    monkeypatch.setattr(accessor, "_fetch_document", fake_fetch_document)
    monkeypatch.setattr(
        accessor,
        "_resolve_image_refs",
        lambda markdown, **_: (
            "![screenshot](images/img_token_123.png)",
            {"images/img_token_123.png": b"\x89PNG\r\n"},
        ),
    )

    resource = asyncio.run(accessor.access("https://example.feishu.cn/docx/doc_token"))

    try:
        assert resource.path.name == "document.md"
        assert resource.path.read_text(encoding="utf-8") == (
            "![screenshot](images/img_token_123.png)"
        )
        image_path = resource.path.parent / "images" / "img_token_123.png"
        assert image_path.read_bytes() == b"\x89PNG\r\n"
        assert resource.meta["original_filename"] == "Test Doc"
    finally:
        resource.cleanup()

    assert not resource.path.parent.exists()


def test_fetch_document_dispatches_all_supported_types(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    accessor = FeishuAccessor()
    handlers = {
        "_parse_docx": MagicMock(return_value=("docx body", "Doc")),
        "_parse_sheets": MagicMock(return_value=("sheet body", "Sheet")),
        "_parse_bitable": MagicMock(return_value=("base body", "Base")),
    }
    for name, handler in handlers.items():
        monkeypatch.setattr(accessor, name, handler)

    def raw_doc_response(data):
        return _FakeMediaResponse(content=json.dumps({"data": data}))

    legacy_responses = {
        "/open-apis/doc/v2/meta/doccn_token": raw_doc_response({"title": "Legacy Doc"}),
        "/open-apis/doc/v2/doccn_token/raw_content": raw_doc_response({"content": "doc body"}),
    }
    raw_request = MagicMock(side_effect=lambda request, _option: legacy_responses[request.uri])
    get_wiki_node = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                node=SimpleNamespace(
                    obj_type="doc",
                    obj_token="doccn_from_wiki",
                    title="Wiki Legacy",
                )
            )
        )
    )
    accessor._user_token_client = SimpleNamespace(
        request=raw_request,
        wiki=SimpleNamespace(v2=SimpleNamespace(space=SimpleNamespace(get_node=get_wiki_node))),
    )

    assert accessor._resolve_wiki_node("wiki_token", "u-test") == (
        "doc",
        "doccn_from_wiki",
        "Wiki Legacy",
    )

    assert accessor.can_handle("https://example.feishu.cn/doc/doccn_token")
    assert accessor.can_handle("https://example.feishu.cn/docs/doccn_token")
    assert accessor._parse_feishu_url("https://example.feishu.cn/docs/doccn_token") == (
        "doc",
        "doccn_token",
    )

    legacy_doc = asyncio.run(
        accessor._fetch_document(
            "https://example.feishu.cn/docs/doccn_token",
            feishu_access_token="u-test",
        )
    )
    docx = asyncio.run(
        accessor._fetch_document(
            "https://example.feishu.cn/docx/doc_token",
            feishu_access_token="u-test",
        )
    )
    sheets = asyncio.run(accessor._fetch_document("https://example.feishu.cn/sheets/sht_token"))
    base = asyncio.run(accessor._fetch_document("https://example.feishu.cn/base/app_token"))
    monkeypatch.setattr(
        accessor,
        "_resolve_wiki_node",
        MagicMock(return_value=("base", "wiki_app_token", "Wiki Base")),
    )
    wiki = asyncio.run(accessor._fetch_document("https://example.feishu.cn/wiki/wiki_token"))

    assert (
        legacy_doc.doc_type,
        docx.doc_type,
        sheets.doc_type,
        base.doc_type,
        wiki.doc_type,
    ) == (
        "doc",
        "docx",
        "sheets",
        "base",
        "base",
    )
    assert legacy_doc.title == "Legacy Doc"
    assert legacy_doc.markdown_content == "# Legacy Doc\n\ndoc body"
    assert wiki.title == "Wiki Base"
    handlers["_parse_docx"].assert_called_once_with("doc_token", "u-test")
    handlers["_parse_sheets"].assert_called_once_with(
        "sht_token",
        None,
        media_download_extras=sheets.media_download_extras,
    )
    assert handlers["_parse_bitable"].call_args_list[-1].args == ("wiki_app_token", None)

    monkeypatch.setattr(accessor, "_probe_docx_document", MagicMock())
    monkeypatch.setattr(
        accessor,
        "_fetch_legacy_doc_metadata",
        MagicMock(return_value={"title": "Legacy/Title"}),
    )
    monkeypatch.setattr(
        accessor,
        "_fetch_spreadsheet_metadata",
        MagicMock(return_value={"properties": {"title": "Sheet/Title"}, "sheets": []}),
    )
    monkeypatch.setattr(
        accessor,
        "_list_bitable_tables",
        MagicMock(
            return_value=[
                SimpleNamespace(table_id="tbl_one", name="One"),
                SimpleNamespace(table_id="tbl_two", name="Two"),
            ]
        ),
    )
    monkeypatch.setattr(accessor, "_probe_bitable_table", MagicMock())
    monkeypatch.setattr(accessor, "_get_drive_folder_name", MagicMock(return_value="Docs/Root"))
    monkeypatch.setattr(accessor, "_probe_drive_folder_children", MagicMock())
    monkeypatch.setattr(
        accessor,
        "_resolve_wiki_node",
        MagicMock(return_value=("base", "wiki_app_token", "Wiki Base")),
    )

    legacy_identity = asyncio.run(accessor.preflight_source("https://example.feishu.cn/doc/doccn"))
    docx_identity = asyncio.run(accessor.preflight_source("https://example.feishu.cn/docx/doc"))
    sheets_identity = asyncio.run(
        accessor.preflight_source("https://example.feishu.cn/sheets/sht")
    )
    base_identity = asyncio.run(accessor.preflight_source("https://example.feishu.cn/base/app"))
    table_identity = asyncio.run(
        accessor.preflight_source("https://example.feishu.cn/base/app?table=tbl_one")
    )
    folder_identity = asyncio.run(
        accessor.preflight_source("https://example.feishu.cn/drive/folder/fld")
    )
    wiki_identity = asyncio.run(accessor.preflight_source("https://example.feishu.cn/wiki/wiki"))

    assert legacy_identity.doc_type == "doc"
    assert legacy_identity.source_name == "Legacy_Title"
    assert docx_identity.source_name is None
    assert sheets_identity.source_name == "Sheet_Title"
    assert base_identity.source_name == "Bitable (2 tables)"
    assert table_identity.source_name == "tbl_one"
    assert folder_identity.source_name == "Docs_Root"
    assert wiki_identity.source_name == "Wiki Base"


def test_fetch_document_honors_bitable_table_and_view(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    list_tables = MagicMock()
    list_fields = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                items=[SimpleNamespace(field_name="Status")],
                has_more=False,
                page_token=None,
            )
        )
    )
    list_records = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                items=[SimpleNamespace(fields={"Status": "Published"})],
                has_more=False,
                page_token=None,
            )
        )
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(max_records_per_table=10)
    accessor._client = SimpleNamespace(
        bitable=SimpleNamespace(
            v1=SimpleNamespace(
                app_table=SimpleNamespace(list=list_tables),
                app_table_field=SimpleNamespace(list=list_fields),
                app_table_record=SimpleNamespace(list=list_records),
            )
        )
    )

    document = asyncio.run(
        accessor._fetch_document(
            "https://example.feishu.cn/base/app_token?table=tblSales&view=vewPublic"
        )
    )

    list_tables.assert_not_called()
    request = list_records.call_args.args[0]
    assert (request.table_id, request.view_id) == ("tblSales", "vewPublic")
    assert document.title == "tblSales (vewPublic)"
    assert document.meta["feishu_table_id"] == "tblSales"
    assert document.meta["feishu_view_id"] == "vewPublic"

    list_fields.reset_mock()
    list_records.reset_mock()
    identity = asyncio.run(
        accessor.preflight_source(
            "https://example.feishu.cn/base/app_token?table=tblSales&view=vewPublic"
        )
    )

    assert identity.source_name == "tblSales (vewPublic)"
    assert list_tables.call_count == 0
    assert list_fields.call_count == 1
    assert list_records.call_count == 1
    request = list_records.call_args.args[0]
    assert (request.table_id, request.view_id) == ("tblSales", "vewPublic")


def test_fetch_document_rejects_bitable_view_without_table():
    accessor = FeishuAccessor()

    with pytest.raises(ValueError, match="'view'.*'table'"):
        asyncio.run(
            accessor._fetch_document("https://example.feishu.cn/base/app_token?view=vewPublic")
        )


def test_parse_sheets_handles_grid_and_embedded_bitable(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    request = MagicMock(
        side_effect=[
            _FakeMediaResponse(
                b'{"data":{"properties":{"title":"Budget"},"sheets":['
                b'{"sheetId":"sheet-1","title":"Q1","rowCount":3,"columnCount":28},'
                b'{"sheetId":"block-1","title":"Content Calendar","rowCount":0,'
                b'"columnCount":0,"blockInfo":{"blockType":"BITABLE_BLOCK",'
                b'"blockToken":"app-token_table-1"}}]}}'
            ),
            _FakeMediaResponse(b'{"data":{"valueRange":{"values":[["name","amount"],["A",1]]}}}'),
            _FakeMediaResponse(b"\x89PNG\r\n"),
        ]
    )
    list_tables = MagicMock()
    list_fields = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                items=[
                    SimpleNamespace(field_name="Topic", field_id="fld-topic"),
                    SimpleNamespace(field_name="Cover", field_id="fld-cover"),
                    SimpleNamespace(field_name="Brief", field_id="fld-brief"),
                ],
                has_more=False,
                page_token=None,
            )
        )
    )
    list_records = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                items=[
                    SimpleNamespace(
                        record_id="rec-1",
                        fields={
                            "Topic": "Welcome",
                            "Cover": [
                                {
                                    "file_token": "cover-token",
                                    "name": "cover.png",
                                    "type": "image/png",
                                }
                            ],
                            "Brief": [
                                {
                                    "file_token": "brief-token",
                                    "name": "brief.pdf",
                                    "type": "application/pdf",
                                }
                            ],
                        },
                    )
                ],
                has_more=False,
                page_token=None,
            )
        )
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(
        max_rows_per_sheet=2,
        max_records_per_table=10,
        download_images=True,
    )
    accessor._user_token_client = SimpleNamespace(
        request=request,
        bitable=SimpleNamespace(
            v1=SimpleNamespace(
                app_table=SimpleNamespace(list=list_tables),
                app_table_field=SimpleNamespace(list=list_fields),
                app_table_record=SimpleNamespace(list=list_records),
            )
        ),
    )

    media_download_extras = {}
    markdown, title = accessor._parse_sheets(
        "sht_token",
        "u-test",
        media_download_extras=media_download_extras,
    )

    assert title == "Budget"
    assert "| name | amount |" in markdown
    assert "1 more rows truncated" in markdown
    assert "2 columns after Z omitted" in markdown
    assert "### Content Calendar" in markdown
    assert "Welcome" in markdown
    assert "![cover.png](feishu://image/cover-token)" in markdown
    assert "brief.pdf" in markdown
    assert "feishu://image/brief-token" not in markdown
    assert "Empty sheet" not in markdown
    assert media_download_extras == {
        "cover-token": [
            json.dumps(
                {
                    "bitablePerm": {
                        "tableId": "table-1",
                        "attachments": {"fld-cover": {"rec-1": ["cover-token"]}},
                    }
                },
                separators=(",", ":"),
            )
        ]
    }
    assert list_tables.call_count == 0
    assert list_fields.call_args.args[0].table_id == "table-1"

    resolved, images = accessor._resolve_image_refs(
        markdown,
        feishu_access_token="u-test",
        media_download_extras=media_download_extras,
    )

    assert "![cover.png](images/cover-token.png)" in resolved
    assert images == {"images/cover-token.png": b"\x89PNG\r\n"}
    assert request.call_args_list[-1].args[0].uri == (
        "/open-apis/drive/v1/medias/cover-token/download"
    )
    assert json.loads(request.call_args_list[-1].args[0].queries["extra"]) == {
        "bitablePerm": {
            "tableId": "table-1",
            "attachments": {"fld-cover": {"rec-1": ["cover-token"]}},
        }
    }
    assert all(call.args[0].token_types == {"user"} for call in request.call_args_list)
    assert all(call.args[1].user_access_token == "u-test" for call in request.call_args_list)


def test_bitable_media_context_falls_back_without_sdk_ids():
    extras = {}

    FeishuAccessor._collect_bitable_media_extras(
        {"file_token": "cover-token", "name": "cover.png", "type": "image/png"},
        table_id="table-1",
        field_id=None,
        record_id="rec-1",
        media_download_extras=extras,
    )

    assert extras == {"cover-token": [None]}


def test_bitable_media_context_collection_is_bounded():
    extras = {}

    for index in range(_MAX_MEDIA_DOWNLOAD_CONTEXTS + 2):
        FeishuAccessor._collect_bitable_media_extras(
            {"file_token": "cover-token", "name": "cover.png", "type": "image/png"},
            table_id="table-1",
            field_id="field-1",
            record_id=f"record-{index}",
            media_download_extras=extras,
        )

    assert len(extras["cover-token"]) == _MAX_MEDIA_DOWNLOAD_CONTEXTS


def test_parse_bitable_uses_user_token_and_formats_records(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    list_tables = MagicMock(
        side_effect=[
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(table_id="table-1", name="Leads")],
                    has_more=True,
                    page_token="tables-2",
                )
            ),
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(table_id="table-2", name="Companies")],
                    has_more=False,
                    page_token=None,
                )
            ),
        ]
    )
    list_fields = MagicMock(
        side_effect=[
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(field_name="Owner")],
                    has_more=True,
                    page_token="fields-2",
                )
            ),
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(field_name="Status")],
                    has_more=False,
                    page_token=None,
                )
            ),
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(field_name="Name")],
                    has_more=False,
                    page_token=None,
                )
            ),
        ]
    )
    list_records = MagicMock(
        side_effect=[
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(fields={"Owner": [{"name": "Alice"}], "Status": "New"})],
                    has_more=False,
                    page_token=None,
                )
            ),
            _SuccessResponse(
                SimpleNamespace(
                    items=[SimpleNamespace(fields={"Name": "Acme"})],
                    has_more=False,
                    page_token=None,
                )
            ),
        ]
    )
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(max_records_per_table=10)
    accessor._user_token_client = SimpleNamespace(
        bitable=SimpleNamespace(
            v1=SimpleNamespace(
                app_table=SimpleNamespace(list=list_tables),
                app_table_field=SimpleNamespace(list=list_fields),
                app_table_record=SimpleNamespace(list=list_records),
            )
        )
    )

    markdown, title = accessor._parse_bitable("app_token", "u-test")

    assert title == "Bitable (2 tables)"
    assert "## Leads" in markdown
    assert "## Companies" in markdown
    assert "Alice" in markdown
    assert "Acme" in markdown
    assert "records truncated" not in markdown
    assert list_tables.call_args_list[1].args[0].page_token == "tables-2"
    assert list_fields.call_args_list[1].args[0].page_token == "fields-2"
    assert all(call.args[1].user_access_token == "u-test" for call in list_records.call_args_list)


def test_embedded_sheet_uses_same_user_token(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    inspect_block = MagicMock(
        return_value=_FakeMediaResponse(
            b'{"data":{"block":{"sheet":{"token":"spreadsheet-1_sheet-1"}}}}'
        )
    )
    accessor = FeishuAccessor()
    accessor._user_token_client = SimpleNamespace(request=inspect_block)
    read_range = MagicMock(return_value=[["name", "amount"], ["A", "1"]])
    monkeypatch.setattr(accessor, "_read_sheet_range", read_range)
    block = SimpleNamespace(
        block_id="block-1",
        block_type=30,
        parent_id="doc-1",
        sheet=SimpleNamespace(),
    )

    markdown = accessor._block_to_markdown(
        block,
        {},
        {},
        document_id="doc-1",
        feishu_access_token="u-test",
    )

    assert "| name | amount |" in markdown
    assert inspect_block.call_args.args[0].token_types == {"user"}
    assert inspect_block.call_args.args[1].user_access_token == "u-test"
    assert read_range.call_args.kwargs["feishu_access_token"] == "u-test"


def test_access_keeps_raw_title_but_exposes_safe_original_filename(monkeypatch):
    accessor = FeishuAccessor()
    accessor._config = SimpleNamespace(download_images=False)

    async def fake_fetch_document(*_args, **_kwargs):
        from openviking.parse.accessors.feishu_accessor import FeishuDocument

        return FeishuDocument(
            doc_type="docx",
            token="doc_token",
            markdown_content="# API Docs/Overview",
            title="API Docs/Overview",
            meta={},
        )

    monkeypatch.setattr(accessor, "_fetch_document", fake_fetch_document)

    resource = asyncio.run(accessor.access("https://example.feishu.cn/docx/doc_token"))
    try:
        assert resource.meta["feishu_title"] == "API Docs/Overview"
        assert resource.meta["original_filename"] == "API Docs_Overview"
    finally:
        resource.cleanup()
