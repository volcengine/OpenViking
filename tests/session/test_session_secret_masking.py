# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from openviking.message import ContextPart, TextPart, ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.session import secret_masking
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.config.open_viking_config import get_openviking_config

SECRET = "sk-proj-abcd012345678901234567890123wxyz"
MASKED = "sk-proj-ab****yz"
ARK_SECRET = "ark-1256ca13-21f2-4124-8832-0411e3c355sb-e95ee"
ARK_MASKED = "ark-12****ee"


@pytest.fixture(autouse=True)
def masking_enabled(service, monkeypatch):
    monkeypatch.setattr(get_openviking_config().memory, "session_secret_masking_enabled", True)


@pytest.mark.parametrize(
    "method", ["add_message", "add_message_async", "add_messages", "add_messages_async"]
)
@pytest.mark.parametrize("enabled", [False, True])
async def test_append_entry_points_store_expected_content(
    session, client, monkeypatch, method, enabled
):
    monkeypatch.setattr(get_openviking_config().memory, "session_secret_masking_enabled", enabled)
    monkeypatch.setattr(get_openviking_config().memory, "extraction_enabled", False)
    if not enabled:
        monkeypatch.setattr(secret_masking, "MAX_TEXT_CHARS", 1)
    body = "abcd012345678901234567890123wxyz"
    text = f"{SECRET}\nMY_COMPANY_API_KEY={body}"
    abstract = f"EVAL_ARK_API_KEY={ARK_SECRET}"
    parameters = {"secret_key": SECRET, "MY-API-KEY": body, "DB_PASSWORD": body}
    output = f"curl --api-key {body} --user synthetic:{body} url"
    parts = [
        TextPart(text),
        ContextPart(abstract=abstract),
        ToolPart(tool_id="config-check", tool_input=parameters, tool_output=output),
    ]
    original = deepcopy(parts)
    if "messages" in method:
        result = getattr(session, method)([{"role": "assistant", "parts": parts}])
    else:
        result = getattr(session, method)("assistant", parts)
    if method.endswith("async"):
        await result
    if enabled:
        assert parts == original
    fresh = client(session_id=session.session_id)
    await fresh.load()
    stored = fresh.messages[0].parts
    assert stored[0].text == (f"{MASKED}\nMY_COMPANY_API_KEY=ab****yz" if enabled else text)
    assert stored[1].abstract == (f"EVAL_ARK_API_KEY={ARK_MASKED}" if enabled else abstract)
    assert stored[2].tool_input == (
        {"secret_key": MASKED, "MY-API-KEY": "ab****yz", "DB_PASSWORD": "ab****yz"}
        if enabled
        else parameters
    )
    assert stored[2].tool_output == (
        "curl --api-key ab****yz --user [REDACTED] url" if enabled else output
    )


async def test_externalized_output_and_archive_only_contain_masked_content(
    session, client, monkeypatch
):
    session._tool_output_externalization_config = ToolOutputExternalizationConfig(
        threshold_chars=100
    )
    output = "\n".join(
        json.dumps(
            {
                "trace_id": index,
                "api_key": SECRET,
                "EVAL-ARK_API-KEY": ARK_SECRET,
                "DB_PASSWORD": "abcd012345678901234567890123wxyz",
                "command": "curl --user synthetic:abcd012345678901234567890123wxyz url",
                "status": ["ok", "retry", "timeout"][index % 3],
            }
        )
        for index in range(30)
    )
    original = ToolPart(tool_id="deploy-logs", tool_output=output, tool_status="completed")
    messages = await session.add_messages_async(
        [
            {"role": "user", "parts": [TextPart("Summarize the deployment checks.")]},
            {"role": "assistant", "parts": [original]},
        ]
    )
    stored = messages[1].parts[0]
    assert original.tool_output == output
    assert not original.tool_output_ref
    assert stored.tool_output_ref
    external = await session._viking_fs.read_file(
        f"{stored.tool_output_ref}/output.txt", ctx=session.ctx
    )
    expected = output.replace(SECRET, MASKED).replace(ARK_SECRET, ARK_MASKED)
    expected = expected.replace("synthetic:abcd012345678901234567890123wxyz", "[REDACTED]")
    expected = expected.replace("abcd012345678901234567890123wxyz", "ab****yz")
    assert external == expected
    assert SECRET not in stored.tool_output
    fresh = client(session_id=session.session_id)
    await fresh.load()
    hydrated = await fresh._tool_outputs.hydrate_for_extraction(fresh.messages)
    assert hydrated[1].parts[0].tool_output == external

    inputs = []

    async def extract(**kwargs):
        inputs.extend(kwargs["messages"])
        return []

    monkeypatch.setattr(session._session_compressor, "extract_long_term_memories", extract)
    result = await session.commit_async(enable_working_memory=False)
    from tests.session.test_session_commit import _wait_for_task

    task = await _wait_for_task(result["task_id"])
    assert task["status"] == "completed"
    assert any(
        p.tool_output == external for m in inputs for p in m.parts if isinstance(p, ToolPart)
    )
    archive = await session._viking_fs.read_file(
        f"{result['archive_uri']}/messages.jsonl", ctx=session.ctx
    )
    assert SECRET not in archive
    assert ARK_SECRET not in archive


@pytest.mark.parametrize("failure", ["limit", "scanner_error"])
async def test_failed_batch_does_not_externalize_or_append(session, monkeypatch, failure):
    session._tool_output_externalization_config = ToolOutputExternalizationConfig(
        threshold_chars=20
    )
    await session.add_message_async("user", [TextPart("Existing valid message.")])
    before = await session._viking_fs.read_file(f"{session.uri}/messages.jsonl", ctx=session.ctx)
    if failure == "limit":
        monkeypatch.setattr(secret_masking, "MAX_DEPTH", 1)
        expected_error = InvalidArgumentError
    else:
        monkeypatch.setattr(
            "openviking.session.session.mask_message_specs_async",
            AsyncMock(side_effect=RuntimeError("scanner unavailable")),
        )
        expected_error = RuntimeError
    with pytest.raises(expected_error):
        await session.add_messages_async(
            [
                {
                    "role": "assistant",
                    "parts": [
                        ToolPart(
                            tool_id="must-not-externalize",
                            tool_output="\n".join(f"trace={i} api_key={SECRET}" for i in range(10)),
                        )
                    ],
                },
                {"role": "assistant", "parts": [ToolPart(tool_input={"a": {"b": SECRET}})]},
            ]
        )
    assert (
        await session._viking_fs.read_file(f"{session.uri}/messages.jsonl", ctx=session.ctx)
        == before
    )
    assert (await session.list_tool_results())["tool_results"] == []


async def test_storage_failure_fallback_never_restores_unmasked_output(session, monkeypatch):
    from openviking.session.tool_result_store import ToolResultStore

    session._tool_output_externalization_config = ToolOutputExternalizationConfig(
        threshold_chars=20, failure_mode="preserve_raw"
    )
    monkeypatch.setattr(
        ToolResultStore, "write", AsyncMock(side_effect=OSError("storage unavailable"))
    )
    await session.add_message_async("assistant", [ToolPart(tool_output=(SECRET + "\n") * 10)])
    assert session.messages[0].parts[0].tool_output == (MASKED + "\n") * 10
