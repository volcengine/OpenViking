# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu wiki space / directory batch import (issue #3120).

Covers ``FeishuAccessor.classify_url``, ``expand_feishu_url`` and the recursive
``list_wiki_subtree`` walker. All Feishu API calls are mocked — the lark-oapi
SDK is replaced with fakes that mimic ``client.wiki.v2.space.list`` and
``client.wiki.v2.space.get_node``.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openviking.parse.accessors.feishu_accessor import FeishuAccessor


# ---------------------------------------------------------------------------
# Mock plumbing — mirrors the pattern in tests/parse/test_feishu_accessor.py.
# ---------------------------------------------------------------------------


class _SuccessResponse:
    """Stand-in for a successful lark-oapi response object."""

    def __init__(self, data):
        self.data = data
        self.code = 0
        self.msg = ""

    @staticmethod
    def success():
        return True


class _FailureResponse:
    """Stand-in for a failed lark-oapi response object."""

    def __init__(self, code=1, msg="boom"):
        self.data = None
        self.code = code
        self.msg = msg

    @staticmethod
    def success():
        return False


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


class _FakeListSpaceNodeRequest:
    """Fake ``lark_oapi.api.wiki.v2.ListSpaceNodeRequest``."""

    @staticmethod
    def builder():
        return _FakeListSpaceNodeRequestBuilder()


class _FakeListSpaceNodeRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(
            space_id=None, parent_node_token=None, page_token=None, page_size=None
        )

    def space_id(self, value):
        self._request.space_id = value
        return self

    def parent_node_token(self, value):
        self._request.parent_node_token = value
        return self

    def page_token(self, value):
        self._request.page_token = value
        return self

    def page_size(self, value):
        self._request.page_size = value
        return self

    def build(self):
        return self._request


class _FakeGetNodeSpaceRequest:
    """Fake ``lark_oapi.api.wiki.v2.GetNodeSpaceRequest``."""

    @staticmethod
    def builder():
        return _FakeGetNodeSpaceRequestBuilder()


class _FakeGetNodeSpaceRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(token=None)

    def token(self, value):
        self._request.token = value
        return self

    def build(self):
        return self._request


def _install_fake_lark_wiki(monkeypatch):
    """Install just enough of lark-oapi for the wiki batch code paths."""
    lark = ModuleType("lark_oapi")
    core_model = ModuleType("lark_oapi.core.model")
    core_model.RequestOption = _FakeRequestOption
    wiki_v2 = ModuleType("lark_oapi.api.wiki.v2")
    wiki_v2.ListSpaceNodeRequest = _FakeListSpaceNodeRequest
    wiki_v2.GetNodeSpaceRequest = _FakeGetNodeSpaceRequest

    monkeypatch.setitem(sys.modules, "lark_oapi", lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.core.model", core_model)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.wiki", ModuleType("lark_oapi.api.wiki"))
    monkeypatch.setitem(sys.modules, "lark_oapi.api.wiki.v2", wiki_v2)


def _make_node(
    *,
    node_token,
    obj_type,
    obj_token,
    title="",
    has_child=False,
    space_id="space_1",
    parent_node_token=None,
    url="",
):
    return SimpleNamespace(
        node_token=node_token,
        obj_token=obj_token,
        obj_type=obj_type,
        title=title,
        has_child=has_child,
        space_id=space_id,
        parent_node_token=parent_node_token,
        url=url,
    )


def _make_client(space_list=None, get_node=None):
    """Build a fake lark client exposing ``client.wiki.v2.space.{list,get_node}``."""
    space = SimpleNamespace(list=space_list or MagicMock(), get_node=get_node or MagicMock())
    return SimpleNamespace(wiki=SimpleNamespace(v2=SimpleNamespace(space=space)))


