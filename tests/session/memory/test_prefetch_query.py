# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.prompts.manager import PromptManager
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryTypeSchema
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.prefetch_query import (
    build_maintenance_queries,
    build_maintenance_targets,
)
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.memory_config import MemoryConfig


@pytest.fixture(autouse=True)
def runtime_config(monkeypatch):
    config = SimpleNamespace(
        output_language_override="",
        language_fallback="en",
        memory=MemoryConfig(),
    )
    monkeypatch.setattr(
        "openviking.session.memory.session_extract_context_provider.get_openviking_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.get_openviking_config", lambda: config
    )
    manager = PromptManager(templates_dir=PromptManager._get_bundled_templates_dir())
    monkeypatch.setattr("openviking.session.memory.prefetch_query.render_prompt", manager.render)


def _target(aspect="commute method and transportation", indices=None, *, subject="user", scope=""):
    return {
        "subject": subject,
        "aspect": aspect,
        "scope": scope,
        "message_indices": [0] if indices is None else indices,
    }


def _vlm(targets):
    return SimpleNamespace(
        is_available=lambda: True,
        get_completion_async=AsyncMock(return_value=json.dumps({"targets": targets})),
    )


async def test_query_planner_receives_full_ordered_text_with_roles_peers_and_dates():
    messages = [
        Message(
            id="a",
            role="assistant",
            peer_id="peer-a",
            created_at="2026-04-01T08:00:00Z",
            parts=[
                TextPart("A suggestion, not an adopted preference. " * 180),
                ToolPart(tool_id="t", tool_name="read", tool_output="DO_NOT_SEND_TOOL_OUTPUT"),
            ],
        ),
        Message(
            id="b",
            role="user",
            peer_id="peer-b",
            created_at="2026-04-01T08:01:00Z",
            parts=[TextPart("I now take the subway to work."), TextPart("I read on the train.")],
        ),
    ]
    original = [message.to_dict() for message in messages]
    vlm = _vlm([_target(indices=[1]), _target("reading", [1], scope="during commute")])

    queries = await build_maintenance_queries(messages, vlm)

    assert queries == ["user commute method and transportation", "user reading during commute"]
    kwargs = vlm.get_completion_async.await_args.kwargs
    records = json.loads(kwargs["messages"][1]["content"])
    assert [r["message_index"] for r in records] == [0, 1]
    assert [r["role"] for r in records] == ["assistant", "user"]
    assert records[0]["text"] == messages[0].parts[0].text
    assert records[0]["peer_id"] == "peer-a"
    assert records[0]["created_at"] == "2026-04-01T08:00:00Z"
    assert records[1]["text"].endswith("\nI read on the train.")
    assert "DO_NOT_SEND_TOOL_OUTPUT" not in kwargs["messages"][1]["content"]
    assert "tools" not in kwargs and "functions" not in kwargs
    assert kwargs["thinking"] is False
    assert [message.to_dict() for message in messages] == original


async def test_queries_are_normalized_and_deduplicated():
    vlm = _vlm([_target("  commute  "), _target("COMMUTE"), _target("reading")])
    queries = await build_maintenance_queries(
        [Message(id="a", role="user", parts=[TextPart("I read on the train to work.")])], vlm
    )
    assert queries == ["user commute", "user reading"]


async def test_query_is_built_from_independent_slots_even_with_one_source_message():
    messages = [
        Message(
            id="m",
            role="user",
            parts=[TextPart("I now take the subway to work and read novels on the way.")],
        )
    ]
    vlm = _vlm(
        [
            _target("commute transportation", scope="to work"),
            _target("reading preferences", scope="during commute"),
        ]
    )

    targets = await build_maintenance_targets(messages, vlm)

    assert [t["query"] for t in targets] == [
        "user commute transportation to work",
        "user reading preferences during commute",
    ]
    assert [t["message_indices"] for t in targets] == [[0], [0]]
    assert targets[1]["scope"] == "during commute"
    assert "subway" not in " ".join(t["query"] for t in targets)
    assert "novels" not in " ".join(t["query"] for t in targets)
    vlm.get_completion_async.assert_awaited_once()


