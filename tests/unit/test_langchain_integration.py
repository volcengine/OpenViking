from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.store.base import PutOp

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingRetriever,
    OpenVikingSessionContextAssembler,
    OpenVikingStore,
    create_openviking_tools,
    with_openviking_context,
)
from openviking.integrations.langchain.client import (
    OpenVikingConnection,
    ensure_client,
    maybe_commit_session,
)
from openviking.integrations.langchain.history import (
    langchain_message_to_openviking,
    openviking_message_to_langchain,
)
from openviking.integrations.langchain.middleware import _message_signature
from openviking.integrations.langchain.tools import _archive_grep_pattern


def test_retriever_returns_langchain_documents():
    client = InMemoryOpenVikingClient(
        {
            "viking://user/memories/preferences.md": "The user prefers azure deploys.",
            "viking://resources/runbooks/release.md": "Release notes mention LangChain.",
        }
    )
    retriever = OpenVikingRetriever(
        client=client,
        target_uri=["viking://user/memories", "viking://resources"],
        limit=3,
    )

    docs = retriever.invoke("azure LangChain")

    assert {doc.metadata["openviking_uri"] for doc in docs} == {
        "viking://resources/runbooks/release.md",
        "viking://user/memories/preferences.md",
    }
    assert all(doc.page_content for doc in docs)


def test_create_openviking_tools_exposes_common_viking_primitives():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "The user likes LangGraph agents."}
    )
    tools = create_openviking_tools(client=client, profile="agent")
    names = {tool.name for tool in tools}

    assert {
        "viking_find",
        "viking_search",
        "viking_browse",
        "viking_read",
        "viking_grep",
        "viking_archive_search",
        "viking_archive_expand",
        "viking_store",
        "viking_add_resource",
        "viking_add_skill",
        "viking_health",
    }.issubset(names)
    assert "viking_forget" not in names

    find_tool = next(tool for tool in tools if tool.name == "viking_find")
    assert "viking://user/memories/profile.md" in find_tool.invoke(
        {"query": "LangGraph", "limit": 2}
    )

    store_tool = next(tool for tool in tools if tool.name == "viking_store")
    stored = store_tool.invoke(
        {
            "messages": [
                {"role": "user", "content": "Remember that azure is preferred."},
                {"role": "assistant", "content": "Noted."},
            ],
            "session_id": "test-session",
            "commit": False,
        }
    )
    assert '"messages_added":2' in stored
    assert len(client.sessions["test-session"]) == 2
    assert client.sessions["test-session"][0]["parts"][0]["text"] == (
        "Remember that azure is preferred."
    )


def test_create_openviking_tools_profiles_control_destructive_tools():
    client = InMemoryOpenVikingClient()

    retrieval_names = {
        tool.name for tool in create_openviking_tools(client=client, profile="retrieval")
    }
    assert "viking_store" not in retrieval_names
    assert "viking_add_resource" not in retrieval_names
    assert "viking_add_skill" not in retrieval_names
    assert "viking_forget" not in retrieval_names

    admin_names = {tool.name for tool in create_openviking_tools(client=client, profile="admin")}
    assert "viking_store" in admin_names
    assert "viking_add_resource" in admin_names
    assert "viking_add_skill" in admin_names
    assert "viking_forget" in admin_names

    explicit_names = {
        tool.name
        for tool in create_openviking_tools(
            client=client,
            tool_names=["viking_find", "viking_store"],
        )
    }
    assert explicit_names == {"viking_find", "viking_store"}


def test_openviking_tools_read_l0_l1_l2_content_modes():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/release.md": "Release runbook full details."}
    )
    tools = {tool.name: tool for tool in create_openviking_tools(client=client)}
    read_tool = tools["viking_read"]

    abstract = read_tool.invoke(
        {
            "uris": "viking://resources/runbooks/release.md",
            "content_mode": "abstract",
        }
    )
    overview = read_tool.invoke(
        {
            "uris": "viking://resources/runbooks/release.md",
            "content_mode": "overview",
        }
    )
    full = read_tool.invoke(
        {
            "uris": "viking://resources/runbooks/release.md",
            "content_mode": "read",
        }
    )

    assert '"content_mode": "abstract"' in abstract
    assert '"content_mode": "overview"' in overview
    assert '"content_mode": "read"' in full
    assert "Release runbook full details." in full


