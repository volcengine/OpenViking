# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for FeishuAccessor user token handling."""

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


class _FakeListDocumentBlockRequest:
    @staticmethod
    def builder():
        return _FakeListDocumentBlockRequestBuilder()


class _FakeListDocumentBlockRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(document_id=None, page_token=None)

    def document_id(self, document_id):
        self._request.document_id = document_id
        return self

    def page_size(self, _page_size):
        return self

    def document_revision_id(self, _revision_id):
        return self

    def page_token(self, page_token):
        self._request.page_token = page_token
        return self

    def build(self):
        return self._request


class _FakeGetNodeSpaceRequest:
    @staticmethod
    def builder():
        return _FakeGetNodeSpaceRequestBuilder()


class _FakeGetNodeSpaceRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(token=None)

    def token(self, token):
        self._request.token = token
        return self

    def build(self):
        return self._request


def _package(name):
    module = ModuleType(name)
    module.__path__ = []
    return module


def _install_fake_lark_modules(monkeypatch):
    modules = {
        "lark_oapi": _package("lark_oapi"),
        "lark_oapi.api": _package("lark_oapi.api"),
        "lark_oapi.api.docx": _package("lark_oapi.api.docx"),
        "lark_oapi.api.wiki": _package("lark_oapi.api.wiki"),
        "lark_oapi.core": _package("lark_oapi.core"),
    }
    docx_v1 = ModuleType("lark_oapi.api.docx.v1")
    docx_v1.ListDocumentBlockRequest = _FakeListDocumentBlockRequest
    wiki_v2 = ModuleType("lark_oapi.api.wiki.v2")
    wiki_v2.GetNodeSpaceRequest = _FakeGetNodeSpaceRequest
    core_model = ModuleType("lark_oapi.core.model")
    core_model.RequestOption = _FakeRequestOption
    modules.update(
        {
            "lark_oapi.api.docx.v1": docx_v1,
            "lark_oapi.api.wiki.v2": wiki_v2,
            "lark_oapi.core.model": core_model,
        }
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


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


def test_fetch_all_blocks_keeps_default_app_token_call_shape(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    list_blocks = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(items=[], has_more=False, page_token=None),
        )
    )
    accessor = FeishuAccessor()
    accessor._client = SimpleNamespace(
        docx=SimpleNamespace(v1=SimpleNamespace(document_block=SimpleNamespace(list=list_blocks)))
    )

    blocks = accessor._fetch_all_blocks("doc_token")

    assert blocks == []
    assert len(list_blocks.call_args.args) == 1
    assert list_blocks.call_args.args[0].document_id == "doc_token"


def test_resolve_wiki_node_uses_user_access_token_option(monkeypatch):
    _install_fake_lark_modules(monkeypatch)
    node = SimpleNamespace(obj_type="doc", obj_token="doc_token", title="Title")
    get_node = MagicMock(return_value=_SuccessResponse(SimpleNamespace(node=node)))
    accessor = FeishuAccessor()
    accessor._user_token_client = SimpleNamespace(
        wiki=SimpleNamespace(v2=SimpleNamespace(space=SimpleNamespace(get_node=get_node)))
    )

    doc_type, token, title = accessor._resolve_wiki_node("wiki_token", "u-test")

    assert (doc_type, token, title) == ("docx", "doc_token", "Title")
    request, option = get_node.call_args.args
    assert request.token == "wiki_token"
    assert option.user_access_token == "u-test"
