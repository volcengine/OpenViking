# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for FeishuAccessor user token and image handling."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from openviking.parse.accessors.feishu_accessor import FeishuAccessor


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
    def __init__(self, content=b"image-bytes", success=True, code=0, msg="", headers=None):
        self.raw = _FakeRawResponse(content, headers=headers)
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


def _install_fake_lark_modules(monkeypatch):
    lark = ModuleType("lark_oapi")
    lark.BaseRequest = _FakeBaseRequest
    lark.HttpMethod = SimpleNamespace(GET="GET")
    lark.AccessTokenType = SimpleNamespace(TENANT="tenant", USER="user")
    docx_v1 = ModuleType("lark_oapi.api.docx.v1")
    docx_v1.ListDocumentBlockRequest = _FakeListDocumentBlockRequest
    core_model = ModuleType("lark_oapi.core.model")
    core_model.RequestOption = _FakeRequestOption
    monkeypatch.setitem(sys.modules, "lark_oapi", lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.docx.v1", docx_v1)
    monkeypatch.setitem(sys.modules, "lark_oapi.core.model", core_model)


def test_fetch_all_blocks_uses_user_access_token_option(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    list_blocks = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(items=[], has_more=False, page_token=None),
        )
    )
    accessor = FeishuAccessor()
    accessor._user_token_client = SimpleNamespace(
        docx=SimpleNamespace(v1=SimpleNamespace(document_block=SimpleNamespace(list=list_blocks)))
    )

    blocks = accessor._fetch_all_blocks("doc_token", feishu_access_token="u-test")

    assert blocks == []
    request, option = list_blocks.call_args.args
    assert request.document_id == "doc_token"
    assert option.user_access_token == "u-test"


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

    updated, images = accessor._resolve_image_refs(
        "![j](feishu://image/img_token_jpeg)"
    )

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

    updated, images = accessor._resolve_image_refs(
        "![w](feishu://image/img_token_webp)"
    )

    assert updated == "![w](images/img_token_webp.webp)"
    assert images == {"images/img_token_webp.webp": webp_bytes}


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


def test_guess_image_ext_defaults_to_png_when_unknown():
    accessor = FeishuAccessor()
    assert accessor._guess_image_ext(b"not-an-image", None) == ".png"
    assert accessor._guess_image_ext(b"\xff\xd8\xff", None) == ".jpg"
    assert accessor._guess_image_ext(b"anything", "image/gif") == ".gif"


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
        )

    monkeypatch.setattr(accessor, "_fetch_document", fake_fetch_document)

    main_thread = threading.get_ident()
    ran_on = {}

    def fake_resolve(markdown, **_):
        ran_on["thread"] = threading.get_ident()
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
