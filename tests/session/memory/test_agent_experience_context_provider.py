# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock, patch

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


def test_create_tool_context_uses_extract_context_page_id_map():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md",
    )

    extract_context = provider.get_extract_context()
    extract_context.page_id_map.get_page_id(
        "viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md"
    )

    tool_ctx = provider.create_tool_context()

    assert tool_ctx.page_id_map is extract_context.page_id_map


def test_user_memory_provider_splits_but_trajectory_provider_keeps_messages_whole():
    text = "第一句很长很长很长很长很长很长很长很长很长很长很长。" * 8
    messages = [Message(id="1", role="user", parts=[TextPart(text=text)])]

    user_provider = SessionExtractContextProvider(messages=messages)
    trajectory_provider = AgentTrajectoryContextProvider(messages=messages)

    assert len(user_provider.get_extract_context().messages) > 1
    assert len(trajectory_provider.get_extract_context().messages) == 1
    assert trajectory_provider.get_extract_context().messages[0] is messages[0]


def test_agent_only_schemas_are_excluded_from_peer_user_memory_extraction():
    messages = [
        Message(
            id="1",
            role="user",
            parts=[TextPart(text="帮我导出 B 站投稿数据")],
            peer_id="case_demo_peer",
        )
    ]
    ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="default"),
        role=Role.USER,
    )
    provider = SessionExtractContextProvider(messages=messages)
    provider._isolation_handler = MemoryIsolationHandler(
        ctx,
        ExtractContext(messages),
        allow_self=False,
        allowed_peer_ids={"case_demo_peer"},
    )

    schema_types = {schema.memory_type for schema in provider.get_memory_schemas(ctx)}

    assert "experiences" not in schema_types
    assert "trajectories" not in schema_types


