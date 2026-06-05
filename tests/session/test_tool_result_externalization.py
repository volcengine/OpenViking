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


async def test_add_message_externalizes_large_tool_output(session: Session):
    session._tool_output_externalization_config = _small_config()
    raw = "alpha-" * 300

    msg = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_1",
                tool_name="read_file",
                tool_input={"path": "a.txt"},
                tool_output=raw,
                tool_status="completed",
            )
        ],
        peer_id="web:visitor:alice",
    )

    part = msg.get_tool_parts()[0]
    assert part.tool_output_truncated is True
    assert part.tool_output_ref.startswith(f"viking://session/{session.session_id}/tool-results/")
    assert part.tool_output_original_chars == len(raw)
    assert part.tool_output_externalized_reason == "single_threshold"
    assert raw not in part.tool_output

    stored = await session.read_tool_result(part.tool_output_ref.rsplit("/", 1)[-1], limit=-1)
    assert stored["content"] == raw
    assert stored["offset_unit"] == "unicode_code_point"
    assert stored["metadata"]["user_id"] == session.ctx.user.user_id
    assert stored["metadata"]["peer_id"] == "web:visitor:alice"


async def test_hydrate_tool_outputs_for_extraction_uses_memory_copy(session: Session):
    session._tool_output_externalization_config = _small_config()
    raw = "alpha-" * 300

    msg = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_hydrate",
                tool_name="read_file",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    )
    compressed_part = msg.get_tool_parts()[0]
    compressed_output = compressed_part.tool_output

    hydrated = await session._hydrate_tool_outputs_for_extraction([msg])

    assert hydrated[0] is not msg
    assert hydrated[0].get_tool_parts()[0].tool_output == raw
    assert hydrated[0].get_tool_parts()[0].tool_output_ref == compressed_part.tool_output_ref
    assert compressed_part.tool_output == compressed_output
    assert raw not in compressed_part.tool_output


async def test_externalized_tool_output_uses_typed_synopsis_stub(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        preview_chars=200,
    )
    raw = '{"users":[{"id":1,"name":"Ada"},{"id":2,"name":"Lin"}],"meta":{"count":2}}' * 3

    msg = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_json_synopsis",
                tool_name="fetch_json",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    )

    part = msg.get_tool_parts()[0]
    assert part.tool_output_truncated is True
    assert "kind: json" in part.tool_output
    assert "Synopsis:" in part.tool_output
    assert "users" in part.tool_output
    assert "openviking_tool_result_search" in part.tool_output
    assert raw not in part.tool_output

    tool_result_id = part.tool_output_ref.rsplit("/", 1)[-1]
    stored = await session.read_tool_result(tool_result_id, limit=-1)
    assert stored["content"] == raw
    assert stored["metadata"]["synopsis"]["kind"] == "json"
    assert stored["metadata"]["synopsis_kind"] == "json"


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


async def test_preview_contract_fields_remain_present(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        preview_chars=40,
    )
    raw = "alpha-" * 20

    part = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_contract",
                tool_name="read_file",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]

    assert "[OpenViking tool result externalized]" in part.tool_output
    assert "tool_name: read_file" in part.tool_output
    assert "original_chars:" in part.tool_output
    assert "preview_chars:" in part.tool_output
    assert "ref:" in part.tool_output
    assert "sha256:" in part.tool_output
    assert "reason: single_threshold" in part.tool_output


async def test_externalized_tool_output_uses_mime_type_for_xml(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        preview_chars=200,
    )
    raw = "\ufeff<root><item id='1'>A</item><item id='2'>B</item></root>"

    part = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_xml",
                tool_name="fetch_xml",
                tool_output=raw,
                tool_output_mime_type="application/xml",
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]

    assert "kind: xml" in part.tool_output
    assert "root: root" in part.tool_output


async def test_tool_output_at_or_below_threshold_remains_inline_when_budget_allows(
    session: Session,
):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        assistant_turn_inline_budget_chars=1000,
    )

    below = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_below_threshold",
                tool_name="echo",
                tool_output="x" * 19,
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]
    equal = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_equal_threshold",
                tool_name="echo",
                tool_output="y" * 20,
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]

    assert below.tool_output_truncated is False
    assert below.tool_output_ref == ""
    assert below.tool_output == "x" * 19
    assert equal.tool_output_truncated is False
    assert equal.tool_output_ref == ""
    assert equal.tool_output == "y" * 20


