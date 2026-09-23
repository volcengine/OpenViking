# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json

import pytest

import openviking.session.session as session_module
import openviking.session.tool_result_store as tool_result_store
from openviking.message import ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.session import Session
from openviking.session.tool_result_store import ToolResultStore
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import FailedPreconditionError
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    yield


@pytest.fixture
def session(tmp_path):
    return Session(
        VikingFS(agfs=MockLocalAGFS(root_path=tmp_path)),
        session_id="test_session_tool_results",
    )


@pytest.fixture
def session_with_tool_call(session):
    tool_id = "test_tool_001"
    tool_part = ToolPart(
        tool_id=tool_id,
        tool_name="test_tool",
        tool_input={"param": "value"},
        tool_status="running",
    )
    msg = session.add_message("assistant", [tool_part])
    return session, msg.id, tool_id


def _small_config(**overrides):
    values = {
        "threshold_chars": 20,
        "preview_chars": 12,
        "assistant_turn_inline_budget_chars": 30,
        "assistant_turn_preview_budget_chars": 20,
        "min_preview_chars": 4,
    }
    values.update(overrides)
    return ToolOutputExternalizationConfig(**values)


def _json_items_payload(count: int) -> str:
    items = ",".join(f'{{"id":{idx},"name":"item{idx}"}}' for idx in range(count))
    return f'{{"items":[{items}]}}'


@pytest.mark.parametrize("failure_mode", [None, "reject", "preserve_raw", "preview_only"])
async def test_tool_output_externalization_write_contract(
    session: Session, monkeypatch, failure_mode
):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        preview_chars=200,
        failure_mode=failure_mode or "preserve_raw",
    )
    raw = '{"users":[{"id":1,"name":"Ada"},{"id":2,"name":"Lin"}],"meta":{"count":2}}' * 3
    part = ToolPart(
        tool_id="call_json_synopsis_once",
        tool_name="fetch_json",
        tool_output=raw,
        tool_status="completed",
    )
    calls = 0
    original_synopsis = tool_result_store.generate_tool_result_synopsis

    def wrapped(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal calls
        calls += 1
        return original_synopsis(*args, **kwargs)

    monkeypatch.setattr(tool_result_store, "generate_tool_result_synopsis", wrapped)
    monkeypatch.setattr(session_module, "generate_tool_result_synopsis", wrapped)
    caller_loop = asyncio.get_running_loop()
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    original_write = ToolResultStore.write

    async def delayed_write(store, **kwargs):
        assert asyncio.get_running_loop() is caller_loop
        write_started.set()
        await release_write.wait()
        if failure_mode:
            raise OSError("storage unavailable")
        return await original_write(store, **kwargs)

    monkeypatch.setattr(ToolResultStore, "write", delayed_write)
    append = asyncio.create_task(session.add_messages_async([{"role": "user", "parts": [part]}]))
    try:
        await asyncio.wait_for(write_started.wait(), timeout=2)
        assert not append.done()
        assert part.tool_output == raw
        assert not part.tool_output_ref
        assert session.messages == []
        release_write.set()
        if failure_mode == "reject":
            with pytest.raises(FailedPreconditionError, match="Failed to externalize"):
                await append
            assert session.messages == []
        else:
            messages = await asyncio.wait_for(append, timeout=2)
            assert session.messages[0].id == messages[0].id
            if failure_mode is None:
                assert part.tool_output_ref
                assert (
                    await session._viking_fs.read_file(f"{part.tool_output_ref}/output.txt") == raw
                )
                assert calls == 1
            else:
                assert not part.tool_output_ref
                assert "storage unavailable" in part.tool_output_externalization_error
            if failure_mode == "preserve_raw":
                assert part.tool_output == raw
            else:
                assert part.tool_output_truncated
                assert part.tool_output != raw
    finally:
        release_write.set()
        await asyncio.gather(append, return_exceptions=True)


async def test_list_tool_results_filters_tool_name_before_limit():
    class FakeVikingFS:
        def __init__(self):
            self.entries = [
                {"name": "tr_other", "isDir": True},
                {"name": "tr_target", "isDir": True},
            ]
            self.metadata = {
                "tr_other": {"tool_result_id": "tr_other", "tool_name": "other"},
                "tr_target": {"tool_result_id": "tr_target", "tool_name": "target"},
            }

        async def ls(self, uri, *, output, node_limit, ctx):  # noqa: ANN001
            return self.entries[:node_limit]

        async def read_file(self, uri, *, ctx):  # noqa: ANN001
            tool_result_id = uri.rstrip("/").split("/")[-2]
            return json.dumps(self.metadata[tool_result_id])

    store = ToolResultStore(
        FakeVikingFS(),
        "viking://user/alice/sessions/filter-before-limit",
        "filter-before-limit",
        ctx=None,
    )

    result = await store.list(tool_name="target", limit=1)

    assert result["tool_results"] == [{"tool_result_id": "tr_target", "tool_name": "target"}]