async def test_target_deduplication_preserves_all_source_indices_and_scope():
    messages = [
        Message(id=str(i), role="user", parts=[TextPart("My commute routine changed.")])
        for i in range(3)
    ]
    vlm = _vlm(
        [
            _target(" commute method ", [0, 0], subject=" Alice ", scope="to  work"),
            _target("COMMUTE METHOD", [1], subject="ALICE", scope="TO WORK"),
            _target("commute method", [2], subject="Alice", scope="to school"),
        ]
    )

    targets = await build_maintenance_targets(messages, vlm)

    assert len(targets) == 2
    assert targets[0]["query"] == "Alice commute method to work"
    assert targets[0]["message_indices"] == [0, 1]
    assert targets[1]["query"] == "Alice commute method to school"


async def test_ten_independent_targets_are_accepted_without_a_five_query_cutoff():
    vlm = _vlm([_target(f"attribute {i}") for i in range(10)])
    messages = [Message(id="m", role="user", parts=[TextPart("Several attributes changed.")])]

    targets = await build_maintenance_targets(messages, vlm)

    assert len(targets) == 10
    assert targets[-1]["query"] == "user attribute 9"


async def test_named_objects_and_optional_empty_scope_are_preserved():
    target = _target("maintenance history", subject="Acme Roadster bicycle")
    del target["scope"]
    vlm = _vlm([target])
    messages = [Message(id="m", role="user", parts=[TextPart("My Acme Roadster was serviced.")])]

    targets = await build_maintenance_targets(messages, vlm)

    assert targets[0]["query"] == "Acme Roadster bicycle maintenance history"
    assert targets[0]["scope"] == ""


async def test_new_information_is_not_forwarded_to_retrieval():
    target = _target("destination", subject="Alice", scope="honeymoon")
    target["new_information"] = "Plans to visit Canada next month and try poutine."
    vlm = _vlm([target])
    messages = [Message(id="m", role="user", parts=[TextPart(target["new_information"])])]

    targets = await build_maintenance_targets(messages, vlm)

    assert targets == [
        {
            "subject": "Alice",
            "aspect": "destination",
            "scope": "honeymoon",
            "query": "Alice destination honeymoon",
            "message_indices": [0],
        }
    ]


@pytest.mark.parametrize(
    "response",
    [
        "not a plan",
        json.dumps({"targets": [_target(indices=[99])]}),
        json.dumps({"targets": [_target(indices=[True])]}),
        json.dumps({"targets": [_target(indices=[])]}),
        json.dumps({"targets": [_target(" ")]}),
        json.dumps({"targets": [_target("a" * 121)]}),
        json.dumps({"targets": [_target(scope="a" * 161)]}),
        json.dumps({"targets": [_target(scope=123)]}),
        json.dumps({"targets": [_target()] * 11}),
        json.dumps({"targets": [{**_target(), "query": "only the new subway value"}]}),
        json.dumps({"targets": [_target()], "delete": ["old.md"]}),
    ],
)
async def test_invalid_plan_falls_back_to_original_conversation_query(response):
    vlm = _vlm([])
    vlm.get_completion_async.return_value = response
    provider = SessionExtractContextProvider(
        messages=[Message(id="m", role="user", parts=[TextPart("I now take the subway.")])],
        vlm_config=vlm,
    )
    assert await provider._build_prefetch_search_queries() == [
        provider._build_prefetch_search_query()
    ]
    vlm.get_completion_async.assert_awaited_once()


async def test_model_failure_falls_back_but_cancellation_propagates():
    vlm = _vlm([])
    provider = SessionExtractContextProvider(
        messages=[Message(id="m", role="user", parts=[TextPart("I now take the subway.")])],
        vlm_config=vlm,
    )
    vlm.get_completion_async.side_effect = RuntimeError("provider unavailable")
    assert await provider._build_prefetch_search_queries() == [
        provider._build_prefetch_search_query()
    ]
    vlm.get_completion_async.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await provider._build_prefetch_search_queries()


async def test_valid_empty_plan_skips_retrieval_and_empty_text_skips_model():
    vlm = _vlm([])
    provider = SessionExtractContextProvider(
        messages=[Message(id="m", role="user", parts=[TextPart("Hi!")])], vlm_config=vlm
    )
    assert await provider._build_prefetch_search_queries() == []
    assert await build_maintenance_queries([], vlm) == []
    vlm.get_completion_async.assert_awaited_once()


@pytest.mark.parametrize("available", [None, False])
async def test_missing_or_disabled_model_preserves_original_query(available):
    vlm = _vlm([]) if available is not None else None
    if vlm:
        vlm.is_available = lambda: False
    provider = SessionExtractContextProvider(
        messages=[Message(id="m", role="user", parts=[TextPart("I now take the subway.")])],
        vlm_config=vlm,
    )
    assert await provider._build_prefetch_search_queries() == [
        provider._build_prefetch_search_query()
    ]
    if vlm:
        vlm.get_completion_async.assert_not_awaited()