# ---------------------------------------------------------------------------
# classify_url / is_batch_url (pure logic, no mocks).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        # Single-doc URL forms — unchanged behaviour.
        ("https://volcengine.feishu.cn/docx/doxcnABC", ("single_doc", "docx", "doxcnABC")),
        ("https://volcengine.feishu.cn/sheets/stok1", ("single_doc", "sheets", "stok1")),
        ("https://volcengine.feishu.cn/base/appTok1", ("single_doc", "base", "appTok1")),
        # Wiki node URL — may be a doc or a directory.
        (
            "https://volcengine.feishu.cn/wiki/nodeTok1",
            ("wiki_node", "nodeTok1", None),
        ),
        # Space root URL — always batch.
        (
            "https://volcengine.feishu.cn/wiki/settings/7123456789012345",
            ("wiki_space_root", "7123456789012345", None),
        ),
        # Lark domains also recognised.
        (
            "https://volcengine.larksuite.com/wiki/settings/abc",
            ("wiki_space_root", "abc", None),
        ),
        (
            "https://volcengine.larkoffice.com/wiki/nodeX",
            ("wiki_node", "nodeX", None),
        ),
    ],
)
def test_classify_url_recognises_known_forms(url, expected):
    assert FeishuAccessor.classify_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo",
        "https://example.com/docx/foo",  # not a feishu domain
        "https://volcengine.feishu.cn/",  # no path
        "https://volcengine.feishu.cn/wiki/",  # wiki with no token
        "https://volcengine.feishu.cn/wiki/settings/",  # settings with no space id
        "/local/path/docx/foo",  # not a URL
    ],
)
def test_classify_url_rejects_unrecognized(url):
    assert FeishuAccessor.classify_url(url) is None


def test_is_batch_url_only_true_for_space_root():
    assert FeishuAccessor.is_batch_url(
        "https://x.feishu.cn/wiki/settings/space1"
    ) is True
    # Wiki node URLs need an API check, so is_batch_url returns False here.
    assert FeishuAccessor.is_batch_url("https://x.feishu.cn/wiki/node1") is False
    assert FeishuAccessor.is_batch_url("https://x.feishu.cn/docx/d1") is False
    assert FeishuAccessor.is_batch_url("https://github.com/o/r") is False


# ---------------------------------------------------------------------------
# _build_doc_url — direct URLs only for parseable types; None otherwise.
# ---------------------------------------------------------------------------


def test_build_doc_url_emits_direct_url_for_known_types():
    node = _make_node(node_token="n1", obj_type="docx", obj_token="doxcnA", title="A")
    assert (
        FeishuAccessor._build_doc_url(node)
        == "https://feishu.cn/docx/doxcnA"
    )


def test_build_doc_url_normalizes_short_api_type_names():
    # Wiki API returns short names: doc/sheet/bitable — _WIKI_TYPE_MAP normalizes.
    doc = _make_node(node_token="n1", obj_type="doc", obj_token="t1")
    sheet = _make_node(node_token="n2", obj_type="sheet", obj_token="t2")
    base = _make_node(node_token="n3", obj_type="bitable", obj_token="t3")
    assert FeishuAccessor._build_doc_url(doc) == "https://feishu.cn/docx/t1"
    assert FeishuAccessor._build_doc_url(sheet) == "https://feishu.cn/sheets/t2"
    assert FeishuAccessor._build_doc_url(base) == "https://feishu.cn/base/t3"


def test_build_doc_url_returns_none_for_unsupported_types():
    # Wiki nodes of unknown obj_type (e.g. mindmap/file) are skipped — they
    # would otherwise produce URLs the accessor cannot parse.
    node = _make_node(node_token="n1", obj_type="mindmap", obj_token="t1")
    assert FeishuAccessor._build_doc_url(node) is None


def test_build_doc_url_returns_none_when_obj_token_missing():
    node = _make_node(node_token="n1", obj_type="docx", obj_token="")
    assert FeishuAccessor._build_doc_url(node) is None


# ---------------------------------------------------------------------------
# expand_feishu_url — single-doc passthrough (no API calls for /docx/, /sheets/).
# ---------------------------------------------------------------------------