def test_ensure_client_defaults_to_http_client(monkeypatch):
    created = {}

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self._initialized = False

        def initialize(self):
            self._initialized = True

    import openviking.client as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", FakeHTTPClient)

    client = ensure_client(
        OpenVikingConnection(
            api_key="test-key",
            user_id="test-user",
            agent_id="test-agent",
        )
    )

    assert isinstance(client, FakeHTTPClient)
    assert client._initialized is True
    assert created["api_key"] == "test-key"
    assert created["user_id"] == "test-user"
    assert created["agent_id"] == "test-agent"
    assert created["url"] is None


def test_chat_message_history_preserves_tool_parts():
    client = InMemoryOpenVikingClient()
    history = OpenVikingChatMessageHistory(session_id="history-session", client=client)

    history.add_messages(
        [
            HumanMessage(content="Find deployment notes."),
            AIMessage(
                content="Searching.",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "viking_find",
                        "args": {"query": "deployment"},
                    }
                ],
            ),
            ToolMessage(
                content="Azure deployment notes found.",
                tool_call_id="call-1",
                name="viking_find",
            ),
        ]
    )

    stored = client.sessions["history-session"]
    assert stored[1]["parts"][1]["type"] == "tool"
    assert stored[2]["parts"][0]["tool_output"] == "Azure deployment notes found."

    restored = history.messages
    restored_ai_messages = [message for message in restored if isinstance(message, AIMessage)]
    restored_tool_messages = [message for message in restored if isinstance(message, ToolMessage)]
    assert len(restored_ai_messages) == 1
    assert len(restored_ai_messages[0].tool_calls) == 1
    assert len(restored_tool_messages) == 1


def test_openviking_tool_result_restores_without_duplicate_ai_tool_call():
    restored = openviking_message_to_langchain(
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool",
                    "tool_id": "call-1",
                    "tool_name": "viking_find",
                    "tool_output": "Azure deployment notes found.",
                    "tool_status": "completed",
                }
            ],
        }
    )

    assert len(restored) == 1
    assert isinstance(restored[0], ToolMessage)
    assert restored[0].tool_call_id == "call-1"


def test_empty_human_message_uses_empty_text_part():
    payloads = langchain_message_to_openviking(HumanMessage(content=""))

    assert payloads == [{"role": "user", "parts": [{"type": "text", "text": ""}]}]


def test_system_messages_are_never_persisted_to_openviking_history():
    payloads = langchain_message_to_openviking(
        SystemMessage(content="Never persist runtime policy."),
        persist_system_messages=True,
    )

    assert payloads == []


def test_session_context_assembler_uses_archive_active_messages_and_recall():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Azure deployments use OpenViking context."}
    )
    client.add_message("assembler-session", "user", content="Earlier user turn")
    client.add_message("assembler-session", "assistant", content="Earlier assistant turn")
    client.commit_session("assembler-session")
    client.add_message("assembler-session", "user", content="Active turn")

    assembler = OpenVikingSessionContextAssembler(
        client=client,
        target_uri="viking://resources",
    )
    assembled = assembler.assemble(session_id="assembler-session", query="azure context")

    assert client.search_calls[-1]["session_id"] == "assembler-session"
    assert "Session archive overview" in assembled.block
    assert "Earlier user turn" in assembled.block
    assert "Active session messages" in assembled.block
    assert "Active turn" in assembled.block
    assert "Azure deployments" in assembled.block
    assert assembled.context_parts[0]["type"] == "context"


def test_with_openviking_context_wraps_runnable_with_history():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Azure is the deployment color."}
    )

    def answer(messages):
        assert "Azure is the deployment color" in messages[0].content
        return AIMessage(content="OpenViking says azure.")

    runnable = with_openviking_context(
        RunnableLambda(answer),
        client=client,
        session_id="wrapped-session",
        target_uri="viking://resources",
    )

    result = runnable.invoke(
        [HumanMessage(content="What deployment color?")],
    )

    assert result.content == "OpenViking says azure."
    assert len(client.sessions["wrapped-session"]) == 2
    assert any(part["type"] == "context" for part in client.sessions["wrapped-session"][1]["parts"])


