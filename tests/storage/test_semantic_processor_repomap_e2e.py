# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end smoke tests for code skeleton summaries."""

import asyncio
from types import SimpleNamespace

import pytest


class _FakeFS:
    def __init__(self, content: str):
        self.content = content

    async def read_file(self, file_path, ctx=None):
        return self.content


class _NoLLMVLM:
    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    async def get_completion_async(self, prompt):
        self.calls += 1
        raise AssertionError("LLM should not be called when ast skeleton is available")


class _FallbackVLM:
    def __init__(self, response: str = "fallback summary"):
        self.calls = 0
        self.prompts = []
        self.response = response

    def is_available(self):
        return True

    async def get_completion_async(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self.response


@pytest.mark.asyncio
async def test_semantic_processor_uses_aider_repomap_skeleton(monkeypatch):
    import openviking.parse.parsers.code.ast as ast_mod
    import openviking.session.memory.utils.language as language_mod
    import openviking.storage.queuefs.semantic_processor as semantic_processor_mod
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    code = "\n".join(
        [
            "export class Greeter {",
            "  hello(name: string): string {",
            "    return `hello ${name}`;",
            "  }",
            "}",
            "",
            "export function helper(value: number): number {",
            "  return value + 1;",
            "}",
            "",
            *(f"// filler {i}" for i in range(120)),
        ]
    )
    vlm = _NoLLMVLM()
    config = SimpleNamespace(
        vlm=vlm,
        code=SimpleNamespace(code_summary_mode="ast", code_skeleton_provider="aider_repomap"),
        semantic=SimpleNamespace(max_file_content_chars=2_000_000, max_skeleton_chars=2_000_000),
    )

    monkeypatch.setattr(semantic_processor_mod, "get_openviking_config", lambda: config)
    monkeypatch.setattr(semantic_processor_mod, "get_viking_fs", lambda: _FakeFS(code))
    monkeypatch.setattr(ast_mod, "_configured_skeleton_provider", lambda: "aider_repomap")
    monkeypatch.setattr(language_mod, "resolve_output_language", lambda content: "English")

    result = await SemanticProcessor()._generate_text_summary(
        file_path="viking://resources/sample.ts",
        file_name="sample.ts",
        llm_sem=asyncio.Semaphore(1),
        ctx=None,
    )

    assert result["content"] == code
    assert result["summary"].startswith("# sample.ts [aider-repomap-lite, compact]")
    assert "class Greeter" in result["summary"]
    assert "helper" in result["summary"]
    assert vlm.calls == 0


@pytest.mark.asyncio
async def test_semantic_processor_uses_query_skeleton(monkeypatch):
    import openviking.parse.parsers.code.ast as ast_mod
    import openviking.session.memory.utils.language as language_mod
    import openviking.storage.queuefs.semantic_processor as semantic_processor_mod
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    code = "\n".join(
        [
            "class Greeter:",
            "    def hello(self, name: str) -> str:",
            "        return f'hello {name}'",
            "",
            "def helper(value: int) -> int:",
            "    return value + 1",
            "",
            *(f"# filler {i}" for i in range(120)),
        ]
    )
    vlm = _NoLLMVLM()
    config = SimpleNamespace(
        vlm=vlm,
        code=SimpleNamespace(code_summary_mode="ast", code_skeleton_provider="repomap_query"),
        semantic=SimpleNamespace(max_file_content_chars=2_000_000, max_skeleton_chars=2_000_000),
    )

    monkeypatch.setattr(semantic_processor_mod, "get_openviking_config", lambda: config)
    monkeypatch.setattr(semantic_processor_mod, "get_viking_fs", lambda: _FakeFS(code))
    monkeypatch.setattr(ast_mod, "_configured_skeleton_provider", lambda: "repomap_query")
    monkeypatch.setattr(language_mod, "resolve_output_language", lambda content: "English")

    result = await SemanticProcessor()._generate_text_summary(
        file_path="viking://resources/sample.py",
        file_name="sample.py",
        llm_sem=asyncio.Semaphore(1),
        ctx=None,
    )

    assert result["content"] == code
    assert result["summary"].startswith("# sample.py [repomap-query, compact]")
    assert "class Greeter" in result["summary"]
    assert "function hello" in result["summary"]
    assert "function helper" in result["summary"]
    assert "return value + 1" not in result["summary"]
    assert vlm.calls == 0


@pytest.mark.asyncio
async def test_semantic_processor_uses_process_auto_skeleton_for_new_language(monkeypatch):
    import openviking.parse.parsers.code.ast as ast_mod
    import openviking.session.memory.utils.language as language_mod
    import openviking.storage.queuefs.semantic_processor as semantic_processor_mod
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    code = "\n".join(
        [
            "struct FraudWindowJoiner {",
            "    func join(userId: String, score: Int) -> Int {",
            "        return score + userId.count",
            "    }",
            "}",
            "",
            *(f"func helper{i}(value: Int) -> Int {{ return value + {i} }}" for i in range(120)),
        ]
    )
    vlm = _NoLLMVLM()
    config = SimpleNamespace(
        vlm=vlm,
        code=SimpleNamespace(code_summary_mode="ast", code_skeleton_provider="process"),
        semantic=SimpleNamespace(max_file_content_chars=2_000_000, max_skeleton_chars=2_000_000),
    )

    monkeypatch.setattr(semantic_processor_mod, "get_openviking_config", lambda: config)
    monkeypatch.setattr(semantic_processor_mod, "get_viking_fs", lambda: _FakeFS(code))
    monkeypatch.setattr(ast_mod, "_configured_skeleton_provider", lambda: "process")
    monkeypatch.setattr(language_mod, "resolve_output_language", lambda content: "English")

    result = await SemanticProcessor()._generate_text_summary(
        file_path="viking://resources/FraudWindowJoiner.swift",
        file_name="FraudWindowJoiner.swift",
        llm_sem=asyncio.Semaphore(1),
        ctx=None,
    )

    assert result["content"] == code
    assert result["summary"].startswith("# FraudWindowJoiner.swift [Swift]")
    assert "class FraudWindowJoiner" in result["summary"]
    assert "join" in result["summary"]
    assert "helper0" in result["summary"]
    assert vlm.calls == 0


@pytest.mark.asyncio
async def test_semantic_processor_process_provider_falls_back_for_denied_config(monkeypatch):
    import openviking.parse.parsers.code.ast as ast_mod
    import openviking.session.memory.utils.language as language_mod
    import openviking.storage.queuefs.semantic_processor as semantic_processor_mod
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    content = "\n".join(
        [
            "service:",
            "  timeout_ms: 100",
            "  retries: 3",
            *(f"  key_{i}: value_{i}" for i in range(120)),
        ]
    )
    vlm = _FallbackVLM("LLM fallback summary for config")
    config = SimpleNamespace(
        vlm=vlm,
        code=SimpleNamespace(code_summary_mode="ast", code_skeleton_provider="process"),
        semantic=SimpleNamespace(max_file_content_chars=2_000_000, max_skeleton_chars=2_000_000),
    )

    monkeypatch.setattr(semantic_processor_mod, "get_openviking_config", lambda: config)
    monkeypatch.setattr(semantic_processor_mod, "get_viking_fs", lambda: _FakeFS(content))
    monkeypatch.setattr(ast_mod, "_configured_skeleton_provider", lambda: "process")
    monkeypatch.setattr(language_mod, "resolve_output_language", lambda content: "English")

    result = await SemanticProcessor()._generate_text_summary(
        file_path="viking://resources/service.yaml",
        file_name="service.yaml",
        llm_sem=asyncio.Semaphore(1),
        ctx=None,
    )

    assert result["content"] == content
    assert result["summary"] == "LLM fallback summary for config"
    assert vlm.calls == 1
