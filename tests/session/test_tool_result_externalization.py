# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

import openviking.session.session as session_module
import openviking.session.tool_result_store as tool_result_store
from openviking.message import ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.session import Session
from openviking.session.tool_result_store import ToolResultStore
from openviking_cli.exceptions import NotFoundError


class MemoryVikingFS:
    def __init__(self):
        self.files = {}

    async def write_file(self, uri, content, *, ctx=None):  # noqa: ANN001
        self.files[uri] = content

    async def append_file(self, uri, content, *, ctx=None):  # noqa: ANN001
        self.files[uri] = self.files.get(uri, "") + content

    async def read_file(self, uri, *, ctx=None):  # noqa: ANN001
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def ls(self, uri, *, output="original", node_limit=1000, ctx=None):  # noqa: ANN001
        prefix = uri.rstrip("/") + "/"
        names = set()
        for file_uri in self.files:
            if not file_uri.startswith(prefix):
                continue
            rest = file_uri[len(prefix) :]
            first = rest.split("/", 1)[0]
            if first:
                names.add(first)
        return [{"name": name, "isDir": True} for name in sorted(names)][:node_limit]


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    yield


@pytest.fixture
def session():
    return Session(MemoryVikingFS(), session_id="test_session_tool_results")


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


async def test_externalized_tool_output_generates_synopsis_once(session: Session, monkeypatch):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        preview_chars=200,
    )
    raw = '{"users":[{"id":1,"name":"Ada"},{"id":2,"name":"Lin"}],"meta":{"count":2}}' * 3

    calls = 0
    original = tool_result_store.generate_tool_result_synopsis

    def wrapped(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tool_result_store, "generate_tool_result_synopsis", wrapped)
    monkeypatch.setattr(session_module, "generate_tool_result_synopsis", wrapped)

    session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_json_synopsis_once",
                tool_name="fetch_json",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    )

    assert calls == 1


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
        "viking://session/filter-before-limit",
        "filter-before-limit",
        ctx=None,
    )

    result = await store.list(tool_name="target", limit=1)

    assert result["tool_results"] == [{"tool_result_id": "tr_target", "tool_name": "target"}]