def test_with_openviking_context_dynamic_session_requires_config():
    client = InMemoryOpenVikingClient()
    runnable = with_openviking_context(
        RunnableLambda(lambda _messages: AIMessage(content="ok")),
        client=client,
    )

    with pytest.raises(ValueError, match="Missing keys .*session_id"):
        runnable.invoke([HumanMessage(content="hi")])


def test_with_openviking_context_dynamic_session_uses_configured_session():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Azure dynamic context."}
    )

    def answer(messages):
        assert "Azure dynamic context" in messages[0].content
        return AIMessage(content="dynamic ok")

    runnable = with_openviking_context(
        RunnableLambda(answer),
        client=client,
        target_uri="viking://resources",
    )

    result = runnable.invoke(
        [HumanMessage(content="What dynamic context?")],
        config={"configurable": {"session_id": "dynamic-session"}},
    )

    assert result.content == "dynamic ok"
    assert client.search_calls[-1]["session_id"] == "dynamic-session"
    assert len(client.sessions["dynamic-session"]) == 2


def test_with_openviking_context_dynamic_session_can_use_thread_id_key():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Thread dynamic context."}
    )
    runnable = with_openviking_context(
        RunnableLambda(lambda _messages: AIMessage(content="thread ok")),
        client=client,
        target_uri="viking://resources",
        session_id_config_key="thread_id",
    )

    result = runnable.invoke(
        [HumanMessage(content="What thread context?")],
        config={"configurable": {"thread_id": "thread-session"}},
    )

    assert result.content == "thread ok"
    assert client.search_calls[-1]["session_id"] == "thread-session"
    assert len(client.sessions["thread-session"]) == 2


def test_with_openviking_context_clears_pending_context_after_failure():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Azure failure context."}
    )
    calls = {"count": 0}

    def answer(_messages):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic model failure")
        return AIMessage(content="Recovered.")

    runnable = with_openviking_context(
        RunnableLambda(answer),
        client=client,
        session_id="failure-session",
        target_uri="viking://resources",
    )

    with pytest.raises(RuntimeError, match="synthetic model failure"):
        runnable.invoke([HumanMessage(content="What deployment color?")])

    client.records.clear()
    result = runnable.invoke([HumanMessage(content="No context this time.")])

    assert result.content == "Recovered."
    assistant_parts = client.sessions["failure-session"][-1]["parts"]
    assert not any(part["type"] == "context" for part in assistant_parts)


def test_with_openviking_context_dynamic_error_clears_pending_context():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Azure dynamic failure context."}
    )
    calls = {"count": 0}

    def answer(_messages):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic dynamic failure")
        return AIMessage(content="recovered")

    runnable = with_openviking_context(
        RunnableLambda(answer),
        client=client,
        target_uri="viking://resources",
    )

    with pytest.raises(RuntimeError, match="synthetic dynamic failure"):
        runnable.invoke(
            [HumanMessage(content="What dynamic failure context?")],
            config={"configurable": {"session_id": "dynamic-failure-session"}},
        )

    client.records.clear()
    result = runnable.invoke(
        [HumanMessage(content="No context now.")],
        config={"configurable": {"session_id": "dynamic-failure-session"}},
    )

    assert result.content == "recovered"
    assistant_parts = client.sessions["dynamic-failure-session"][-1]["parts"]
    assert not any(part["type"] == "context" for part in assistant_parts)


def test_archive_tools_search_and_expand_committed_session():
    client = InMemoryOpenVikingClient()
    client.add_message("archive-session", "user", content="Remember cobalt archive detail.")
    client.add_message("archive-session", "assistant", content="Cobalt archive detail stored.")
    commit = client.commit_session("archive-session")
    assert commit["archive_id"] == "archive_001"

    tools = {tool.name: tool for tool in create_openviking_tools(client=client)}
    searched = tools["viking_archive_search"].invoke(
        {
            "session_id": "archive-session",
            "archive_id": "archive_001",
            "query": "cobalt archive",
        }
    )
    expanded = tools["viking_archive_expand"].invoke(
        {"session_id": "archive-session", "archive_id": "archive_001"}
    )

    assert "Cobalt archive detail" in searched
    assert "archive_001" in expanded
    assert "Remember cobalt archive detail" in expanded


