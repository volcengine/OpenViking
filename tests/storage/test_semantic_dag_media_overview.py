# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import DirNode, SemanticDagExecutor
from openviking_cli.session.user_id import UserIdentifier


def _executor():
    processor = SimpleNamespace(
        _generate_overview=AsyncMock(return_value="# generic\n\ngeneric brief"),
        _normalize_overview_generation=lambda text: (text, "abstract"),
    )
    ctx = RequestContext(user=UserIdentifier("acc", "user"), role=Role.USER)
    with patch(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        return_value=SimpleNamespace(),
    ):
        executor = SemanticDagExecutor(processor, "resource", 2, ctx)
    return executor, processor


def _node(filename, summary, *, additional_files=None, children=None):
    uri = "viking://resources/media"
    files = [(filename, summary), *(additional_files or [])]
    file_paths = [f"{uri}/{name}" for name, _ in files]
    children = children or []
    return DirNode(
        uri=uri,
        children_dirs=children,
        file_paths=file_paths,
        file_index={file_path: idx for idx, file_path in enumerate(file_paths)},
        child_index={child_uri: idx for idx, child_uri in enumerate(children)},
        file_summaries=[{"name": name, "summary": item_summary} for name, item_summary in files],
        children_abstracts=[None] * len(children),
        pending=0,
    )


def test_valid_single_video_summary_is_direct_overview():
    executor, _ = _executor()
    summary = "# Demo\n\nDense brief.\n\n### demo.mp4\n\nDetailed event."
    node = _node("demo.mp4", summary)

    assert executor._select_direct_media_overview(node, node.file_summaries) == summary


def test_valid_single_audio_summary_is_direct_overview():
    executor, _ = _executor()
    summary = "# Recording\n\nConcise audio brief.\n\n### recording.mp3\n\nDetailed event."
    node = _node("recording.mp3", summary)

    assert executor._select_direct_media_overview(node, node.file_summaries) == summary


@pytest.mark.parametrize(
    ("filename", "summary"),
    [
        ("demo.mp4", ""),
        ("demo.mp4", "Audio summary generation not yet implemented"),
        ("demo.mp4", "# Demo\n\nBrief without filename heading"),
        ("image.png", "# Image\n\nBrief\n\n### image.png\n\nDetail"),
    ],
)
def test_empty_malformed_or_non_media_summary_is_not_direct(filename, summary):
    executor, _ = _executor()
    node = _node(filename, summary)

    assert executor._select_direct_media_overview(node, node.file_summaries) is None


@pytest.mark.parametrize(
    "summary",
    [
        "# Demo\n\n### demo.mp4",
        "# Demo\n\n### demo.mp4\n\nDetailed event.\n\nDense brief.",
        "# Demo\n\n## Section\n\nDense brief.\n\n### demo.mp4\n\nDetailed event.",
    ],
    ids=["h1-and-h3-only", "filename-heading-before-brief", "heading-instead-of-brief"],
)
def test_media_summary_requires_brief_before_filename_heading(summary):
    executor, _ = _executor()
    node = _node("demo.mp4", summary)

    assert executor._select_direct_media_overview(node, node.file_summaries) is None


@pytest.mark.parametrize(
    ("additional_files", "children"),
    [
        ([("notes.txt", "A text file")], None),
        ([("other.mp4", "Another media summary")], None),
        (None, ["viking://resources/media/child"]),
    ],
    ids=["mixed-files", "multiple-media-files", "child-directory"],
)
def test_multiple_files_or_children_are_not_direct(additional_files, children):
    executor, _ = _executor()
    summary = "# Demo\n\nDense brief.\n\n### demo.mp4\n\nDetailed event."
    node = _node("demo.mp4", summary, additional_files=additional_files, children=children)

    assert executor._select_direct_media_overview(node, node.file_summaries) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["demo.mp4", "demo.mp3"])
async def test_overview_task_skips_generic_vlm_for_valid_single_media(filename):
    executor, processor = _executor()
    summary = f"# Demo\n\nDense brief.\n\n### {filename}\n\nDetailed event."
    node = _node(filename, summary)
    executor._nodes[node.uri] = node
    executor._parent[node.uri] = None
    executor._root_done = asyncio.Event()
    executor._write_directory_semantics = AsyncMock(return_value=True)
    executor._add_vectorize_task = AsyncMock()

    await executor._overview_task(node.uri)

    processor._generate_overview.assert_not_awaited()
    executor._write_directory_semantics.assert_awaited_once_with(
        node.uri, summary, "abstract"
    )


@pytest.mark.asyncio
async def test_empty_single_media_summary_uses_generic_vlm():
    executor, processor = _executor()
    node = _node("demo.mp4", "")
    executor._nodes[node.uri] = node
    executor._parent[node.uri] = None
    executor._root_done = asyncio.Event()
    executor._write_directory_semantics = AsyncMock(return_value=True)
    executor._add_vectorize_task = AsyncMock()

    await executor._overview_task(node.uri)

    processor._generate_overview.assert_awaited_once()
    file_summaries = processor._generate_overview.await_args.args[1]
    assert file_summaries == [{"name": "demo.mp4", "summary": ""}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("summary", "additional_files", "children"),
    [
        ("# Demo\n\n### demo.mp4", None, None),
        ("# Demo\n\n### demo.mp4\n\nDetailed event.\n\nDense brief.", None, None),
        (
            "# Demo\n\nDense brief.\n\n### demo.mp4\n\nDetailed event.",
            [("notes.txt", "A text file")],
            None,
        ),
        (
            "# Demo\n\nDense brief.\n\n### demo.mp4\n\nDetailed event.",
            None,
            ["viking://resources/media/child"],
        ),
    ],
    ids=["h1-and-h3-only", "filename-heading-before-brief", "mixed-files", "child-directory"],
)
async def test_overview_task_uses_generic_vlm_for_direct_reuse_fallbacks(
    summary, additional_files, children
):
    executor, processor = _executor()
    node = _node(
        "demo.mp4",
        summary,
        additional_files=additional_files,
        children=children,
    )
    executor._nodes[node.uri] = node
    executor._parent[node.uri] = None
    executor._root_done = asyncio.Event()
    executor._write_directory_semantics = AsyncMock(return_value=True)
    executor._add_vectorize_task = AsyncMock()

    await executor._overview_task(node.uri)

    processor._generate_overview.assert_awaited_once()
