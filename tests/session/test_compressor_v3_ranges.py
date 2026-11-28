# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Range indices must survive image normalization and the write-request boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message
from openviking.message.part import ImagePart, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.compressor_v3 import SessionCompressorV3
from openviking.session.memory.dataclass import MemoryFile, ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_type_registry import get_default_registry
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.streaming_memory_updater import clone_memory_update_request
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture(autouse=True)
def local_config(tmp_path):
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "enable_watch_scheduler": False,
            "storage": {"workspace": str(tmp_path)},
            "embedding": {
                "dense": {"provider": "openai", "model": "test", "api_key": "test", "dimension": 8}
            },
        }
    )
    yield
    OpenVikingConfigSingleton.reset_instance()


@pytest.mark.asyncio
@pytest.mark.parametrize("caption_fails", [False, True])
@pytest.mark.parametrize("images", [0, 1, 2])
async def test_ranges_match_after_real_compressor_submit(monkeypatch, caption_fails, images):
    raw = [
        Message(id="m0", role="user", parts=[TextPart("TASK: inspect the item.")]),
        Message(
            id="m1",
            role="user",
            parts=[
                TextPart("\n".join(f"M1_UNIT_{i:03d}: " + "x" * 110 for i in range(20))),
                *[ImagePart(url=f"https://example.invalid/{i}.png") for i in range(images)],
            ],
        ),
        Message(id="m2", role="assistant", parts=[TextPart("M2_UNRELATED: another subject.")]),
    ]
    original_parts = [list(m.parts) for m in raw]
    original_texts = [[p.text for p in m.parts if isinstance(p, TextPart)] for m in raw]
    expected_context = None
    received = []
    caption_calls = []
    registry = get_default_registry()

    class FakeVision:
        def is_available(self):
            return True

        async def get_vision_completion_async(self, **kwargs):
            caption_calls.append(kwargs)
            if caption_fails:
                raise RuntimeError("Synthetic caption failure")
            return "VISUAL_FACT: the selected item is blue."

    class Orchestrator:
        async def run(self):
            return ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_type="events",
                        uris=["viking://user/u/memories/events/test.md"],
                        memory_fields={
                            "event_name": "test",
                            "goal": "inspect",
                            "summary": "Summary",
                            "ranges": "2",
                        },
                    )
                ],
                delete_file_contents=[],
                errors=[],
            ), []

    class FakeUpdater:
        async def submit(self, request):
            received.append(request)
            # The append-only split clones requests before the writer uses them.
            cloned = clone_memory_update_request(request, operations=request.operations)
            writer = ExtractContext(cloned.messages)
            assert len(writer.messages) == len(expected_context.messages)
            assert [(m.id, m.role, m.peer_id) for m in writer.messages] == [
                (m.id, m.role, m.peer_id) for m in expected_context.messages
            ]
            for ranges in ("2", "5-6", "0-5", "0-2,5-6", f"0-{len(writer.messages) - 1}"):
                expected = expected_context.read_message_ranges(ranges).pretty_print()
                actual = writer.read_message_ranges(ranges).pretty_print()
                assert expected == actual
                rendered = MemoryFileUtils.write(
                    MemoryFile(extra_fields={"ranges": ranges, "summary": "Summary"}),
                    content_template=registry.get("events").content_template,
                    extract_context=writer,
                )
                assert expected in rendered
            tail = writer.read_message_ranges(str(len(writer.messages) - 1)).pretty_print()
            assert "M2_UNRELATED" in tail
            assert "M2_UNRELATED" not in writer.read_message_ranges("0-5").pretty_print()
            return SimpleNamespace(operations=request.operations, apply_result=MemoryUpdateResult())

    def orchestrator(**kwargs):
        nonlocal expected_context
        expected_context = kwargs["context_provider"].get_extract_context()
        return Orchestrator()

    compressor = SessionCompressorV3(
        vikingdb=None,
        rollout_analyzer=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=FakeVision())),
    )
    compressor._get_or_create_react = orchestrator
    monkeypatch.setattr("openviking.session.compressor_v3.get_viking_fs", lambda: SimpleNamespace())
    monkeypatch.setattr("openviking.session.compressor_v3.ensure_agent_file", AsyncMock())
    monkeypatch.setattr(
        "openviking.session.memory.account_templates.resolve_account_memory_registry",
        AsyncMock(return_value=registry),
    )
    monkeypatch.setattr(registry, "initialize_memory_files", AsyncMock())
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_memory_updater",
        AsyncMock(return_value=FakeUpdater()),
    )
    await compressor._extract_user_memories(
        messages=raw,
        ctx=RequestContext(user=UserIdentifier.the_default_user("u"), role=Role.ROOT),
        allowed_memory_types={"events"},
        strict_extract_errors=True,
    )
    assert len(received) == 1
    assert bool(caption_calls) == bool(images)
    assert [m.parts for m in raw] == original_parts
    assert [[p.text for p in m.parts if isinstance(p, TextPart)] for m in raw] == original_texts
    if images and not caption_fails:
        assert "[Image description]" in received[0].messages[1].parts[-1].text
    assert all(isinstance(p, TextPart) for m in received[0].messages for p in m.parts)