async def test_assistant_turn_budget_splits_aggregate_and_externalizes_largest_output(
    session: Session,
):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=1200,
        assistant_turn_inline_budget_chars=800,
    )

    start_count = len(session.messages)
    returned = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_a",
                tool_name="tool_a",
                tool_output=_json_items_payload(40),
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_b",
                tool_name="tool_b",
                tool_output=_json_items_payload(1),
                tool_status="completed",
            ),
        ],
    )

    new_messages = session.messages[start_count:]
    assert len(new_messages) == 2
    assert returned == new_messages[0]
    assert [len(msg.parts) for msg in new_messages] == [1, 1]

    parts = [msg.get_tool_parts()[0] for msg in new_messages]
    externalized = [p for p in parts if p.tool_output_truncated]
    assert len(externalized) == 1
    assert externalized[0].tool_id == "call_a"
    assert externalized[0].tool_output_externalized_reason == "turn_budget"
    assert all(
        p.tool_output_group_original_chars
        == len(_json_items_payload(40)) + len(_json_items_payload(1))
        for p in parts
    )
    assert parts[0].tool_output_group_id == parts[1].tool_output_group_id


async def test_assistant_turn_budget_externalizes_multiple_largest_outputs(
    session: Session,
):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=1200,
        assistant_turn_inline_budget_chars=1600,
        assistant_turn_preview_budget_chars=120,
        preview_chars=12,
    )

    start_count = len(session.messages)
    session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_a",
                tool_name="tool_a",
                tool_output=_json_items_payload(40),
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_b",
                tool_name="tool_b",
                tool_output=_json_items_payload(40),
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_c",
                tool_name="tool_c",
                tool_output=_json_items_payload(1),
                tool_status="completed",
            ),
        ],
    )

    parts = [msg.get_tool_parts()[0] for msg in session.messages[start_count:]]
    by_id = {part.tool_id: part for part in parts}

    assert by_id["call_a"].tool_output_truncated is True
    assert by_id["call_b"].tool_output_truncated is True
    assert by_id["call_c"].tool_output_truncated is False
    assert by_id["call_a"].tool_output_externalized_reason == "turn_budget"
    assert by_id["call_b"].tool_output_externalized_reason == "turn_budget"


async def test_assistant_turn_budget_skips_externalization_when_stub_is_larger(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=100,
        assistant_turn_inline_budget_chars=25,
        assistant_turn_preview_budget_chars=20,
        preview_chars=12,
    )

    start_count = len(session.messages)
    session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_a",
                tool_name="tool_a",
                tool_output="a" * 18,
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_b",
                tool_name="tool_b",
                tool_output="b" * 12,
                tool_status="completed",
            ),
        ],
    )

    parts = [msg.get_tool_parts()[0] for msg in session.messages[start_count:]]
    assert [part.tool_output_truncated for part in parts] == [False, False]
    assert sum(len(part.tool_output or "") for part in parts) == 30


async def test_assistant_turn_budget_uses_rendered_stub_length(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=1200,
        assistant_turn_inline_budget_chars=1600,
        assistant_turn_preview_budget_chars=120,
        preview_chars=12,
    )

    start_count = len(session.messages)
    session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_a",
                tool_name="tool_a",
                tool_output=_json_items_payload(40),
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_b",
                tool_name="tool_b",
                tool_output=_json_items_payload(40),
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_c",
                tool_name="tool_c",
                tool_output=_json_items_payload(1),
                tool_status="completed",
            ),
        ],
    )

    parts = [msg.get_tool_parts()[0] for msg in session.messages[start_count:]]
    by_id = {part.tool_id: part for part in parts}
    raw_a = _json_items_payload(40)
    raw_b = _json_items_payload(40)
    raw_c = _json_items_payload(1)
    ref_a = (
        f"viking://session/{session.session_id}/tool-results/"
        f"{tool_result_store.build_tool_result_id('call_a', tool_result_store.sha256_text(raw_a))}"
    )
    stub_a = tool_result_store.make_preview(
        raw_a,
        preview_chars=12,
        ref=ref_a,
        tool_name="tool_a",
        sha256=tool_result_store.sha256_text(raw_a),
        reason="turn_budget",
        original_chars=len(raw_a),
        mime_type="text/plain",
    )
    single_externalized_total = len(stub_a) + len(raw_b) + len(raw_c)

    assert by_id["call_a"].tool_output_truncated is True
    assert by_id["call_b"].tool_output_truncated is True
    assert by_id["call_c"].tool_output_truncated is False
    assert single_externalized_total > 1600
    assert sum(len(part.tool_output or "") for part in parts) < single_externalized_total


async def test_tool_result_aggregate_splits_when_externalization_disabled(session: Session):
    session._tool_output_externalization_config = _small_config(enabled=False)
    start_count = len(session.messages)

    session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_a",
                tool_name="tool_a",
                tool_output="a",
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_b",
                tool_name="tool_b",
                tool_output="b",
                tool_status="completed",
            ),
        ],
    )

    new_messages = session.messages[start_count:]
    assert len(new_messages) == 2
    assert [msg.get_tool_parts()[0].tool_id for msg in new_messages] == ["call_a", "call_b"]
    assert all(msg.get_tool_parts()[0].tool_output_truncated is False for msg in new_messages)