def test_archive_search_without_archive_id_searches_raw_history():
    client = InMemoryOpenVikingClient()
    client.add_message("archive-search-session", "user", content="Hidden cobalt archive detail.")
    commit = client.commit_session("archive-search-session")
    assert commit["archive_id"] == "archive_001"
    client.archives["archive-search-session"][0]["overview"] = "compressed summary"
    client.archives["archive-search-session"][0]["abstract"] = "compressed summary"

    tools = {tool.name: tool for tool in create_openviking_tools(client=client)}
    searched = tools["viking_archive_search"].invoke(
        {
            "session_id": "archive-search-session",
            "query": "hidden cobalt archive",
        }
    )

    assert "Hidden cobalt archive detail" in searched
    assert "viking://session/archive-search-session/history" in searched


def test_archive_grep_pattern_uses_backend_safe_token_regex():
    assert _archive_grep_pattern("hidden cobalt archive") == "hidden"
    assert "(?=" not in _archive_grep_pattern("hidden cobalt archive")


def test_commit_policy_commits_when_threshold_is_reached():
    client = InMemoryOpenVikingClient()
    history = OpenVikingChatMessageHistory(
        session_id="commit-session",
        client=client,
        commit_policy=OpenVikingCommitPolicy(
            mode="pending_tokens",
            pending_token_threshold=1,
        ),
    )

    history.add_messages(
        [
            HumanMessage(content="first message"),
            AIMessage(content="second message"),
        ]
    )

    assert client.archives["commit-session"]
    assert client.sessions["commit-session"] == []


def test_history_is_empty_after_commit_archives_tool_messages():
    client = InMemoryOpenVikingClient()
    history = OpenVikingChatMessageHistory(
        session_id="orphan-tool-session",
        client=client,
        commit_policy=OpenVikingCommitPolicy(
            mode="pending_tokens",
            pending_token_threshold=1,
        ),
    )

    history.add_messages(
        [
            HumanMessage(content="Find release notes."),
            AIMessage(
                content="Searching.",
                tool_calls=[
                    {
                        "id": "call-orphan",
                        "name": "viking_find",
                        "args": {"query": "release"},
                    }
                ],
            ),
            ToolMessage(
                content="Release notes found.",
                tool_call_id="call-orphan",
                name="viking_find",
            ),
        ]
    )

    assert client.sessions["orphan-tool-session"] == []
    assert history.messages == []


def test_chat_message_history_clear_raises_when_delete_fails():
    class DeleteDeniedClient(InMemoryOpenVikingClient):
        def delete_session(self, session_id: str) -> None:
            raise PermissionError(f"cannot delete {session_id}")

    client = DeleteDeniedClient()
    history = OpenVikingChatMessageHistory(session_id="clear-session", client=client)
    history.add_messages([HumanMessage(content="private stale context")])

    with pytest.raises(PermissionError, match="cannot delete clear-session"):
        history.clear()

    assert len(client.sessions["clear-session"]) == 1


def test_langgraph_store_round_trip_and_semantic_search():
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)

    store.put(
        ("users", "ada"),
        "preferences",
        {"color": "azure", "framework": "langgraph", "nested": {"rank": 3}},
    )
    store.put(("users", "ada"), "profile", {"color": "teal", "framework": "langgraph"})
    store.put(
        ("users", "ada"),
        "bad-rank",
        {"color": "navy", "framework": "langgraph", "nested": {"rank": "high"}},
    )

    item = store.get(("users", "ada"), "preferences")
    assert item.value["framework"] == "langgraph"

    filtered = store.search(("users",), filter={"nested.rank": {"$gte": 3}}, limit=5)
    assert [item.key for item in filtered] == ["preferences"]

    bad_in_filter = store.search(("users",), filter={"framework": {"$in": 3}}, limit=5)
    assert bad_in_filter == []

    semantic = store.search(("users",), query="azure", limit=5)
    assert semantic[0].namespace == ("users", "ada")
    assert semantic[0].value["color"] == "azure"

    assert store.list_namespaces(prefix=("users",)) == [("users", "ada")]


def test_langgraph_store_rejects_ttl_writes():
    store = OpenVikingStore(client=InMemoryOpenVikingClient())

    with pytest.raises(NotImplementedError, match="TTL is not supported"):
        store.put(("users", "ada"), "temporary", {"note": "expires"}, ttl=60)


