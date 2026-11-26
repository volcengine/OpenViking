# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import Mock, call

import pytest

from openviking.eval.ragas.pipeline import RAGQueryPipeline


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/volcengine/OpenViking.git",
        "git@github.com:volcengine/OpenViking.git",
        "ssh://git@github.com/volcengine/OpenViking.git",
        "git://example.org/team/repo.git",
    ],
)
def test_add_code_repos_forwards_git_url(url):
    pipeline = RAGQueryPipeline()
    pipeline._client = Mock()
    pipeline._client.add_resource.return_value = {"root_uri": "viking://resources/repo"}

    assert pipeline.add_code_repos([url], wait=False) == ["viking://resources/repo"]
    pipeline._client.add_resource.assert_called_once_with(url, wait=False)


def test_add_code_repos_preserves_input_order_and_skips_missing_paths(tmp_path):
    url = "https://github.com/volcengine/OpenViking.git"
    pipeline = RAGQueryPipeline()
    pipeline._client = Mock()
    pipeline._client.add_resource.side_effect = [
        {"root_uri": "viking://resources/local"},
        {"root_uri": "viking://resources/remote"},
    ]

    roots = pipeline.add_code_repos([tmp_path, tmp_path / "missing", url])

    assert roots == ["viking://resources/local", "viking://resources/remote"]
    assert pipeline._client.add_resource.call_args_list == [
        call(str(tmp_path), wait=True),
        call(url, wait=True),
    ]


def test_add_documents_keeps_local_path_handling(tmp_path):
    pipeline = RAGQueryPipeline()
    pipeline._client = Mock()
    pipeline._client.add_resource.return_value = {"root_uri": "viking://resources/local"}

    roots = pipeline.add_documents(
        [str(tmp_path), tmp_path / "missing", "https://github.com/volcengine/OpenViking.git"]
    )

    assert roots == ["viking://resources/local"]
    pipeline._client.add_resource.assert_called_once_with(str(tmp_path), wait=True)