@pytest.mark.asyncio
async def test_agent_experience_prefetch_reads_new_trajectory_without_conversation():
    provider = AgentExperienceContextProvider(
        messages=[Message(id="raw", role="user", parts=[TextPart("RAW_CONVERSATION")])],
        trajectory_summary="album release party discussion",
        trajectory_task_query="Help arrange the release party first.",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1"),
        role=Role.USER,
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None
    provider.search_files = AsyncMock(return_value=[])
    provider._build_conversation_message = AsyncMock(side_effect=AssertionError("No conversation"))

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert messages[0]["role"] == "user"
    assert all("## Conversation History" not in message["content"] for message in messages)
    provider._build_conversation_message.assert_not_called()
    assert add_tool_call_pair.call_count == 1
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["context_role"] == "new_trajectory"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["memory_type"] == "trajectories"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["uri"] == provider.trajectory_uri
    assert (
        add_tool_call_pair.call_args_list[0].kwargs["result"]["task_query"]
        == "Help arrange the release party first."
    )
    assert messages[-1]["role"] == "user"
    assert provider.get_conversation_text() == provider.trajectory_summary
    assert "RAW_CONVERSATION" not in json.dumps(messages)
    provider.search_files.assert_not_awaited()
    assert provider.get_memory_schemas(provider._ctx)[0].operation_mode == "add_only"
    assert provider._get_registry().get("experiences").operation_mode == "upsert"


def _history_provider(records, *, case_memory_type="cases", case_signature="returns"):
    root = "viking://user/user_1/memories"
    case_uri = f"{root}/cases/returns.md"
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="Current execution with its own evaluation.",
        trajectory_uri=f"{root}/trajectories/current.md",
        trajectory_case_uris=[case_uri, "viking://user/other/memories/cases/returns.md"],
        trajectory_source_archive_uri="viking://user/user_1/sessions/current/history/archive_001",
        trajectory_evaluation={"score": 0.0},
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1"), role=Role.USER
    )
    files = {}
    links = []
    for index, (name, outcome, content, extra) in enumerate(records):
        uri = name if name.startswith("viking://") else f"{root}/trajectories/{name}.md"
        links.append({"to_uri": uri, "created_at": f"2026-10-28T00:{index:02}:00Z"})
        if content is not None:
            files[uri] = MemoryFileUtils.write(
                MemoryFile(
                    uri=uri,
                    memory_type="trajectories",
                    content=content,
                    extra_fields={"outcome": outcome, "case_uris": [case_uri], **extra},
                )
            )
    files[case_uri] = MemoryFileUtils.write(
        MemoryFile(
            uri=case_uri,
            memory_type=case_memory_type,
            content="Case, not an extraction input.",
            extra_fields={"case_name": "returns", "task_signature": case_signature},
            links=links,
        )
    )

    async def read_file(uri, ctx):
        assert ctx is provider._ctx
        if uri not in files:
            raise FileNotFoundError(uri)
        return files[uri]

    provider._viking_fs = AsyncMock()
    provider._viking_fs.read_file.side_effect = read_file
    provider._viking_fs.ls.return_value = []
    provider.search_files = AsyncMock(return_value=[])
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_memory_type", "case_signature", "valid_case"),
    [
        ("cases", "returns", True),
        (None, "returns", True),
        ("experiences", "returns", False),
    ],
)
async def test_experience_history_keeps_outcome_diversity_without_cross_case_or_duplicate_runs(
    case_memory_type, case_signature, valid_case
):
    archive = "viking://user/user_1/sessions/old/history/archive_001"
    provider = _history_provider(
        [
            ("failure", "failure", "Exchange failed after return changed the state.", {}),
            ("partial", "partial", "Only one request was completed.", {}),
            ("success_older", "success", "Older successful execution.", {}),
            (
                "same_archive",
                "success",
                "Re-extracted version of the same execution.",
                {"source_archive_uri": archive},
            ),
            (
                "success",
                "success",
                "Explained the conflict before writing; return completed.",
                {"source_archive_uri": archive, "task_query": "Return the selected item first."},
            ),
            ("duplicate", "failure", "Current execution with its own evaluation.", {}),
            (
                "current_archive",
                "failure",
                "Another task from the current archive.",
                {
                    "source_archive_uri": "viking://user/user_1/sessions/current/history/archive_001/"
                },
            ),
            (
                "unrelated",
                "failure",
                "Wrong Case.",
                {"case_uris": ["viking://user/user_1/memories/cases/other.md"]},
            ),
            ("missing", "failure", None, {}),
            ("viking://user/other/memories/trajectories/foreign.md", "failure", "Wrong user.", {}),
            ("../cases/escape", "failure", "Wrong directory.", {}),
        ],
        case_memory_type=case_memory_type,
        case_signature=case_signature,
    )

    messages = await provider.prefetch()
    results = [json.loads(msg["content"])["result"] for msg in messages[:-1]]
    historical = [result for result in results if result["context_role"] == "historical_trajectory"]

    if not valid_case:
        assert historical == []
        assert provider.history_uris == []
        assert provider._viking_fs.read_file.await_count == 1
        provider._viking_fs.write_file.assert_not_awaited()
        return

    assert [result["outcome"] for result in historical] == ["success", "failure"]
    assert historical[0]["task_query"] == "Return the selected item first."
    assert provider.history_uris == [result["uri"] for result in historical]
    assert provider.history_uris == [
        "viking://user/user_1/memories/trajectories/success.md",
        "viking://user/user_1/memories/trajectories/failure.md",
    ]
    assert results[0]["post_hoc_evaluation"] == {"score": 0.0}
    assert all("post_hoc_evaluation" not in item for item in historical)
    assert "Case, not an extraction input" not in str(messages)
    assert provider.read_file_contents == {}  # Historical records are read-only context.
    read_uris = [call.args[0] for call in provider._viking_fs.read_file.await_args_list]
    assert not any("/other/" in uri or "/../" in uri for uri in read_uris)
    provider._viking_fs.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_experience_history_falls_back_to_recent_records_and_bounds_reads():
    count = 25
    provider = _history_provider(
        [(f"history_{idx}", "success", f"Distinct execution {idx}.", {}) for idx in range(count)]
    )

    await provider.prefetch()

    assert provider.history_uris == [
        f"viking://user/user_1/memories/trajectories/history_{idx}.md"
        for idx in range(count - 1, max(-1, count - 3), -1)
    ]
    assert provider._viking_fs.read_file.await_count == 1 + min(count, 20)
