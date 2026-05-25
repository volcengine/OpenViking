# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for batched semantic file summary generation."""

import json
import re
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.utils import language as language_utils
from openviking.storage.queuefs import semantic_dag, semantic_processor
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.parser_config import SemanticConfig

pytestmark = pytest.mark.asyncio


class _FakeVLM:
    def __init__(self, *, omit_last_batch_summary: bool = False):
        self.prompts: list[str] = []
        self.omit_last_batch_summary = omit_last_batch_summary

    def is_available(self) -> bool:
        return True

    async def get_completion_async(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        ids = re.findall(r'"id":\s*"(file_\d+)"', prompt)
        if ids:
            if self.omit_last_batch_summary:
                ids = ids[:-1]
            return json.dumps(
                {
                    "summaries": [
                        {"id": file_id, "summary": f"batch summary for {file_id}"}
                        for file_id in ids
                    ]
                }
            )
        return "fallback summary"


class _FakeFS:
    def __init__(self, *, files: dict[str, str] | None = None, entries=None):
        self.files = files or {}
        self.entries = entries or {}

    async def read_file(self, file_path: str, ctx=None):
        return self.files[file_path]

    async def ls(self, uri: str, ctx=None):
        return self.entries.get(uri, [])


def _config(vlm: _FakeVLM, *, batch_size: int = 10, batch_chars: int = 10000):
    return SimpleNamespace(
        vlm=vlm,
        semantic=SemanticConfig(
            file_summary_batch_size=batch_size,
            max_file_summary_batch_chars=batch_chars,
        ),
        code=SimpleNamespace(code_summary_mode="llm"),
        output_language_override="en",
    )


async def test_generate_file_summaries_batches_text_files(monkeypatch):
    file_paths = [
        "viking://resources/root/alpha.md",
        "viking://resources/root/beta.txt",
        "viking://resources/root/gamma.rst",
    ]
    fake_vlm = _FakeVLM()
    fake_fs = _FakeFS(
        files={
            file_paths[0]: "# Alpha\nAlpha documentation.",
            file_paths[1]: "Beta plain text.",
            file_paths[2]: "Gamma reference text.",
        }
    )
    monkeypatch.setattr(semantic_processor, "get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(semantic_processor, "get_openviking_config", lambda: _config(fake_vlm))
    monkeypatch.setattr(language_utils, "get_openviking_config", lambda: _config(fake_vlm))

    processor = SemanticProcessor(max_concurrent_llm=4)

    summaries = await processor._generate_file_summaries(file_paths)

    assert len(fake_vlm.prompts) == 1
    assert all(path in summaries for path in file_paths)
    assert summaries[file_paths[0]] == {"name": "alpha.md", "summary": "batch summary for file_1"}
    assert summaries[file_paths[1]] == {"name": "beta.txt", "summary": "batch summary for file_2"}
    assert summaries[file_paths[2]] == {"name": "gamma.rst", "summary": "batch summary for file_3"}


async def test_generate_file_summaries_falls_back_for_missing_batch_result(monkeypatch):
    file_paths = [
        "viking://resources/root/alpha.md",
        "viking://resources/root/beta.md",
    ]
    fake_vlm = _FakeVLM(omit_last_batch_summary=True)
    fake_fs = _FakeFS(
        files={
            file_paths[0]: "# Alpha\nAlpha documentation.",
            file_paths[1]: "# Beta\nBeta documentation.",
        }
    )
    monkeypatch.setattr(semantic_processor, "get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(semantic_processor, "get_openviking_config", lambda: _config(fake_vlm))
    monkeypatch.setattr(language_utils, "get_openviking_config", lambda: _config(fake_vlm))

    processor = SemanticProcessor(max_concurrent_llm=4)

    summaries = await processor._generate_file_summaries(file_paths)

    assert len(fake_vlm.prompts) == 2
    assert summaries[file_paths[0]] == {"name": "alpha.md", "summary": "batch summary for file_1"}
    assert summaries[file_paths[1]] == {"name": "beta.md", "summary": "fallback summary"}


async def test_semantic_dag_groups_sibling_files_for_summary(monkeypatch):
    root_uri = "viking://resources/root"
    file_paths = [
        "viking://resources/root/alpha.md",
        "viking://resources/root/beta.txt",
    ]
    fake_fs = _FakeFS(
        entries={
            root_uri: [
                {"name": "alpha.md", "isDir": False},
                {"name": "beta.txt", "isDir": False},
            ]
        }
    )
    monkeypatch.setattr(semantic_dag, "get_viking_fs", lambda: fake_fs)

    async def _write_semantic_sidecars(**kwargs):
        return True

    monkeypatch.setattr(semantic_dag, "write_semantic_sidecars", _write_semantic_sidecars)

    class _FakeProcessor:
        def __init__(self):
            self.summary_calls: list[list[str]] = []

        async def _generate_file_summaries(self, paths, llm_sem=None, ctx=None):
            self.summary_calls.append(list(paths))
            return {
                path: {"name": path.split("/")[-1], "summary": f"summary for {path}"}
                for path in paths
            }

        async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
            return "# root\n\nOverview"

        def _extract_abstract_from_overview(self, overview):
            return "Overview"

        def _enforce_size_limits(self, overview, abstract):
            return overview, abstract

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=4,
        ctx=ctx,
        skip_vectorization=True,
    )

    await executor.run(root_uri)

    assert processor.summary_calls == [file_paths]
