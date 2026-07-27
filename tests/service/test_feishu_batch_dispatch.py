# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Service-level tests for Feishu wiki batch dispatch (issue #3120).

Verifies that ``ResourceService._maybe_enqueue_feishu_batch_add_resource``:
* expands a wiki space / directory URL via ``FeishuAccessor.expand_feishu_url``,
* spawns one ``add_resource`` background task per expanded child document,
* returns ``None`` for single-doc URLs (preserving the original import path).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.accessors.feishu_accessor import FeishuAccessor
from openviking.service.resource_service import (
    ResourceService,
    _is_feishu_batch_candidate,
)


# ---------------------------------------------------------------------------
# _is_feishu_batch_candidate — pure routing predicate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.feishu.cn/wiki/settings/space1", True),
        ("https://x.feishu.cn/wiki/nodeTok1", True),
        ("https://x.feishu.cn/docx/doxcnABC", False),
        ("https://x.feishu.cn/sheets/stok1", False),
        ("https://x.feishu.cn/base/appTok1", False),
        ("https://github.com/org/repo", False),
        ("https://example.com/wiki/foo", False),  # not a feishu domain
    ],
)
def test_is_feishu_batch_candidate(url, expected):
    assert _is_feishu_batch_candidate(url) is expected


# ---------------------------------------------------------------------------
# _maybe_enqueue_feishu_batch_add_resource — multi-doc dispatch.
# ---------------------------------------------------------------------------


def _make_service() -> ResourceService:
    """A ResourceService without dependencies — enough for the batch helper.

    The batch helper only uses ``self.add_resource`` (mocked per-test),
    ``self._background_tasks`` and module-level helpers; it does not touch the
    database / filesystem / processor.
    """
    return ResourceService()


async def _drain_background_tasks(service: ResourceService) -> None:
    """Await every in-flight background task so child-call assertions are stable."""
    tasks = list(service._background_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def test_batch_dispatch_spawns_one_child_per_expanded_doc():
    """A space-root URL expands to 3 docs → 3 add_resource calls + summary."""
    service = _make_service()
    captured_calls = []

    async def _fake_add_resource(path, *args, **kwargs):
        captured_calls.append({"path": path, "parent": kwargs.get("parent")})
        return {"status": "success", "root_uri": f"viking://resources/{path[-4:]}"}

    service.add_resource = _fake_add_resource  # type: ignore[assignment]

    expanded = [
        ("https://x.feishu.cn/docx/doxcn1", "Doc 1"),
        ("https://x.feishu.cn/docx/doxcn2", "Doc 2"),
        ("https://x.feishu.cn/docx/doxcn3", "Doc 3"),
    ]

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(return_value=expanded),
        ):
            result = await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                to="viking://resources/my_wiki",
                parent=None,
                parser_args={},
                kwargs={},
            )
        await _drain_background_tasks(service)
        return result

    result = asyncio.run(_runner())

    assert result is not None
    assert result["status"] == "queued_batch"
    assert result["batch_count"] == 3
    assert len(result["children"]) == 3
    assert result["children"][0] == {
        "url": "https://x.feishu.cn/docx/doxcn1",
        "title": "Doc 1",
        "index": 0,
    }
    # ``to`` was converted to the batch parent for every child.
    assert len(captured_calls) == 3
    assert {c["path"] for c in captured_calls} == {
        "https://x.feishu.cn/docx/doxcn1",
        "https://x.feishu.cn/docx/doxcn2",
        "https://x.feishu.cn/docx/doxcn3",
    }
    assert all(c["parent"] == "viking://resources/my_wiki" for c in captured_calls)


def test_batch_dispatch_returns_none_for_single_doc_url():
    """When expand_feishu_url yields a single doc, the helper returns None so the
    caller falls through to the existing single-doc import path."""
    service = _make_service()
    service.add_resource = AsyncMock()  # type: ignore[assignment]

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(
                return_value=[("https://x.feishu.cn/wiki/leaf1", "")],
            ),
        ):
            result = await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/leaf1",
                ctx=None,
                parser_args={},
                kwargs={},
            )
        await _drain_background_tasks(service)
        return result

    result = asyncio.run(_runner())
    assert result is None
    service.add_resource.assert_not_called()


def test_batch_dispatch_passes_feishu_access_token_to_expander():
    """The ``feishu_access_token`` from args/kwargs must reach expand_feishu_url
    so user-token-authorized wiki spaces can be traversed."""
    service = _make_service()
    service.add_resource = AsyncMock(return_value={"status": "success"})  # type: ignore[assignment]

    expanded = [
        ("https://x.feishu.cn/docx/d1", "D1"),
        ("https://x.feishu.cn/docx/d2", "D2"),
    ]

    async def _runner():
        mock_expand = AsyncMock(return_value=expanded)
        with patch.object(FeishuAccessor, "expand_feishu_url", new=mock_expand):
            await service._maybe_enqueue_feishu_batch_add_resource(
                path="https://x.feishu.cn/wiki/settings/space1",
                ctx=None,
                parser_args={"feishu_access_token": "u-test-token"},
                kwargs={},
            )
        await _drain_background_tasks(service)
        return mock_expand

    mock_expand = asyncio.run(_runner())
    # expand_feishu_url(path, feishu_access_token=...)
    assert mock_expand.call_count == 1
    assert mock_expand.call_args.kwargs.get("feishu_access_token") == "u-test-token"


def test_batch_dispatch_propagates_expand_failure():
    """If expand_feishu_url raises, the error must propagate (no silent
    single-doc fallback for a genuine API failure)."""
    service = _make_service()
    service.add_resource = AsyncMock()  # type: ignore[assignment]

    async def _runner():
        with patch.object(
            FeishuAccessor,
            "expand_feishu_url",
            new=AsyncMock(side_effect=RuntimeError("wiki API down")),
        ):
            with pytest.raises(RuntimeError, match="wiki API down"):
                await service._maybe_enqueue_feishu_batch_add_resource(
                    path="https://x.feishu.cn/wiki/settings/space1",
                    ctx=None,
                    parser_args={},
                    kwargs={},
                )

    asyncio.run(_runner())
    service.add_resource.assert_not_called()