def _provider(*, topn=None, targets=None, only_fixed=False):
    messages = [Message(id="m", role="user", peer_id="p", parts=[TextPart("I read on the train.")])]
    registry = MemoryTypeRegistry(load_schemas=False)
    for name, filename, mode, stage in [
        ("profile", "profile.md", "upsert", "user"),
        ("entities", "{{ category }}/{{ name }}.md", "upsert", "user"),
        ("preferences", "{{ topic }}.md", "upsert", "user"),
        ("events", "{{ name }}.md", "add_only", "user"),
        ("experiences", "{{ name }}.md", "upsert", "agent"),
        ("excluded", "{{ name }}.md", "upsert", "user"),
    ]:
        if only_fixed and name != "profile":
            continue
        registry.register(
            MemoryTypeSchema(
                memory_type=name,
                directory=f"viking://user/{{{{ user_space }}}}/memories/{name}",
                filename_template=filename,
                operation_mode=mode,
                stage=stage,
            )
        )
    ctx = RequestContext(user=UserIdentifier(account_id="acc", user_id="u"), role=Role.USER)
    provider = SessionExtractContextProvider(
        messages=messages, ctx=ctx, memory_registry=registry, vlm_config=_vlm(targets or [])
    )
    if topn is not None:
        provider._prefetch_search_topn = topn
    provider._isolation_handler = MemoryIsolationHandler(
        ctx,
        provider.get_extract_context(),
        allow_self=False,
        allowed_peer_ids={"p"},
        allowed_memory_types={"entities", "preferences", "profile", "events", "experiences"},
    )
    provider.read_file = AsyncMock(return_value={"content": "Existing memory, unchanged."})
    return provider


@pytest.mark.parametrize("topn, expected_count", [(3, 3), (5, 5), (10, 10), (20, 11)])
async def test_prefetch_search_scope_interleaving_and_configured_read_budget(
    topn, expected_count, caplog
):
    caplog.set_level(
        logging.DEBUG, logger="openviking.session.memory.session_extract_context_provider"
    )
    provider = _provider(
        topn=topn,
        targets=[_target("commute method"), _target("commute reading"), _target("commute routine")],
    )
    root = "viking://user/u/peers/p/memories"
    a = [f"{root}/entities/person/p{i}.md" for i in range(5)]
    b = [a[1], *[f"{root}/preferences/p{i}.md" for i in range(4)]]
    c = [a[0], b[2], a[4], f"{root}/entities/person/p5.md", f"{root}/entities/person/p6.md"]
    provider._viking_fs = SimpleNamespace(
        search=AsyncMock(
            side_effect=[
                SimpleNamespace(to_dict=lambda: {"memories": [{"uri": u, "score": 1} for u in a]}),
                SimpleNamespace(to_dict=lambda: {"memories": [{"uri": u, "score": 1} for u in b]}),
                SimpleNamespace(to_dict=lambda: {"memories": [{"uri": u, "score": 1} for u in c]}),
            ]
        )
    )

    messages = await provider.prefetch()

    calls = provider._viking_fs.search.await_args_list
    assert [call.args[0] for call in calls] == [
        "user commute method",
        "user commute reading",
        "user commute routine",
    ]
    for call in calls:
        assert call.kwargs["target_uri"] == [f"{root}/entities", f"{root}/preferences"]
        assert call.kwargs["limit"] == 15  # Existing tool overfetch remains unchanged.
        assert call.kwargs["ctx"] is provider._ctx
    read_uris = [call.args[0] for call in provider.read_file.await_args_list]
    assert read_uris == [
        f"{root}/profile/profile.md",
        *[a[0], a[1], b[1], b[2], a[2], a[4], a[3], b[3], c[3], b[4], c[4]][:expected_count],
    ]
    traces = [json.loads(message["content"]) for message in messages[1:]]
    assert [t["args"]["query"] for t in traces if t["tool_call_name"] == "search"] == [
        "user commute method",
        "user commute reading",
        "user commute routine",
    ]
    assert provider.get_tools() == []
    assert "I read on the train." in messages[0]["content"]
    assert (
        f"candidate_count=15 unique_count=11 selected_count={expected_count} budget={topn}"
        in caplog.text
    )
    assert "Memory prefetch selected URIs:" in caplog.text
    assert "Memory prefetch search detail:" in caplog.text