def test_langgraph_store_batch_rejects_ttl_writes():
    store = OpenVikingStore(client=InMemoryOpenVikingClient())

    with pytest.raises(NotImplementedError, match="TTL is not supported"):
        store.batch(
            [
                PutOp(
                    namespace=("users", "ada"),
                    key="temporary",
                    value={"note": "expires"},
                    ttl=60,
                )
            ]
        )


@pytest.mark.parametrize(
    ("root_uri", "shorthand_prefix", "canonical_prefix"),
    [
        (
            "viking://user/memories/langgraph_store",
            "viking://user/memories",
            "viking://user/default/memories",
        ),
        (
            "viking://user/memories/langgraph_store",
            "viking://user/memories",
            "viking://user/default/agent/support/memories",
        ),
        (
            "viking://agent/memories/langgraph_store",
            "viking://agent/memories",
            "viking://agent/support/memories",
        ),
        (
            "viking://agent/memories/langgraph_store",
            "viking://agent/memories",
            "viking://agent/support/user/default/memories",
        ),
        (
            "viking://agent/skills/langgraph_store",
            "viking://agent/skills",
            "viking://agent/support/user/default/skills",
        ),
    ],
    ids=[
        "user-memory",
        "user-memory-isolated-by-agent",
        "agent-memory",
        "agent-memory-isolated-by-user",
        "agent-skills-isolated-by-user",
    ],
)
def test_langgraph_store_accepts_canonical_result_uris_for_shorthand_root(
    root_uri,
    shorthand_prefix,
    canonical_prefix,
):
    class CanonicalizingClient(InMemoryOpenVikingClient):
        def _canonicalize(self, value):
            if isinstance(value, str):
                return value.replace(f"{shorthand_prefix}/", f"{canonical_prefix}/", 1)
            if isinstance(value, list):
                return [self._canonicalize(item) for item in value]
            return value

        def write(self, uri, content, mode="replace", **kwargs):
            return super().write(self._canonicalize(uri), content, mode=mode, **kwargs)

        def read(self, uri, *args, **kwargs):
            return super().read(self._canonicalize(uri), *args, **kwargs)

        def glob(self, pattern, uri="viking://"):
            return super().glob(pattern, self._canonicalize(uri))

        def find(self, query, target_uri="", **kwargs):
            return super().find(query, self._canonicalize(target_uri), **kwargs)

        def rm(self, uri, recursive=False):
            return super().rm(self._canonicalize(uri), recursive=recursive)

    client = CanonicalizingClient()
    store = OpenVikingStore(client=client, root_uri=root_uri)

    store.put(("users", "ada"), "preferences", {"color": "azure"})

    semantic = store.search(("users",), query="azure", limit=5)

    assert semantic[0].namespace == ("users", "ada")
    assert semantic[0].key == "preferences"
    assert semantic[0].value["color"] == "azure"


def test_langgraph_store_ignores_unrelated_canonical_result_uris():
    store = OpenVikingStore(
        client=InMemoryOpenVikingClient(),
        root_uri="viking://user/memories/langgraph_store",
    )

    assert (
        store._parse_index_uri(
            "viking://user/default/agent/support/memories/other_store/index/users/ada.md"
        )
        is None
    )
    assert (
        store._parse_index_uri(
            "viking://agent/support/user/default/memories/langgraph_store/index/users/ada.md"
        )
        is None
    )


def test_langgraph_store_waits_for_indexing_by_default():
    class RecordingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.write_wait_values = []

        def write(self, *args, wait=False, **kwargs):
            self.write_wait_values.append(wait)
            return super().write(*args, wait=wait, **kwargs)

    client = RecordingClient()
    store = OpenVikingStore(client=client)

    store.put(("users",), "ada", {"color": "azure"})

    assert client.write_wait_values
    assert all(wait is True for wait in client.write_wait_values)

    async_client = RecordingClient()
    async_store = OpenVikingStore(client=async_client, wait=False)
    async_store.put(("users",), "ada", {"color": "green"})

    assert async_client.write_wait_values
    assert all(wait is False for wait in async_client.write_wait_values)