async def test_read_back_tool_result_reuses_source_ref(session: Session):
    session._tool_output_externalization_config = _small_config()
    raw = "source-" * 20
    original = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_src",
                tool_name="read_file",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]

    read_msg = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_read",
                tool_name="openviking_tool_result_read",
                tool_input={"tool_output_ref": original.tool_output_ref, "offset": 0, "limit": 50},
                tool_output=raw[:50],
                tool_status="completed",
            )
        ],
    )

    read_part = read_msg.get_tool_parts()[0]
    assert read_part.tool_output_ref == original.tool_output_ref
    assert read_part.tool_output_source_ref == original.tool_output_ref
    assert read_part.tool_output_source_offset == 0
    assert read_part.tool_output_source_limit == 50
    assert read_part.tool_output_externalized_reason == "source_read"
    assert read_part.tool_output_truncated is False

    hydrated = await session._hydrate_tool_outputs_for_extraction([read_msg])
    assert hydrated[0].get_tool_parts()[0].tool_output == raw[:50]


async def test_read_back_tool_result_preview_honors_min_preview_chars(session: Session):
    session._tool_output_externalization_config = _small_config(
        preview_chars=4,
        min_preview_chars=12,
    )
    raw = "source-" * 20
    original = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_src_min_preview",
                tool_name="read_file",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]

    read_msg = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_read_min_preview",
                tool_name="openviking_tool_result_read",
                tool_input={"tool_output_ref": original.tool_output_ref},
                tool_output="abcdefghij" * 4,
                tool_status="completed",
            )
        ],
    )

    read_part = read_msg.get_tool_parts()[0]
    assert "kind:" in read_part.tool_output
    assert "preview_chars: 12" in read_part.tool_output
    assert "openviking_tool_result_read" in read_part.tool_output
    assert "abcdef" in read_part.tool_output
    assert "efghij" in read_part.tool_output


async def test_tool_result_pairing_survives_synopsis_stubbing(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        assistant_turn_inline_budget_chars=25,
        preview_chars=100,
    )

    returned = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_big",
                tool_name="fetch_json",
                tool_output='{"items":[{"id":1},{"id":2}]}' * 5,
                tool_status="completed",
            ),
            ToolPart(
                tool_id="call_small",
                tool_name="echo",
                tool_output="ok",
                tool_status="completed",
            ),
        ],
    )

    assert returned.get_tool_parts()[0].tool_id in {"call_big", "call_small"}
    all_parts = [msg.get_tool_parts()[0] for msg in session.messages[-2:]]
    assert {part.tool_id for part in all_parts} == {"call_big", "call_small"}
    big = next(part for part in all_parts if part.tool_id == "call_big")
    small = next(part for part in all_parts if part.tool_id == "call_small")
    assert big.tool_output_truncated is True
    assert "kind: json" in big.tool_output
    assert small.tool_output == "ok"


async def test_update_tool_part_externalizes_large_output(session_with_tool_call):
    session, message_id, tool_id = session_with_tool_call
    session._tool_output_externalization_config = _small_config()
    raw = "updated-" * 20

    session.update_tool_part(message_id, tool_id, raw, status="completed")

    msg = next(m for m in session.messages if m.id == message_id)
    part = msg.find_tool_part(tool_id)
    assert part is not None
    assert part.tool_output_truncated is True
    assert part.tool_output_ref
    stored = await session.read_tool_result(part.tool_output_ref.rsplit("/", 1)[-1], limit=-1)
    assert stored["content"] == raw


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


async def test_tool_result_search_uses_raw_payload_not_stub_helper_text(session: Session):
    session._tool_output_externalization_config = _small_config(
        threshold_chars=20,
        preview_chars=60,
    )
    raw = "needle\n" + ("ordinary payload\n" * 40)

    part = session.add_message(
        "user",
        [
            ToolPart(
                tool_id="call_search_raw",
                tool_name="read_log",
                tool_output=raw,
                tool_status="completed",
            )
        ],
    ).get_tool_parts()[0]
    tool_result_id = part.tool_output_ref.rsplit("/", 1)[-1]

    assert "openviking_tool_result_read" in part.tool_output
    raw_match = await session.search_tool_result(tool_result_id, query="needle")
    helper_match = await session.search_tool_result(
        tool_result_id,
        query="openviking_tool_result_read",
    )

    assert len(raw_match["matches"]) == 1
    assert helper_match["matches"] == []
