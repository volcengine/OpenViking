# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.message import Message, ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.session.tool_output_externalizer import ToolOutputExternalizer
from openviking.session.tool_result_store import sha256_text


def _config() -> ToolOutputExternalizationConfig:
    return ToolOutputExternalizationConfig(
        threshold_chars=100,
        preview_chars=200,
        assistant_turn_inline_budget_chars=100,
        assistant_turn_preview_budget_chars=100,
        min_preview_chars=50,
    )


async def test_second_externalize_group_pass_keeps_source_read_metadata():
    session_uri = "viking://user/alice/sessions/s1"
    source_ref = f"{session_uri}/tool-results/tr_source_1"
    raw = "0123456789" * 2_000
    part = ToolPart(
        tool_id="call_read_1",
        tool_name="openviking_tool_result_read",
        tool_input={"tool_output_ref": source_ref},
        tool_output=raw,
        tool_status="completed",
    )
    message = Message(id="msg_1", role="assistant", parts=[part])
    externalizer = ToolOutputExternalizer(object(), session_uri, "s1", ctx=None)

    await externalizer.externalize_group([message], _config())
    first_preview = part.tool_output
    first_original_chars = part.tool_output_original_chars
    first_sha256 = part.tool_output_sha256

    assert part.tool_output_source_ref == source_ref
    assert part.tool_output_truncated is True
    assert first_original_chars == len(raw)
    assert first_sha256 == sha256_text(raw)

    await externalizer.externalize_group([message], _config())

    assert part.tool_output == first_preview
    assert part.tool_output_original_chars == first_original_chars
    assert part.tool_output_sha256 == first_sha256
    assert part.tool_output_source_ref == source_ref