def test_expand_single_docx_url_returns_unchanged(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    accessor = FeishuAccessor()
    # No client is wired up — the test fails if any API call is attempted.
    result = asyncio.run(
        accessor.expand_feishu_url("https://x.feishu.cn/docx/doxcnABC")
    )
    assert result == [("https://x.feishu.cn/docx/doxcnABC", "")]


def test_expand_single_sheets_url_returns_unchanged(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    accessor = FeishuAccessor()
    result = asyncio.run(
        accessor.expand_feishu_url("https://x.feishu.cn/sheets/stok1")
    )
    assert result == [("https://x.feishu.cn/sheets/stok1", "")]


# ---------------------------------------------------------------------------
# expand_feishu_url — wiki node URL (uses get_node to check has_child).
# ---------------------------------------------------------------------------


def test_expand_wiki_node_without_children_returns_single_url(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    get_node = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                node=_make_node(
                    node_token="leaf1",
                    obj_type="docx",
                    obj_token="doxcnLeaf",
                    title="Leaf",
                    has_child=False,
                )
            )
        )
    )
    accessor = FeishuAccessor()
    accessor._user_token_client = _make_client(get_node=get_node)

    result = asyncio.run(
        accessor.expand_feishu_url(
            "https://x.feishu.cn/wiki/leaf1", feishu_access_token="u-test"
        )
    )
    assert result == [("https://x.feishu.cn/wiki/leaf1", "")]
    # Verify get_node was called with the right token + user-token option.
    request, option = get_node.call_args.args
    assert request.token == "leaf1"
    assert option.user_access_token == "u-test"


def test_expand_wiki_node_with_children_returns_subtree(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    # Root node has children; its subtree contains two docs.
    get_node = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                node=_make_node(
                    node_token="dir1",
                    obj_type="docx",
                    obj_token="doxcnDir",
                    title="Directory Root",
                    has_child=True,
                    space_id="space_1",
                )
            )
        )
    )
    # space.list returns the directory's two children (both leaves).
    space_list = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                items=[
                    _make_node(
                        node_token="c1",
                        obj_type="docx",
                        obj_token="doxcnC1",
                        title="Child 1",
                    ),
                    _make_node(
                        node_token="c2",
                        obj_type="doc",
                        obj_token="doxcnC2",
                        title="Child 2",
                    ),
                ],
                has_more=False,
                page_token=None,
            )
        )
    )
    accessor = FeishuAccessor()
    accessor._user_token_client = _make_client(space_list=space_list, get_node=get_node)

    result = asyncio.run(
        accessor.expand_feishu_url(
            "https://x.feishu.cn/wiki/dir1", feishu_access_token="u-test"
        )
    )
    # Three docs: the directory root itself + two children.
    urls = [url for url, _title in result]
    assert urls == [
        "https://feishu.cn/docx/doxcnDir",
        "https://feishu.cn/docx/doxcnC1",
        "https://feishu.cn/docx/doxcnC2",
    ]
    titles = [title for _url, title in result]
    assert titles == ["Directory Root", "Child 1", "Child 2"]
    # space.list was called with the directory's node_token as parent.
    list_request = space_list.call_args.args[0]
    assert list_request.space_id == "space_1"
    assert list_request.parent_node_token == "dir1"


# ---------------------------------------------------------------------------
# expand_feishu_url — space root URL (uses space.list, no get_node).
# ---------------------------------------------------------------------------


def test_expand_space_root_url_returns_all_nodes(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    space_list = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(
                items=[
                    _make_node(
                        node_token="t1",
                        obj_type="docx",
                        obj_token="doxcnT1",
                        title="Top Doc 1",
                    ),
                    _make_node(
                        node_token="t2",
                        obj_type="doc",
                        obj_token="doxcnT2",
                        title="Top Doc 2",
                    ),
                ],
                has_more=False,
                page_token=None,
            )
        )
    )
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(
        accessor.expand_feishu_url(
            "https://x.feishu.cn/wiki/settings/space_42"
        )
    )
    urls = [url for url, _title in result]
    assert urls == [
        "https://feishu.cn/docx/doxcnT1",
        "https://feishu.cn/docx/doxcnT2",
    ]
    list_request = space_list.call_args.args[0]
    assert list_request.space_id == "space_42"
    # Root listing: parent_node_token is None.
    assert list_request.parent_node_token is None