def test_langgraph_store_uses_create_first_and_preserves_created_at_on_replace():
    class RecordingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.write_modes = []

        def write(self, uri, content, mode="replace", **kwargs):
            self.write_modes.append((uri, mode))
            return super().write(uri, content, mode=mode, **kwargs)

    client = RecordingClient()
    store = OpenVikingStore(client=client)

    store.put(("users",), "ada", {"color": "azure"})
    first = store.get(("users",), "ada")
    store.put(("users",), "ada", {"color": "teal"})
    second = store.get(("users",), "ada")

    data_uri = "viking://user/memories/langgraph_store/data/users/ada.json"
    assert client.write_modes[0] == (data_uri, "create")
    assert (data_uri, "replace") in client.write_modes
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at
    assert second.value["color"] == "teal"


def test_pending_token_commit_does_not_create_missing_session():
    client = InMemoryOpenVikingClient()
    result = maybe_commit_session(
        client,
        "missing-commit-session",
        OpenVikingCommitPolicy(mode="pending_tokens", pending_token_threshold=1),
    )

    assert result is None
    assert "missing-commit-session" not in client.sessions


def test_langgraph_middleware_injects_recall_and_captures_messages():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "The user prefers azure deployments."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda state, runtime: "middleware-session",
        commit_on_after_agent=True,
    )

    captured_request = {}

    class Request:
        messages = [HumanMessage(content="What deployment color?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        captured_request["request"] = request
        return AIMessage(content="ok")

    middleware.wrap_model_call(Request(), handler)
    assert "OpenViking context" in captured_request["request"].system_message.content
    assert "azure deployments" in captured_request["request"].system_message.content

    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Remember this."),
                AIMessage(content="I will."),
            ]
        },
        runtime=None,
    )
    assert client.sessions["middleware-session"] == []
    assert client.archives["middleware-session"]
    archived_messages = client.archives["middleware-session"][0]["messages"]
    assert len(archived_messages) == 2
    assistant_parts = archived_messages[1]["parts"]
    assert any(part["type"] == "context" for part in assistant_parts)


def test_langgraph_middleware_does_not_duplicate_active_messages_in_context():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "Middleware recall uses green context."}
    )
    client.add_message(
        "middleware-active-session",
        "user",
        content="OpenViking active duplicate user turn.",
    )
    client.add_message(
        "middleware-active-session",
        "assistant",
        content="OpenViking active duplicate assistant turn.",
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda state, runtime: "middleware-active-session",
    )
    captured_request = {}

    class Request:
        messages = [HumanMessage(content="What middleware recall?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        captured_request["request"] = request
        return AIMessage(content="ok")

    middleware.wrap_model_call(Request(), handler)
    system_content = captured_request["request"].system_message.content
    assert "Middleware recall uses green context" in system_content
    assert "OpenViking active duplicate user turn" not in system_content
    assert "OpenViking active duplicate assistant turn" not in system_content


def test_langgraph_middleware_uses_runtime_thread_id():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "Runtime thread users prefer teal."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
    )
    captured_request = {}

    class Runtime:
        config = {"configurable": {"thread_id": "runtime-thread"}}

    class Request:
        state = {}
        runtime = Runtime()
        messages = [HumanMessage(content="What runtime color?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        captured_request["request"] = request
        return AIMessage(content="ok")

    middleware.wrap_model_call(Request(), handler)
    assert "Runtime thread users prefer teal" in captured_request["request"].system_message.content
    assert client.search_calls[-1]["session_id"] == "runtime-thread"

    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Remember runtime thread."),
                AIMessage(content="Stored for runtime thread."),
            ]
        },
        Runtime(),
    )
    assert len(client.sessions["runtime-thread"]) == 2