async def test_fixed_files_still_read_when_no_targets_and_no_planning_without_multi_file_schema():
    for only_fixed in (False, True):
        provider = _provider(only_fixed=only_fixed)
        provider.search_files = AsyncMock()
        await provider.prefetch()
        provider.search_files.assert_not_awaited()
        provider.read_file.assert_awaited_once_with(
            "viking://user/u/peers/p/memories/profile/profile.md"
        )
        assert provider._vlm_config.get_completion_async.await_count == (0 if only_fixed else 1)


async def test_non_eager_mode_keeps_read_tools_and_does_not_preload_candidates():
    provider = _provider(targets=[_target("commute method"), _target("commute reading")])
    provider._eager_prefetch = False
    provider.search_files = AsyncMock(
        return_value=["viking://user/u/peers/p/memories/entities/train.md"]
    )
    await provider.prefetch()
    assert provider.search_files.await_count == 2
    provider.read_file.assert_awaited_once_with(
        "viking://user/u/peers/p/memories/profile/profile.md"
    )
    assert provider.get_tools() == ["read"]


def test_query_prompt_is_not_registered_as_a_memory_schema():
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.load_from_directory(str(PromptManager._get_bundled_templates_dir() / "memory"))
    assert "" not in registry.list_names()
    assert "prefetch_queries" not in registry.list_names()
    assert {"entities", "preferences", "profile"} <= set(registry.list_names())


def test_round_robin_handles_duplicates_empty_and_unequal_results():
    merge = SessionExtractContextProvider._merge_prefetch_search_results
    assert merge([], 10) == []
    assert merge([[], ["a", "b"]], 10) == ["a", "b"]
    assert merge([["a", "a", "b", "c"], ["a", "d", "e"]], 10) == ["a", "d", "b", "e", "c"]


def test_default_merged_candidate_budget_is_ten():
    provider = _provider()
    assert provider._prefetch_search_topn == 10
    groups = [[f"a{i}" for i in range(5)], [f"b{i}" for i in range(5)], ["a0", "c0"]]
    merged = provider._merge_prefetch_search_results(groups, provider._prefetch_search_topn)
    assert len(merged) == 10
    assert len(set(merged)) == 10


async def test_prefetch_reads_unfiltered_results_without_an_extra_model_call():
    provider = _provider(targets=[_target("keys plan"), _target("art hobby")])
    root = "viking://user/u/peers/p/memories"
    keys, weights, art, mystery = [
        f"{root}/entities/{name}.md" for name in ["keys", "weights", "art", "unknown"]
    ]
    groups = [
        [
            {"uri": keys, "score": 0.9, "abstract": "The person frequently loses keys."},
            {"uri": weights, "score": 0.8, "abstract": "The person enjoys weight training."},
            {"uri": art, "score": 0.7, "abstract": "The person paints watercolors."},
        ],
        [
            {"uri": art, "score": 0.9, "abstract": "The person paints watercolors."},
            {"uri": keys, "score": 0.8, "abstract": "The person frequently loses keys."},
            {"uri": mystery, "score": 0.7},
        ],
    ]
    provider._viking_fs = SimpleNamespace(
        search=AsyncMock(
            side_effect=[
                SimpleNamespace(to_dict=lambda group=group: {"memories": group}) for group in groups
            ]
        )
    )

    messages = await provider.prefetch()

    provider._vlm_config.get_completion_async.assert_awaited_once()
    assert [call.args[0] for call in provider.read_file.await_args_list] == [
        f"{root}/profile/profile.md",
        keys,
        art,
        weights,
        mystery,
    ]
    searches = [
        json.loads(m["content"])
        for m in messages[1:]
        if json.loads(m["content"]).get("tool_call_name") == "search"
    ]
    assert [s["result"] for s in searches] == [[keys, weights, art], [art, keys, mystery]]
    assert all(call.kwargs["limit"] == 15 for call in provider._viking_fs.search.await_args_list)


async def test_empty_search_results_keep_fixed_reads_without_an_extra_model_call():
    provider = _provider(targets=[_target("keys tracking plan")])
    provider.search_files = AsyncMock(return_value=[])

    messages = await provider.prefetch()

    provider._vlm_config.get_completion_async.assert_awaited_once()
    provider.read_file.assert_awaited_once_with(
        "viking://user/u/peers/p/memories/profile/profile.md"
    )
    provider.search_files.assert_awaited_once()
    assert json.loads(messages[1]["content"])["result"] == []
