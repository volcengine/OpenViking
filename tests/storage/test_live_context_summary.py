# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Live-context node summaries must stay recall-sized.

The summary names the mounted arguments but must not copy their values:
mounted values can carry kilobytes of code or data, which busts the recall
per-entry budget (default per_entry_cap = 320 tokens) and turns every hit
into a bare URI (#7692). File-level summaries are also length-gated now.
"""
import asyncio
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _RecordingVLM:
    def __init__(self, response: str):
        self.is_available = MagicMock(return_value=True)
        self.prompts_received: list[str] = []
        self._response = response

    async def get_completion_async(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        return self._response


def _make_live_context_file(tmp_path: Path, arguments: dict) -> Path:
    node = tmp_path / "portal.json"
    node.write_text(
        "---\n"
        "title: MSTR mNAV realtime\n"
        'description: "Strategy (MSTR) premium snapshot."\n'
        "type: OpenViking Live Context\n"
        "---\n"
        "node body\n",
        encoding="utf-8",
    )
    return node


def _live_context_metadata(arguments: dict) -> dict:
    return {
        "title": "MSTR mNAV realtime",
        "description": "Strategy (MSTR) premium snapshot.",
        "tool_uri": "viking://user/nick/tools/cf-finance/portal_codemode_execute.json",
        "arguments": arguments,
    }


@contextmanager
def _processor_env(vlm, abstract_max_chars: int):
    """Patch the module-level singletons for the duration of the call."""
    mock_config = MagicMock()
    mock_config.semantic.max_file_content_chars = 100000
    mock_config.semantic.abstract_max_chars = abstract_max_chars
    mock_config.semantic.max_skeleton_chars = 10000
    mock_config.semantic.overview_max_chars = 4000
    mock_config.output_language_override = ""

    resolver = MagicMock()
    resolver.get_vlm = AsyncMock(return_value=vlm)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=MagicMock(read_file=AsyncMock(return_value="node body")),
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=mock_config,
        ),
    ):
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        processor = SemanticProcessor(vlm_resolver=resolver)
        processor._current_ctx = MagicMock()
        yield processor


@pytest.mark.asyncio
async def test_live_context_prompt_does_not_inline_argument_values():
    """The prompt carries argument names and total size, never the values."""
    big_code = "async () => {\n  const C = { shares: 423.533e6 };\n  return C;\n}"
    vlm = _RecordingVLM("summary text")
    with tempfile.TemporaryDirectory() as tmpdir:
        node = _make_live_context_file(Path(tmpdir), {"code": big_code})
        with _processor_env(vlm, 256) as processor, patch(
            "openviking.storage.queuefs.semantic_processor.SemanticProcessor._parse_live_context_metadata",
            return_value=_live_context_metadata({"code": big_code}),
        ):
            await processor._generate_text_summary(
                file_path=node,
                file_name="portal.json",
                llm_sem=asyncio.Semaphore(1),
            )

    assert len(vlm.prompts_received) == 1
    prompt = vlm.prompts_received[0]
    assert "Mounted argument names: code" in prompt
    assert "Mounted argument size:" in prompt
    assert big_code not in prompt
    assert "async () =>" not in prompt


@pytest.mark.asyncio
async def test_live_context_summary_truncated_to_abstract_max_chars():
    """A generated summary longer than abstract_max_chars is cut at the
    semantic-processing gate, instead of entering storage and being dropped
    at injection time."""
    oversized_summary = "word " * 200  # 1000 chars, > default 256
    vlm = _RecordingVLM(oversized_summary)
    with tempfile.TemporaryDirectory() as tmpdir:
        node = _make_live_context_file(Path(tmpdir), {"code": "async () => 1"})
        with _processor_env(vlm, 256) as processor, patch(
            "openviking.storage.queuefs.semantic_processor.SemanticProcessor._parse_live_context_metadata",
            return_value=_live_context_metadata({"code": "async () => 1"}),
        ):
            result = await processor._generate_text_summary(
                file_path=node,
                file_name="portal.json",
                llm_sem=asyncio.Semaphore(1),
            )

    summary = result["summary"]
    assert len(summary) <= 256
    assert summary != oversized_summary


@pytest.mark.asyncio
async def test_short_summary_passes_through_unchanged():
    """A summary within budget is not touched by the new gate."""
    vlm = _RecordingVLM("Short recall-ready summary")
    with tempfile.TemporaryDirectory() as tmpdir:
        node = _make_live_context_file(Path(tmpdir), {"code": "async () => 1"})
        with _processor_env(vlm, 256) as processor, patch(
            "openviking.storage.queuefs.semantic_processor.SemanticProcessor._parse_live_context_metadata",
            return_value=_live_context_metadata({"code": "async () => 1"}),
        ):
            result = await processor._generate_text_summary(
                file_path=node,
                file_name="portal.json",
                llm_sem=asyncio.Semaphore(1),
            )

    assert result["summary"] == "Short recall-ready summary"