def test_langgraph_middleware_requires_explicit_session_id():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "Shared fallback should never be used."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
    )

    class Request:
        state = {}
        runtime = None
        messages = [HumanMessage(content="What context?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        return AIMessage(content="ok")

    with pytest.raises(ValueError, match="thread_id"):
        middleware.wrap_model_call(Request(), handler)

    with pytest.raises(ValueError, match="session_id"):
        middleware.after_agent(
            {
                "messages": [
                    HumanMessage(content="Remember this."),
                    AIMessage(content="Stored."),
                ]
            },
            runtime=None,
        )

    assert "langgraph-default" not in client.sessions


def test_langgraph_middleware_captures_cumulative_state_once():
    client = InMemoryOpenVikingClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-cumulative",
    )
    first_turn = [
        HumanMessage(content="Remember first turn."),
        AIMessage(content="First turn stored."),
    ]
    second_turn = first_turn + [
        HumanMessage(content="Remember second turn."),
        AIMessage(content="Second turn stored."),
    ]

    middleware.after_agent({"messages": first_turn}, runtime=None)
    middleware.after_agent({"messages": second_turn}, runtime=None)
    middleware.after_agent({"messages": second_turn}, runtime=None)

    assert [
        message["parts"][0]["text"] for message in client.sessions["middleware-cumulative"]
    ] == [
        "Remember first turn.",
        "First turn stored.",
        "Remember second turn.",
        "Second turn stored.",
    ]


def test_langgraph_middleware_captures_latest_only_state_each_turn():
    client = InMemoryOpenVikingClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-latest-only",
    )
    first_turn = [
        HumanMessage(content="Remember latest first."),
        AIMessage(content="Latest first stored."),
    ]
    replayed_first_turn = [
        HumanMessage(content="Remember latest first."),
        AIMessage(content="Latest first stored."),
    ]
    second_turn = [
        HumanMessage(content="Remember latest second."),
        AIMessage(content="Latest second stored."),
    ]

    middleware.after_agent({"messages": first_turn}, runtime=None)
    middleware.after_agent({"messages": replayed_first_turn}, runtime=None)
    middleware.after_agent({"messages": second_turn}, runtime=None)

    assert [
        message["parts"][0]["text"] for message in client.sessions["middleware-latest-only"]
    ] == [
        "Remember latest first.",
        "Latest first stored.",
        "Remember latest second.",
        "Latest second stored.",
    ]


def test_langgraph_middleware_captures_in_place_message_mutation():
    client = InMemoryOpenVikingClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-mutated",
    )
    messages = [
        HumanMessage(content="Remember mutable first."),
        AIMessage(content="Mutable first stored."),
    ]

    middleware.after_agent({"messages": messages}, runtime=None)
    messages[0].content = "Remember mutable second."
    messages[1].content = "Mutable second stored."
    middleware.after_agent({"messages": messages}, runtime=None)

    assert [message["parts"][0]["text"] for message in client.sessions["middleware-mutated"]] == [
        "Remember mutable first.",
        "Mutable first stored.",
        "Remember mutable second.",
        "Mutable second stored.",
    ]


def test_langgraph_middleware_captures_same_id_changed_content():
    client = InMemoryOpenVikingClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-same-id",
    )

    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Remember same id first.", id="user-stable"),
                AIMessage(content="Same id first stored.", id="assistant-stable"),
            ]
        },
        runtime=None,
    )
    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Remember same id second.", id="user-stable"),
                AIMessage(content="Same id second stored.", id="assistant-stable"),
            ]
        },
        runtime=None,
    )

    assert [message["parts"][0]["text"] for message in client.sessions["middleware-same-id"]] == [
        "Remember same id first.",
        "Same id first stored.",
        "Remember same id second.",
        "Same id second stored.",
    ]


def test_langgraph_middleware_signature_includes_dict_tool_output():
    first = {
        "role": "tool",
        "tool_call_id": "call-dict",
        "name": "viking_find",
        "tool_output": "first output",
    }
    second = {
        "role": "tool",
        "tool_call_id": "call-dict",
        "name": "viking_find",
        "tool_output": "second output",
    }

    assert _message_signature(first) != _message_signature(second)
    assert _message_signature(second) == _message_signature(dict(second))


def test_langgraph_middleware_clears_pending_context_on_duplicate_retry():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "Retry cleanup context."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda state, runtime: "middleware-pending-cleanup",
    )

    class Request:
        messages = [HumanMessage(content="What retry context?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        return AIMessage(content="Retry context stored.")

    middleware.wrap_model_call(Request(), handler)
    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="What retry context?"),
                AIMessage(content="Retry context stored."),
            ]
        },
        runtime=None,
    )
    middleware.wrap_model_call(Request(), handler)
    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="What retry context?"),
                AIMessage(content="Retry context stored."),
            ]
        },
        runtime=None,
    )
    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Remember unrelated turn."),
                AIMessage(content="Unrelated turn stored."),
            ]
        },
        runtime=None,
    )

    unrelated_assistant_parts = client.sessions["middleware-pending-cleanup"][-1]["parts"]
    assert unrelated_assistant_parts == [{"type": "text", "text": "Unrelated turn stored."}]