def test_expand_space_root_url_recurses_into_subdirectories(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    # First call: top-level listing — one doc + one folder.
    # Second call: the folder's children — two docs.
    top_level = _SuccessResponse(
        SimpleNamespace(
            items=[
                _make_node(
                    node_token="top_doc",
                    obj_type="docx",
                    obj_token="doxcnTop",
                    title="Top",
                ),
                _make_node(
                    node_token="folder1",
                    obj_type="docx",
                    obj_token="doxcnFolder",
                    title="Folder",
                    has_child=True,
                ),
            ],
            has_more=False,
            page_token=None,
        )
    )
    folder_children = _SuccessResponse(
        SimpleNamespace(
            items=[
                _make_node(
                    node_token="f_child1",
                    obj_type="doc",
                    obj_token="doxcnFC1",
                    title="FC1",
                    parent_node_token="folder1",
                ),
                _make_node(
                    node_token="f_child2",
                    obj_type="sheet",
                    obj_token="shtFC2",
                    title="FC2",
                    parent_node_token="folder1",
                ),
            ],
            has_more=False,
            page_token=None,
        )
    )
    space_list = MagicMock(side_effect=[top_level, folder_children])
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(
        accessor.expand_feishu_url("https://x.feishu.cn/wiki/settings/space_1")
    )
    urls = [url for url, _title in result]
    # Top-level doc + folder doc + 2 folder children.
    assert urls == [
        "https://feishu.cn/docx/doxcnTop",
        "https://feishu.cn/docx/doxcnFolder",
        "https://feishu.cn/docx/doxcnFC1",
        "https://feishu.cn/sheets/shtFC2",
    ]
    # Two space.list calls: one at root, one with parent_node_token=folder1.
    assert space_list.call_count == 2
    second_request = space_list.call_args_list[1].args[0]
    assert second_request.parent_node_token == "folder1"


# ---------------------------------------------------------------------------
# Pagination.
# ---------------------------------------------------------------------------


def test_expand_space_root_url_follows_pagination(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    page1 = _SuccessResponse(
        SimpleNamespace(
            items=[
                _make_node(
                    node_token="p1a",
                    obj_type="docx",
                    obj_token="doxcnP1A",
                    title="P1A",
                ),
            ],
            has_more=True,
            page_token="page2token",
        )
    )
    page2 = _SuccessResponse(
        SimpleNamespace(
            items=[
                _make_node(
                    node_token="p2a",
                    obj_type="docx",
                    obj_token="doxcnP2A",
                    title="P2A",
                ),
            ],
            has_more=False,
            page_token=None,
        )
    )
    space_list = MagicMock(side_effect=[page1, page2])
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(
        accessor.expand_feishu_url("https://x.feishu.cn/wiki/settings/space_1")
    )
    urls = [url for url, _title in result]
    assert urls == ["https://feishu.cn/docx/doxcnP1A", "https://feishu.cn/docx/doxcnP2A"]
    # The second list call must carry the page_token from page 1.
    second_request = space_list.call_args_list[1].args[0]
    assert second_request.page_token == "page2token"


# ---------------------------------------------------------------------------
# Recursion / node-count caps.
# ---------------------------------------------------------------------------


def test_list_wiki_subtree_respects_max_depth(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    # A chain of nested folders: root → folderA → folderB → doc.
    def _list(space_id, parent_node_token, **_):
        if parent_node_token is None:
            return _SuccessResponse(
                SimpleNamespace(
                    items=[
                        _make_node(
                            node_token="folderA",
                            obj_type="docx",
                            obj_token="doxcnA",
                            title="A",
                            has_child=True,
                        ),
                    ],
                    has_more=False,
                    page_token=None,
                )
            )
        if parent_node_token == "folderA":
            return _SuccessResponse(
                SimpleNamespace(
                    items=[
                        _make_node(
                            node_token="folderB",
                            obj_type="docx",
                            obj_token="doxcnB",
                            title="B",
                            has_child=True,
                            parent_node_token="folderA",
                        ),
                    ],
                    has_more=False,
                    page_token=None,
                )
            )
        if parent_node_token == "folderB":
            return _SuccessResponse(
                SimpleNamespace(
                    items=[
                        _make_node(
                            node_token="leafZ",
                            obj_type="docx",
                            obj_token="doxcnZ",
                            title="Z",
                            parent_node_token="folderB",
                        ),
                    ],
                    has_more=False,
                    page_token=None,
                )
            )
        return _SuccessResponse(SimpleNamespace(items=[], has_more=False, page_token=None))

    space_list = MagicMock(side_effect=lambda req, *a, **kw: _list(
        req.space_id, req.parent_node_token
    ))
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    # max_depth=1 → only the root level (folderA is recorded, but its children
    # are NOT walked because depth would exceed 1).
    result = asyncio.run(
        accessor.list_wiki_subtree(space_id="space_1", max_depth=1)
    )
    urls = [url for url, _title in result]
    assert urls == ["https://feishu.cn/docx/doxcnA"]


def test_list_wiki_subtree_respects_max_nodes(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    # 5 top-level docs; max_nodes caps the result to 2.
    items = [
        _make_node(
            node_token=f"n{i}",
            obj_type="docx",
            obj_token=f"doxcn{i}",
            title=f"doc{i}",
        )
        for i in range(5)
    ]
    space_list = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(items=items, has_more=False, page_token=None)
        )
    )
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(
        accessor.list_wiki_subtree(space_id="space_1", max_nodes=2)
    )
    assert len(result) == 2
    assert [url for url, _ in result] == [
        "https://feishu.cn/docx/doxcn0",
        "https://feishu.cn/docx/doxcn1",
    ]


def test_list_wiki_subtree_empty_space_returns_empty_list(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    space_list = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(items=[], has_more=False, page_token=None)
        )
    )
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(accessor.list_wiki_subtree(space_id="space_1"))
    assert result == []


def test_list_wiki_subtree_dedupes_shared_obj_tokens(monkeypatch):
    """If the same underlying doc (obj_token) appears under two parents, it
    must only be emitted once."""
    _install_fake_lark_wiki(monkeypatch)
    shared = _make_node(
        node_token="shared_node",
        obj_type="docx",
        obj_token="doxcnShared",
        title="Shared",
    )
    space_list = MagicMock(
        return_value=_SuccessResponse(
            SimpleNamespace(items=[shared, shared], has_more=False, page_token=None)
        )
    )
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(accessor.list_wiki_subtree(space_id="space_1"))
    assert result == [("https://feishu.cn/docx/doxcnShared", "Shared")]


def test_list_wiki_subtree_skips_unsupported_obj_type_but_recurses(monkeypatch):
    """A node with an unsupported obj_type (e.g. a folder of type 'wiki' or
    'file') is not emitted, but its children are still walked."""
    _install_fake_lark_wiki(monkeypatch)
    folder = _make_node(
        node_token="folder_unknown",
        obj_type="file",  # not in _DOC_TYPE_HANDLERS
        obj_token="fileTok",
        title="Folder",
        has_child=True,
    )
    child = _make_node(
        node_token="child_doc",
        obj_type="docx",
        obj_token="doxcnChild",
        title="Child",
        parent_node_token="folder_unknown",
    )
    space_list = MagicMock(
        side_effect=[
            _SuccessResponse(
                SimpleNamespace(items=[folder], has_more=False, page_token=None)
            ),
            _SuccessResponse(
                SimpleNamespace(items=[child], has_more=False, page_token=None)
            ),
        ]
    )
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    result = asyncio.run(accessor.list_wiki_subtree(space_id="space_1"))
    # Only the supported child doc is emitted.
    assert result == [("https://feishu.cn/docx/doxcnChild", "Child")]


# ---------------------------------------------------------------------------
# expand_feishu_url — non-Feishu URLs pass through unchanged.
# ---------------------------------------------------------------------------


def test_expand_non_feishu_url_returns_unchanged(monkeypatch):
    _install_fake_lark_wiki(monkeypatch)
    accessor = FeishuAccessor()
    result = asyncio.run(
        accessor.expand_feishu_url("https://github.com/org/repo")
    )
    assert result == [("https://github.com/org/repo", "")]


def test_expand_space_root_api_failure_propagates(monkeypatch):
    """If the Feishu API call fails, the error must surface (no silent
    degradation to single-doc import)."""
    _install_fake_lark_wiki(monkeypatch)
    space_list = MagicMock(return_value=_FailureResponse(code=99991663, msg="denied"))
    accessor = FeishuAccessor()
    accessor._client = _make_client(space_list=space_list)

    with pytest.raises(Exception):  # OpenVikingError raised by _raise_from_lark_response
        asyncio.run(
            accessor.expand_feishu_url("https://x.feishu.cn/wiki/settings/space_1")
        )
