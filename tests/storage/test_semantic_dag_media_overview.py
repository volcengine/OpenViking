# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import patch

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import (
    DirNode,
    SemanticDagExecutor,
)
from openviking_cli.session.user_id import UserIdentifier


def _executor():
    ctx = RequestContext(
        user=UserIdentifier("acc", "user"),
        role=Role.USER,
    )
    with patch(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        return_value=SimpleNamespace(),
    ):
        return SemanticDagExecutor(SimpleNamespace(), "resource", 2, ctx)


def _node(
    filename,
    summary,
):
    uri = "viking://resources/media"
    files = [(filename, summary)]
    file_paths = [f"{uri}/{name}" for name, _ in files]
    return DirNode(
        uri=uri,
        children_dirs=[],
        file_paths=file_paths,
        file_index={file_path: index for index, file_path in enumerate(file_paths)},
        child_index={},
        file_summaries=[{"name": name, "summary": item_summary} for name, item_summary in files],
        children_abstracts=[],
        pending=0,
    )


def test_valid_single_media_summary_is_direct_overview():
    executor = _executor()

    for filename in ("demo.mp4", "recording.mp3"):
        summary = f"# Demo\n\nDense brief.\n\n### {filename}\n\nDetailed event."
        node = _node(filename, summary)
        assert executor._select_direct_media_overview(node, node.file_summaries) == summary


def test_malformed_media_summary_is_not_direct():
    executor = _executor()
    cases = [
        _node("demo.mp4", ""),
        _node(
            "demo.mp4",
            "# Demo\n\nBrief without filename heading",
        ),
    ]

    for node in cases:
        assert executor._select_direct_media_overview(node, node.file_summaries) is None
