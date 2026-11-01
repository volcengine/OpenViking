from __future__ import annotations

import asyncio
import json
import sys
from copy import deepcopy
from types import MappingProxyType
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain_core.messages import (
    AIMessage,
    ChatMessage,
    ChatMessageChunk,
    HumanMessage,
    HumanMessageChunk,
    SystemMessage,
    SystemMessageChunk,
    ToolMessage,
    convert_to_openai_messages,
)
from langchain_core.runnables import RunnableLambda
from langgraph.store.base import InvalidNamespaceError, PutOp, SearchOp
from openviking_sdk.errors import (
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    UnavailableError,
)

import langchain_openviking.client as client_helpers
from langchain_openviking import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingRetriever,
    OpenVikingSessionContextAssembler,
    OpenVikingSessionRecorder,
    OpenVikingStore,
    create_openviking_tools,
    with_openviking_context,
)
from langchain_openviking.client import (
    OpenVikingConnection,
    apply_commit_policy,
    call_openviking,
    ensure_client,
    get_latest_user_text,
)
from langchain_openviking.history import (
    langchain_message_to_openviking,
    openviking_message_to_langchain,
)
from langchain_openviking.middleware import _message_signature
from langchain_openviking.tools import _archive_grep_pattern, _resolve_resource_source


def test_langchain_client_exposes_apply_commit_policy_without_legacy_alias():
    assert hasattr(client_helpers, "apply_commit_policy")
    assert not hasattr(client_helpers, "maybe_commit_session")


def _tool_schema(tool: Any) -> dict[str, Any]:
    args_schema = tool.args_schema
    if hasattr(args_schema, "model_json_schema"):
        return args_schema.model_json_schema()
    return args_schema.schema()


def _schema_enums(schema: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    if "enum" in schema:
        values.update(str(value) for value in schema["enum"])
    for child_key in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(child_key, []):
            values.update(_schema_enums(child))
    return values


def _schema_has_description(schema: dict[str, Any]) -> bool:
    if schema.get("description"):
        return True
    return any(
        _schema_has_description(child)
        for child_key in ("anyOf", "oneOf", "allOf")
        for child in schema.get(child_key, [])
    )


def test_retriever_returns_langchain_documents():
    client = InMemoryOpenVikingClient(
        {
            "viking://~/memories/preferences.md": "The user prefers azure deploys.",
            "viking://resources/runbooks/release.md": "Release notes mention LangChain.",
        }
    )
    retriever = OpenVikingRetriever(
        client=client,
        target_uri=["viking://~/memories", "viking://resources"],
        limit=3,
    )

    docs = retriever.invoke("azure LangChain")

    assert {doc.metadata["openviking_uri"] for doc in docs} == {
        "viking://resources/runbooks/release.md",
        "viking://~/memories/preferences.md",
    }
    assert all(doc.page_content for doc in docs)


def test_retriever_keeps_peer_id_out_of_retrieval():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/preferences.md": "The peer prefers azure deploys."}
    )
    retriever = OpenVikingRetriever(
        client=client,
        target_uri="viking://~/memories",
        actor_peer_id="peer-1",
    )

    retriever.invoke("azure")

    assert "peer_id" not in client.find_calls[-1]


def test_retriever_passes_tags_to_retrieval():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/release.md": "Release notes mention LangChain."}
    )
    retriever = OpenVikingRetriever(
        client=client,
        target_uri="viking://resources",
        tags=["env=prod", "team=search"],
    )

    retriever.invoke("LangChain")

    assert client.find_calls[-1]["tags"] == ["env=prod", "team=search"]


def test_create_openviking_tools_exposes_common_viking_primitives():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "The user likes LangGraph agents."}
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
    assert "viking://~/memories/profile.md" in find_tool.invoke({"query": "LangGraph", "limit": 2})

    store_tool = next(tool for tool in tools if tool.name == "viking_store")
    assert "explicit durable memories" in store_tool.description
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

    add_resource_tool = next(tool for tool in tools if tool.name == "viking_add_resource")
    assert "resource-management operation" in add_resource_tool.description

    health_tool = next(tool for tool in tools if tool.name == "viking_health")
    health = health_tool.invoke({})
    assert '"backend": "OpenViking"' in health
    assert "VikingDB is internal vector/index storage" in health


@pytest.mark.parametrize("role", ["user", "assistant", "system", "tool"])
@pytest.mark.parametrize("text_format", ["string", "block"])
def test_viking_store_normalizes_langchain_text_blocks(role: str, text_format: str):
    content = [
        {"type": "text", "text": "Remember cobalt deployment."},
        {"type": "text", "text": "中文确认"},
    ]
    messages = {
        "user": HumanMessage(content=content),
        "assistant": AIMessage(content=content),
        "system": SystemMessage(content=content),
        "tool": ToolMessage(content=content, tool_call_id="call-normal", name="ordinary_tool"),
    }
    wire = convert_to_openai_messages([messages[role]], text_format=text_format)
    client = InMemoryOpenVikingClient()
    store = create_openviking_tools(client=client, tool_names=["viking_store"])[0]

    result = json.loads(
        store.invoke({"session_id": "text-blocks", "messages": wire, "commit": False})
    )

    assert result["messages_added"] == 1
    stored = client.sessions["text-blocks"][0]
    assert stored["role"] == ("assistant" if role in {"system", "tool"} else role)
    part = stored["parts"][0]
    assert (
        part["tool_output" if role == "tool" else "text"] == "Remember cobalt deployment.\n中文确认"
    )
    if role == "tool":
        assert part["tool_id"] == "call-normal"
        assert part["tool_name"] == "ordinary_tool"
        assert part["tool_status"] == "completed"


@pytest.mark.parametrize("content", [None, False, 0])
def test_viking_store_preserves_scalar_content(content: Any):
    client = InMemoryOpenVikingClient()
    store = create_openviking_tools(client=client, tool_names=["viking_store"])[0]

    store.invoke(
        {
            "session_id": "scalar",
            "messages": [{"role": "user", "content": content}],
            "commit": False,
        }
    )

    assert client.sessions["scalar"][0]["parts"] == [{"type": "text", "text": str(content)}]


@pytest.mark.parametrize("role", ["user", "system", "tool"])
def test_viking_store_preserves_native_parts(role: str):
    client = InMemoryOpenVikingClient()
    store = create_openviking_tools(client=client, tool_names=["viking_store"])[0]
    parts = [{"type": "text", "text": "explicit parts control"}]

    store.invoke(
        {
            "session_id": "native-parts",
            "messages": [
                {"role": role, "content": [{"type": "text", "text": "unused"}], "parts": parts}
            ],
            "commit": False,
        }
    )

    stored = client.sessions["native-parts"][0]
    assert stored["role"] == ("assistant" if role in {"system", "tool"} else role)
    assert stored["parts"] == parts


def test_create_openviking_tools_uses_peer_id_only_for_message_attribution():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "The peer likes LangGraph agents."}
    )
    tools = {
        tool.name: tool for tool in create_openviking_tools(client=client, peer_id="peer-tools")
    }

    assert "peer_id" not in _tool_schema(tools["viking_find"]).get("properties", {})

    tools["viking_find"].invoke({"query": "LangGraph"})
    tools["viking_search"].invoke({"query": "LangGraph", "session_id": "tool-session"})
    tools["viking_store"].invoke(
        {
            "messages": [{"role": "user", "content": "Remember peer tools."}],
            "commit": False,
        }
    )

    assert "peer_id" not in client.find_calls[-1]
    assert "peer_id" not in client.search_calls[-1]
    assert any(
        messages and messages[0].get("peer_id") == "peer-tools"
        for messages in client.sessions.values()
    )


def test_add_resource_resolves_existing_local_file_for_client_upload(tmp_path):
    class RecordingClient:
        def __init__(self):
            self.paths = []

        def add_resource(self, path, **_kwargs):
            self.paths.append(path)
            return {"uri": "viking://resources/report.md"}

    client = RecordingClient()
    resource_file = tmp_path / "report.md"
    resource_file.write_text("deployment notes", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_openviking_tools(
            client=client,
            tool_names=["viking_add_resource"],
        )
    }

    result = tools["viking_add_resource"].invoke({"path": str(resource_file)})

    assert client.paths == [str(resource_file)]
    assert "viking://resources/report.md" in result


def test_resolve_resource_source_handles_local_and_remote_sources(tmp_path):
    local_file = tmp_path / "resource with spaces.md"
    local_file.write_text("notes", encoding="utf-8")

    assert (
        _resolve_resource_source("https://example.com/repo.git") == "https://example.com/repo.git"
    )
    assert _resolve_resource_source("git@github.com:org/repo.git") == "git@github.com:org/repo.git"
    assert (
        _resolve_resource_source("ssh://git@github.com/org/repo.git")
        == "ssh://git@github.com/org/repo.git"
    )
    assert _resolve_resource_source("github.com/org/repo") == "github.com/org/repo"
    assert _resolve_resource_source(f"file://{local_file}") == str(local_file)
    assert (
        _resolve_resource_source("file://remote-host/tmp/report.md")["error"]
        == "unsupported_file_uri"
    )

    missing = _resolve_resource_source(str(tmp_path / "missing.md"))
    assert missing["error"] == "local_path_not_found"
    assert _resolve_resource_source("docs/missing.md")["error"] == "local_path_not_found"


def test_add_resource_keeps_explicit_local_clients_supported(tmp_path):
    client = InMemoryOpenVikingClient()
    resource_file = tmp_path / "report.md"
    resource_file.write_text("report", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_openviking_tools(
            client=client,
            tool_names=["viking_add_resource"],
        )
    }

    result = tools["viking_add_resource"].invoke({"path": str(resource_file)})

    assert "viking://resources/report.md" in result
    assert "viking://resources/report.md" in client.records


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
    uppercase = read_tool.invoke(
        {
            "uris": "viking://resources/runbooks/release.md",
            "content_mode": "READ",
        }
    )

    assert '"content_mode": "abstract"' in abstract
    assert '"content_mode": "overview"' in overview
    assert '"content_mode": "read"' in full
    assert '"content_mode": "read"' in uppercase
    assert "Release runbook full details." in full


def test_openviking_read_tool_schema_describes_file_and_directory_contract():
    client = InMemoryOpenVikingClient()
    tools = {tool.name: tool for tool in create_openviking_tools(client=client)}
    read_tool = tools["viking_read"]
    browse_tool = tools["viking_browse"]

    read_schema = _tool_schema(read_tool)
    content_mode_schema = read_schema["properties"]["content_mode"]

    assert "Directory URIs are not readable" in read_tool.description
    assert "file/document" in read_schema["properties"]["uris"]["description"]
    assert "viking_browse" in read_tool.description
    assert _schema_enums(content_mode_schema) == {"abstract", "overview", "read"}
    assert "List child entries" in browse_tool.description
    assert "namespace or directory" in _tool_schema(browse_tool)["properties"]["uri"]["description"]


def test_openviking_tool_schemas_describe_model_visible_arguments():
    client = InMemoryOpenVikingClient()
    tools = create_openviking_tools(client=client, profile="admin", allow_forget=True)

    for tool in tools:
        schema = _tool_schema(tool)
        for name, property_schema in schema.get("properties", {}).items():
            assert _schema_has_description(property_schema), (
                f"{tool.name}.{name} lacks a description"
            )


@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        ({"status": "degraded"}, "degraded"),
        ({"is_healthy": True}, "healthy"),
        ({"is_healthy": False}, "unhealthy"),
        ({"healthy": False, "is_healthy": True}, "unhealthy"),
        ({"ok": True, "is_healthy": False}, "healthy"),
        ({"status": "degraded", "is_healthy": True}, "degraded"),
        ({"state": "ready", "is_healthy": False}, "healthy"),
        ({"is_healthy": "true"}, "unknown"),
    ],
)
def test_openviking_health_tool_returns_safe_summary(status, expected_state):
    class StatusClient(InMemoryOpenVikingClient):
        def get_status(self) -> dict[str, Any]:
            return {
                **status,
                "state_detail": "service is starting up and loading initial data from the persistence layer",
                "hostname": "internal-openviking.local",
                "database_url": "postgres://user:secret@example.internal/db",
                "errors": ["postgres://user:secret@example.internal/db"],
                "components": {"storage": {"status": "ok"}},
            }

    tools = {tool.name: tool for tool in create_openviking_tools(client=StatusClient())}
    payload = json.loads(tools["viking_health"].invoke({}))

    assert payload["backend"] == "OpenViking"
    assert payload["state"] == expected_state
    assert payload["healthy"] is (expected_state == "healthy")
    assert payload["summary"] == {
        **status,
        "state_detail": "service is starting up and loading initial data from the pers...",
        "component_count": 1,
    }
    assert "internal-openviking.local" not in json.dumps(payload)
    assert "postgres://" not in json.dumps(payload)


def test_openviking_read_directory_uri_returns_recoverable_tool_result_for_all_modes():
    class DirectoryReadClient(InMemoryOpenVikingClient):
        def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
            raise InvalidArgumentError(
                f"Directory cannot be used here: {uri}",
                details={"resource": uri, "expected": "file", "actual": "directory"},
            )

    client = DirectoryReadClient()
    tools = {tool.name: tool for tool in create_openviking_tools(client=client)}

    for content_mode in ("abstract", "overview", "read"):
        result = tools["viking_read"].invoke(
            {
                "uris": "viking://user/default/memories/events/2026/05/09",
                "content_mode": content_mode,
            }
        )

        payload = json.loads(result)
        assert payload == [
            {
                "uri": "viking://user/default/memories/events/2026/05/09",
                "content_mode": content_mode,
                "error": "directory_uri_not_readable",
                "message": (
                    "This URI is a directory. Use viking_browse on this URI to list children, "
                    "then call viking_read on file/document URIs."
                ),
            }
        ]


def test_ensure_client_defaults_to_http_client(monkeypatch):
    created = {}

    class FakeHTTPClient:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self._initialized = False

        def initialize(self):
            self._initialized = True

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", FakeHTTPClient)

    client = ensure_client(
        OpenVikingConnection(
            api_key="test-key",
            user_id="test-user",
        )
    )

    assert client._initialized is True
    assert isinstance(client.get(), FakeHTTPClient)
    assert created["api_key"] == "test-key"
    assert created["user_id"] == "test-user"
    assert created["url"] is None


def test_openviking_client_retries_recoverable_read_with_fresh_client(monkeypatch):
    instances = []

    class FlakyHTTPClient:
        def __init__(self, **_kwargs):
            self.index = len(instances)
            self.closed = False
            self._initialized = False
            instances.append(self)

        def initialize(self):
            self._initialized = True

        def close(self):
            self.closed = True

        def find(self, **_kwargs):
            if self.index == 0:
                raise ConnectionError("OpenViking server was not ready")
            return {
                "memories": [
                    {
                        "uri": "viking://user/default/memories/profile.md",
                        "abstract": "OpenViking recovered",
                        "overview": "OpenViking recovered",
                    }
                ],
                "resources": [],
                "skills": [],
            }

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", FlakyHTTPClient)

    client = ensure_client(OpenVikingConnection(url="http://localhost:1933"))
    result = call_openviking(client, "find", query="recover")

    assert result["memories"][0]["abstract"] == "OpenViking recovered"
    assert len(instances) == 2
    assert instances[0].closed is True
    assert instances[1]._initialized is True


def test_openviking_client_handle_filters_kwargs_for_direct_calls(monkeypatch):
    class FakeHTTPClient:
        def __init__(self, **_kwargs):
            self._initialized = False

        def initialize(self):
            self._initialized = True

        def find(self, query):
            return {"query": query}

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", FakeHTTPClient)

    client = ensure_client(OpenVikingConnection(url="http://localhost:1933"))
    result = client.find("recover", unsupported="ignored")

    assert result == {"query": "recover"}


def test_call_openviking_adapts_flat_kwargs_to_sdk_options():
    calls = []

    class OptionsClient:
        def find(self, query, options=None):
            calls.append(("find", query, options))
            return {"query": query}

        def create_session(self, options=None):
            calls.append(("create_session", options))
            return {"session_id": options["session_id"]}

        def add_message(self, session_id, message):
            calls.append(("add_message", session_id, message))
            return {"ok": True}

        def write(self, uri, content, options=None):
            calls.append(("write", uri, content, options))
            return {"ok": True}

    client = OptionsClient()

    call_openviking(
        client,
        "find",
        query="recover",
        target_uri="viking://resources",
        limit=5,
    )
    call_openviking(client, "create_session", session_id="session-1")
    call_openviking(
        client,
        "add_message",
        session_id="session-1",
        role="user",
        content="hello",
    )
    call_openviking(
        client,
        "write",
        uri="viking://resources/a.md",
        content="hello",
        mode="replace",
        wait=False,
    )

    assert calls == [
        (
            "find",
            "recover",
            {"target_uri": "viking://resources", "limit": 5},
        ),
        ("create_session", {"session_id": "session-1"}),
        (
            "add_message",
            "session-1",
            {"role": "user", "content": "hello"},
        ),
        (
            "write",
            "viking://resources/a.md",
            "hello",
            {"mode": "replace", "wait": False},
        ),
    ]


def test_openviking_client_evicts_but_does_not_retry_mutating_call(monkeypatch):
    instances = []

    class FlakyHTTPClient:
        def __init__(self, **_kwargs):
            self.closed = False
            self._initialized = False
            instances.append(self)

        def initialize(self):
            self._initialized = True

        def close(self):
            self.closed = True

        def add_message(self, **_kwargs):
            raise ConnectionError("OpenViking connection dropped during write")

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", FlakyHTTPClient)

    client = ensure_client(OpenVikingConnection(url="http://localhost:1933"))
    with pytest.raises(ConnectionError):
        call_openviking(
            client,
            "add_message",
            session_id="session-1",
            role="user",
            parts=[{"type": "text", "text": "hello"}],
        )

    assert len(instances) == 1
    assert instances[0].closed is True


def test_retriever_recovers_from_stale_cached_remote_client(monkeypatch):
    instances = []

    class FlakyHTTPClient:
        def __init__(self, **_kwargs):
            self.index = len(instances)
            self.closed = False
            self._initialized = False
            instances.append(self)

        def initialize(self):
            self._initialized = True

        def close(self):
            self.closed = True

        def find(self, **_kwargs):
            if self.index == 0:
                raise RuntimeError("Event loop is closed")
            return {
                "memories": [],
                "resources": [
                    {
                        "uri": "viking://resources/recovered.md",
                        "level": 1,
                        "abstract": "Recovered resource",
                        "overview": "Recovered resource overview",
                        "score": 1.0,
                    }
                ],
                "skills": [],
            }

    import openviking_sdk as client_module

    monkeypatch.setattr(client_module, "SyncHTTPClient", FlakyHTTPClient)

    retriever = OpenVikingRetriever(url="http://localhost:1933")
    docs = retriever.invoke("recover")

    assert [doc.metadata["openviking_uri"] for doc in docs] == ["viking://resources/recovered.md"]
    assert len(instances) == 2
    assert instances[0].closed is True


def test_in_memory_openviking_client_batch_add_messages_records_messages():
    client = InMemoryOpenVikingClient()

    result = client.batch_add_messages(
        "batch-session",
        [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": "hi"}],
                "peer_id": "agent-1",
                "created_at": "2026-05-26T00:00:00+00:00",
            },
        ],
    )

    assert result["session_id"] == "batch-session"
    assert result["message_count"] == 2
    assert result["added"] == 2
    assert result["pending_tokens"] > 0
    assert [message["role"] for message in client.sessions["batch-session"]] == [
        "user",
        "assistant",
    ]
    assert client.sessions["batch-session"][0]["parts"][0]["text"] == "hello"
    assert client.sessions["batch-session"][1]["parts"][0]["text"] == "hi"
    assert client.sessions["batch-session"][1]["peer_id"] == "agent-1"
    assert client.sessions["batch-session"][1]["created_at"] == "2026-05-26T00:00:00+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "second_query,new_turn,second_output",
    [
        ("deployment", True, "deployment notes found."),
        ("Tokyo", True, "Tokyo notes found."),
        ("Tokyo", False, "Tokyo notes found."),
        ("deployment", False, "done"),
        ("deployment", False, "deployment notes found."),
        ("deployment", False, None),
    ],
)
async def test_chat_message_history_preserves_tool_parts(
    asynchronous, second_query, new_turn, second_output
):
    client = InMemoryOpenVikingClient()
    history = OpenVikingChatMessageHistory(
        session_id="history-session", client=client, async_client=client
    )

    messages = []
    outputs = ["deployment notes found.", second_output]
    for index, (query, output) in enumerate(
        zip(("deployment", second_query), outputs, strict=True)
    ):
        if index == 0 or new_turn:
            messages.append(HumanMessage(content=f"Find {query} notes."))
        messages.extend(
            [
                AIMessage(
                    content="Searching.",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "name": "viking_find",
                            "args": {"query": query},
                        }
                    ],
                ),
            ]
        )
        if output is not None:
            messages.append(ToolMessage(content=output, tool_call_id="call-1", name="viking_find"))
    try:
        if asynchronous:
            await history.aadd_messages(messages)
            restored = await history.aget_messages()
        else:
            history.add_messages(messages)
            restored = history.messages
    finally:
        await history.aclose()

    stored = client.sessions["history-session"]
    assert stored[1]["parts"][1]["type"] == "tool"
    assert stored[2]["parts"][0]["tool_output"] == "deployment notes found."

    assert [type(message) for message in restored] == (
        [HumanMessage, AIMessage, ToolMessage]
        + ([HumanMessage] if new_turn else [])
        + [AIMessage]
        + ([ToolMessage] if second_output is not None else [])
    )
    restored_ai_messages = [message for message in restored if isinstance(message, AIMessage)]
    restored_tool_messages = [message for message in restored if isinstance(message, ToolMessage)]
    assert [message.tool_calls for message in restored_ai_messages] == [
        [{"id": "call-1", "name": "viking_find", "args": {"query": query}, "type": "tool_call"}]
        for query in ("deployment", second_query)
    ]
    expected_outputs = [output for output in outputs if output is not None]
    assert [message.tool_call_id for message in restored_tool_messages] == ["call-1"] * len(
        expected_outputs
    )
    assert [message.content for message in restored_tool_messages] == expected_outputs


def test_chat_message_history_writes_peer_id_to_messages():
    client = InMemoryOpenVikingClient()
    history = OpenVikingChatMessageHistory(
        session_id="peer-history-session",
        client=client,
        peer_id="peer-history",
    )

    history.add_messages(
        [
            HumanMessage(content="Remember peer history."),
            AIMessage(content="Stored."),
        ]
    )

    assert [message.get("peer_id") for message in client.sessions["peer-history-session"]] == [
        "peer-history",
        "peer-history",
    ]


@pytest.mark.parametrize(
    "fields",
    [{}, {"tool_input": None}, {"tool_input": {"a": 1}, "tool_name": ""}],
)
def test_openviking_tool_result_restores_without_duplicate_ai_tool_call(fields):
    message = {
        "role": "assistant",
        "parts": [
            {
                "type": "tool",
                "tool_id": "call-1",
                "tool_name": "viking_find",
                "tool_output": "Azure deployment notes found.",
                "tool_status": "completed",
                **fields,
            }
        ],
    }
    restored = openviking_message_to_langchain(message)

    assert len(restored) == 1
    assert isinstance(restored[0], ToolMessage)
    assert restored[0].tool_call_id == "call-1"
    client = InMemoryOpenVikingClient()
    client.add_message("orphan-result", "assistant", parts=message["parts"])
    history = OpenVikingChatMessageHistory("orphan-result", client=client)
    try:
        assert history.messages == []
    finally:
        history.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "followup",
    [
        "none",
        "duplicate",
        "next_turn",
        "approval",
        "updated_output",
        "updated_status",
        "coalesced_poll",
    ],
)
@pytest.mark.parametrize("tool_input", [{"release": "stable"}, {}])
@pytest.mark.parametrize("status,output", [("completed", "Friday"), ("error", "")])
async def test_history_restores_completed_tool_part_with_input(
    asynchronous, followup, tool_input, status, output
):
    client = InMemoryOpenVikingClient()
    completed = {
        "type": "tool",
        "tool_id": "release-2",
        "tool_name": "read_notes",
        "tool_input": tool_input,
        "tool_output": output,
        "tool_status": status,
    }
    initial = deepcopy(completed)
    if followup == "approval":
        initial.pop("tool_output")
        initial["tool_status"] = "pending"
    following = deepcopy(completed)
    if followup == "updated_output":
        following["tool_output"] = f"{output} updated"
    elif followup == "updated_status":
        following["tool_status"] = "error" if status == "completed" else "completed"
    initial_parts = [initial]
    following_parts = [following]
    if followup == "coalesced_poll":
        pending = {**completed, "tool_status": "pending"}
        pending.pop("tool_output")
        initial_parts.insert(0, deepcopy(pending))
        following_parts.insert(0, deepcopy(pending))
    client.add_message(
        "combined-tool",
        "assistant",
        parts=initial_parts,
    )
    if followup != "none":
        if followup in ("next_turn", "approval"):
            client.add_message(
                "combined-tool",
                "user",
                content="Approved." if followup == "approval" else "Run the same tool again.",
            )
        for _ in range(2 if followup == "approval" else 1):
            client.add_message("combined-tool", "assistant", parts=deepcopy(following_parts))
    history = OpenVikingChatMessageHistory(
        session_id="combined-tool", client=client, async_client=client
    )
    stored = deepcopy(client.sessions["combined-tool"])
    try:
        messages = await history.aget_messages() if asynchronous else history.messages
    finally:
        await history.aclose()

    expected_types = {
        "next_turn": [AIMessage, ToolMessage, HumanMessage, AIMessage, ToolMessage],
        "approval": [AIMessage, HumanMessage, ToolMessage],
        "updated_output": [AIMessage, ToolMessage, AIMessage, ToolMessage],
        "updated_status": [AIMessage, ToolMessage, AIMessage, ToolMessage],
        "coalesced_poll": [AIMessage, ToolMessage, AIMessage, ToolMessage],
    }.get(followup, [AIMessage, ToolMessage])
    assert [type(message) for message in messages] == expected_types
    ai = [message for message in messages if isinstance(message, AIMessage)][-1]
    tool = messages[-1]
    assert isinstance(ai, AIMessage)
    assert ai.tool_calls[0]["id"] == "release-2"
    assert ai.tool_calls[0]["name"] == "read_notes"
    assert ai.tool_calls[0]["args"] == tool_input
    assert isinstance(tool, ToolMessage)
    assert tool.tool_call_id == "release-2"
    assert tool.content == following["tool_output"]
    assert tool.status == ("error" if following["tool_status"] == "error" else "success")
    assert client.sessions["combined-tool"] == stored


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("tool_input", [{"a": 1}, {}])
@pytest.mark.parametrize(
    "layout", ["split", "coalesced", "mixed", "result_only", "approved_result_only"]
)
async def test_history_does_not_duplicate_pending_call_for_completed_tool_part(
    asynchronous, tool_input, layout
):
    client = InMemoryOpenVikingClient()
    approved = layout == "approved_result_only"
    if approved:
        layout = "result_only"
    pending = {
        "type": "tool",
        "tool_id": "c1",
        "tool_name": "read_notes",
        "tool_input": tool_input,
        "tool_status": "pending",
    }
    completed = {**pending, "tool_status": "completed", "tool_output": "ok"}
    if layout == "result_only":
        completed.pop("tool_input")
    if layout != "coalesced":
        client.add_message("split-tool", "assistant", parts=[pending])
    if approved:
        client.add_message("split-tool", "user", content="Approved.")
    parts = [pending, completed] if layout == "coalesced" else [completed]
    if layout in ("mixed", "result_only"):
        parts.append({"type": "text", "text": "Lookup complete."})
    if layout == "mixed":
        parts.append({**completed, "tool_id": "c2"})
    client.add_message("split-tool", "assistant", parts=parts)
    history = OpenVikingChatMessageHistory(
        session_id="split-tool", client=client, async_client=client
    )
    stored = deepcopy(client.sessions["split-tool"])
    try:
        messages = await history.aget_messages() if asynchronous else history.messages
    finally:
        await history.aclose()
    expected_types = [AIMessage, ToolMessage] * (2 if layout == "mixed" else 1)
    if layout == "result_only":
        expected_types.append(AIMessage)
    if approved:
        expected_types.insert(1, HumanMessage)
    assert [type(message) for message in messages] == expected_types
    assert [
        call["id"]
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ] == (["c1", "c2"] if layout == "mixed" else ["c1"])
    assert [message.tool_call_id for message in messages if isinstance(message, ToolMessage)] == (
        ["c1", "c2"] if layout == "mixed" else ["c1"]
    )
    if layout in ("mixed", "result_only"):
        assert messages[3 if approved else 2].content == "Lookup complete."
    assert client.sessions["split-tool"] == stored


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("earlier_result", ["pending", "combined", "result_only"])
async def test_history_keeps_new_combined_call_when_an_older_call_reuses_its_id(
    asynchronous, earlier_result
):
    client = InMemoryOpenVikingClient()
    pending = {
        "type": "tool",
        "tool_id": "call_0",
        "tool_name": "weather",
        "tool_input": {"city": "Paris"},
        "tool_status": "pending",
    }
    client.add_message("overlapping-id", "assistant", parts=[pending])
    client.add_message("overlapping-id", "user", content="Check Tokyo instead.")
    if earlier_result != "pending":
        result = {**pending, "tool_status": "completed", "tool_output": "18C"}
        if earlier_result == "result_only":
            result.pop("tool_input")
        for _ in range(2):
            client.add_message("overlapping-id", "assistant", parts=deepcopy([result]))
    client.add_message(
        "overlapping-id",
        "assistant",
        parts=[
            {
                **pending,
                "tool_input": {"city": "Tokyo"},
                "tool_status": "completed",
                "tool_output": "22C",
            }
        ],
    )
    stored = deepcopy(client.sessions["overlapping-id"])
    history = OpenVikingChatMessageHistory("overlapping-id", client=client, async_client=client)
    try:
        messages = await history.aget_messages() if asynchronous else history.messages
    finally:
        await history.aclose()
    assert [type(message) for message in messages] == (
        [AIMessage, HumanMessage]
        + ([] if earlier_result == "pending" else [ToolMessage])
        + [AIMessage, ToolMessage]
    )
    assert messages[0].tool_calls[0]["args"] == {"city": "Paris"}
    assert messages[-2].tool_calls[0]["args"] == {"city": "Tokyo"}
    assert [message.content for message in messages if isinstance(message, ToolMessage)] == (
        ["22C"] if earlier_result == "pending" else ["18C", "22C"]
    )
    assert client.sessions["overlapping-id"] == stored


def test_empty_human_message_uses_empty_text_part():
    payloads = langchain_message_to_openviking(HumanMessage(content=""))

    assert payloads == [{"role": "user", "parts": [{"type": "text", "text": ""}]}]


def test_system_messages_are_never_persisted_to_openviking_history():
    payloads = langchain_message_to_openviking(
        SystemMessage(content="Never persist runtime policy."),
        persist_system_messages=True,
    )

    assert payloads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["history", "ahistory", "recorder", "arecorder"])
@pytest.mark.parametrize("message_type", [ChatMessage, ChatMessageChunk])
@pytest.mark.parametrize(
    "role,expected",
    [
        ("user", "user"),
        ("human", "user"),
        ("system", None),
        ("developer", None),
        ("assistant", "assistant"),
    ],
)
async def test_generic_chat_message_roles_in_session_history(entry, message_type, role, expected):
    client = InMemoryOpenVikingClient()
    message = message_type(role=role, content="Role-specific conversation text.")
    history = OpenVikingChatMessageHistory("chat-roles", client=client, async_client=client)
    recorder = OpenVikingSessionRecorder(client=client, async_client=client)
    try:
        if entry == "history":
            history.add_messages([message])
        elif entry == "ahistory":
            await history.aadd_messages([message])
        elif entry == "recorder":
            recorder.record("chat-roles", [message])
        else:
            await recorder.arecord("chat-roles", [message])
        expected_roles = [] if expected is None else [expected]
        assert [item["role"] for item in client.sessions["chat-roles"]] == expected_roles
        restored = await history.aget_messages()
        assert [item.type for item in restored] == [
            "human" if value == "user" else "ai" for value in expected_roles
        ]
        assert [item.content for item in restored] == [message.content] * len(expected_roles)
    finally:
        await history.aclose()
        await recorder.aclose()


@pytest.mark.parametrize(
    "message,expected",
    [
        (ChatMessage(role="user", content="Current question."), "Current question."),
        (ChatMessage(role="human", content="Current question."), "Current question."),
        (ChatMessageChunk(role="human", content="Current question."), "Current question."),
        ({"type": "chat", "role": "human", "content": "Current question."}, "Current question."),
        ({"type": "chat", "role": "user", "content": "Current question."}, "Current question."),
        (ChatMessage(role="system", content="Runtime policy."), "Earlier question."),
        (ChatMessage(role="developer", content="Runtime policy."), "Earlier question."),
        (ChatMessageChunk(role="developer", content="Runtime policy."), "Earlier question."),
        ({"type": "chat", "role": "developer", "content": "Runtime policy."}, "Earlier question."),
        (ChatMessage(role="critic", content="Critique."), "Earlier question."),
        (HumanMessage(content="Current question.", role="system"), "Current question."),
        (HumanMessageChunk(content="Current question.", role="system"), "Current question."),
        (SystemMessageChunk(content="Runtime policy.", role="user"), "Earlier question."),
        (ChatMessage(role="user", content=""), "Earlier question."),
    ],
)
def test_generic_chat_message_roles_in_recall_query(message, expected):
    assert get_latest_user_text([HumanMessage(content="Earlier question."), message]) == expected


@pytest.mark.parametrize("metadata_prefix", ["openviking", "recall", "", "my_recall"])
@pytest.mark.parametrize(
    "uri, context_type",
    [
        ("viking://resources/runbooks/deploy.md", "resource"),
        ("viking://user/default/memories/preferences/deploy.md", "memory"),
        ("viking://skills/deploy.md", "skill"),
    ],
)
def test_session_context_assembler_uses_archive_active_messages_and_recall(
    metadata_prefix, uri, context_type
):
    content = "Azure deployments use OpenViking context. " * 20
    client = InMemoryOpenVikingClient({uri: content})
    client.add_message("assembler-session", "user", content="Earlier user turn")
    client.add_message("assembler-session", "assistant", content="Earlier assistant turn")
    client.commit_session("assembler-session", enable_working_memory=True)
    client.add_message("assembler-session", "user", content="Active turn")

    assembler = OpenVikingSessionContextAssembler(
        include_session_context=True,
        client=client,
        retriever=OpenVikingRetriever(
            client=client, target_uri=uri, metadata_prefix=metadata_prefix, search_mode="search"
        ),
    )
    assembled = assembler.assemble(session_id="assembler-session", query="azure context")

    assert client.search_calls[-1]["session_id"] == "assembler-session"
    assert "Session archive overview" in assembled.block
    assert "Earlier user turn" in assembled.block
    assert "Active session messages" in assembled.block
    assert "Active turn" in assembled.block
    assert "Azure deployments" in assembled.block
    assert assembled.context_parts == [
        {"type": "context", "uri": uri, "context_type": context_type, "abstract": content[:240]}
    ]
    recorder = OpenVikingSessionRecorder(client=client)
    recorder.record(
        "assembler-session",
        [HumanMessage(content="Deployment question"), AIMessage(content="Deployment answer")],
        context_parts=assembled.context_parts,
    )
    assert [
        part
        for message in client.sessions["assembler-session"][-2:]
        for part in message["parts"]
        if part["type"] == "context"
    ] == assembled.context_parts
    asyncio.run(recorder.aclose())
    asyncio.run(assembler.aclose())


def test_session_context_assembler_keeps_peer_id_out_of_recall():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/peer.md": "Peer memory mentions azure."}
    )
    assembler = OpenVikingSessionContextAssembler(
        client=client,
        target_uri="viking://~/memories",
        actor_peer_id="peer-assembler",
    )

    assembler.assemble(session_id="assembler-peer-session", query="azure")

    assert "peer_id" not in client.search_calls[-1]


def test_session_context_assembler_creates_only_after_not_found():
    class RecordingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.create_calls = 0
            self.context_calls = 0

        def create_session(self, session_id=None):
            self.create_calls += 1
            return super().create_session(session_id=session_id)

        def get_session_context(self, session_id, token_budget=128_000, **kwargs):
            self.context_calls += 1
            return super().get_session_context(
                session_id,
                token_budget=token_budget,
                **kwargs,
            )

    client = RecordingClient()
    assembler = OpenVikingSessionContextAssembler(
        include_session_context=True,
        client=client,
        include_recall=False,
    )

    assembler.assemble(session_id="read-before-create", query="")
    assembler.assemble(session_id="read-before-create", query="")

    assert client.context_calls == 2
    assert client.create_calls == 1
    assert "read-before-create" in client.sessions


def test_session_context_assembler_creates_on_code_not_found():
    class ForeignNotFound(Exception):
        def __init__(self):
            super().__init__("session missing")
            self.code = "NOT_FOUND"

    class RecordingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.create_calls = 0

        def create_session(self, session_id=None):
            self.create_calls += 1
            return super().create_session(session_id=session_id)

        def get_session_context(self, session_id, token_budget=128_000, **kwargs):
            if session_id not in self.sessions:
                raise ForeignNotFound()
            return super().get_session_context(
                session_id,
                token_budget=token_budget,
                **kwargs,
            )

    client = RecordingClient()
    assembler = OpenVikingSessionContextAssembler(
        client=client,
        include_recall=False,
    )

    assembler.assemble(session_id="foreign-not-found", query="")

    assert client.create_calls == 1
    assert "foreign-not-found" in client.sessions


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


def test_with_openviking_context_dynamic_peer_uses_config_for_recall_and_history():
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Thread peer dynamic context."}
    )
    runnable = with_openviking_context(
        RunnableLambda(lambda _messages: AIMessage(content="peer ok")),
        client=client,
        target_uri="viking://resources",
        session_id_config_key="thread_id",
    )

    result = runnable.invoke(
        [HumanMessage(content="What peer context?")],
        config={"configurable": {"thread_id": "thread-peer-session", "peer_id": "peer-dynamic"}},
    )

    assert result.content == "peer ok"
    assert client.search_calls[-1]["session_id"] == "thread-peer-session"
    assert "peer_id" not in client.search_calls[-1]
    assert [message.get("peer_id") for message in client.sessions["thread-peer-session"]] == [
        "peer-dynamic",
        "peer-dynamic",
    ]


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
    assert "viking://user/default/sessions/archive-search-session/history" in searched


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


@pytest.mark.parametrize(
    "invalid_namespace",
    [(), ("users", "", "ada"), ("users", 1), ("users", "ada.profile"), ("langgraph", "ada")],
)
def test_langgraph_store_round_trip_and_semantic_search(invalid_namespace):
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

    records = dict(client.records)
    for value in ({"color": "invalid"}, None):
        with pytest.raises(InvalidNamespaceError):
            store.put(invalid_namespace, "preferences", value)
        assert client.records == records
        with pytest.raises(InvalidNamespaceError):
            asyncio.run(store.aput(invalid_namespace, "preferences", value))
        assert client.records == records

    # A None value is a deletion in LangGraph's BaseStore contract.
    store.put(("users", "ada"), "profile", None)
    assert store.get(("users", "ada"), "profile") is None
    assert "profile" not in {found.key for found in store.search(("users",), limit=5)}

    # Deleting a missing key is idempotent and must not create a record.
    store.put(("users", "ada"), "absent", None)
    assert store.get(("users", "ada"), "absent") is None

    store.put(("users", "ada"), "preferences", None)
    store.put(("users", "ada"), "bad-rank", None)
    assert store.search(("users",), limit=5) == []
    assert store.list_namespaces(prefix=("users",)) == []


@pytest.mark.parametrize("method", ["put", "aput", "batch", "abatch", "delete", "adelete"])
@pytest.mark.parametrize("existing", [False, True])
def test_langgraph_store_none_deletes_through_public_store_methods(
    method: str, existing: bool
) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada", "memories")
    other_namespace = ("users", "grace", "memories")
    store.put(other_namespace, "keep", {"text": "independent memory"})
    if existing:
        store.put(namespace, "item", {"text": "memory to remove"})

    if method == "put":
        store.put(namespace, "item", None)
    elif method == "aput":
        asyncio.run(store.aput(namespace, "item", None))
    elif method == "batch":
        store.batch([PutOp(namespace, "item", None)])
    elif method == "abatch":
        asyncio.run(store.abatch([PutOp(namespace, "item", None)]))
    elif method == "delete":
        store.delete(namespace, "item")
    else:
        asyncio.run(store.adelete(namespace, "item"))

    assert store.get(namespace, "item") is None
    assert store.search(namespace) == []
    assert store.search(namespace, query="memory to remove") == []
    assert store.list_namespaces() == [other_namespace]
    assert store.get(other_namespace, "keep").value == {"text": "independent memory"}


@pytest.mark.parametrize("index", [False, ["text"]])
@pytest.mark.parametrize("existing", [False, True])
def test_langgraph_store_put_none_deletes_with_index_overrides(
    index: bool | list[str], existing: bool
) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada")
    if existing:
        store.put(namespace, "item", {"text": "memory to remove"})

    store.put(namespace, "item", None, index=index)

    assert store.get(namespace, "item") is None
    assert store.search(namespace) == []
    assert store.search(namespace, query="memory to remove") == []
    assert store.list_namespaces() == []


@pytest.mark.parametrize("method", ["put", "aput"])
@pytest.mark.parametrize("value", [{}, {"number": 0}, {"text": "saved memory"}])
def test_langgraph_store_dictionary_values_remain_stored(method: str, value: dict) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada")
    if method == "put":
        store.put(namespace, "item", value)
    else:
        asyncio.run(store.aput(namespace, "item", value))

    assert store.get(namespace, "item").value == value
    assert store.search(namespace)[0].value == value
    assert store.list_namespaces() == [namespace]


@pytest.mark.parametrize("method", ["put", "aput"])
@pytest.mark.parametrize("value", [None, {"text": "replacement"}])
def test_langgraph_store_unsupported_ttl_still_rejects_before_mutation(
    method: str, value: dict | None
) -> None:
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    namespace = ("users", "ada")
    original = {"text": "original memory"}
    store.put(namespace, "item", original)

    with pytest.raises(NotImplementedError, match="TTL"):
        if method == "put":
            store.put(namespace, "item", value, ttl=60)
        else:
            asyncio.run(store.aput(namespace, "item", value, ttl=60))

    assert store.get(namespace, "item").value == original


@pytest.mark.parametrize("use_batch", [False, True])
def test_langgraph_store_accepts_mapping_values(use_batch):
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    original = {"color": "azure", "nested": {"rank": 3}}
    value = MappingProxyType(original)

    if use_batch:
        store.batch([PutOp(namespace=("users", "ada"), key="preferences", value=value)])
    else:
        store.put(("users", "ada"), "preferences", value)

    item = store.get(("users", "ada"), "preferences")
    assert item.value == original
    assert isinstance(item.value, dict)
    assert store.search(("users",), query="azure")[0].value == original
    assert original == {"color": "azure", "nested": {"rank": 3}}


@pytest.mark.parametrize(
    ("method", "collection", "deindex"),
    [
        (method, collection, False)
        for method in ("delete", "adelete", "put", "aput", "batch", "abatch")
        for collection in ("data", "index")
    ]
    + [(method, "index", True) for method in ("put", "aput", "batch", "abatch")],
)
@pytest.mark.parametrize(
    "error", [PermissionDeniedError(), UnavailableError(), TimeoutError("removal timed out")]
)
async def test_langgraph_store_removal_errors_remain_visible_and_retryable(
    method, collection, deindex, error
):
    class FailingRemoveClient(InMemoryOpenVikingClient):
        fail = True

        def rm(self, uri, recursive=False):
            if self.fail and f"/{collection}/" in uri:
                raise error
            if uri not in self.records:
                raise NotFoundError(uri)
            return super().rm(uri, recursive=recursive)

    client = FailingRemoveClient()
    store = OpenVikingStore(client=client)
    namespace, key, value = ("users",), "ada", {"color": "azure"}
    store.put(namespace, key, value)
    store.put(("other",), key, {"color": "teal"})

    async def mutate():
        replacement = value if deindex else None
        index = False if deindex else None
        if method == "delete":
            store.delete(namespace, key)
        elif method == "adelete":
            await store.adelete(namespace, key)
        elif method == "put":
            store.put(namespace, key, replacement, index=index)
        elif method == "aput":
            await store.aput(namespace, key, replacement, index=index)
        else:
            ops = [
                PutOp(namespace, key, replacement, index=index),
                PutOp(namespace, "later", {"written": True}),
            ]
            if method == "batch":
                store.batch(ops)
            else:
                await store.abatch(ops)

    with pytest.raises(type(error)) as caught:
        await mutate()
    assert str(caught.value) == str(error)
    assert getattr(caught.value, "code", None) == getattr(error, "code", None)
    assert bool(store.get(namespace, key)) is (deindex or collection == "data")
    assert store.get(namespace, "later") is None
    assert store.get(("other",), key).value == {"color": "teal"}

    client.fail = False
    await mutate()
    assert store.search(namespace, query="azure") == []
    item = store.get(namespace, key)
    assert (item.value if item else None) == (value if deindex else None)
    if method in ("batch", "abatch"):
        assert store.get(namespace, "later").value == {"written": True}


@pytest.mark.parametrize("missing_error", [FileNotFoundError, NotFoundError])
def test_langgraph_store_removal_keeps_missing_targets_idempotent(missing_error):
    class StrictRemoveClient(InMemoryOpenVikingClient):
        def rm(self, uri, recursive=False):
            if uri not in self.records:
                raise missing_error(uri)
            return super().rm(uri, recursive=recursive)

    store = OpenVikingStore(client=StrictRemoveClient())
    namespace, key, value = ("users",), "ada", {"color": "azure"}
    store.delete(namespace, key)
    store.put(namespace, key, value, index=False)
    assert store.get(namespace, key).value == value
    store.put(namespace, key, value)
    assert len(store.search(namespace, query="azure")) == 1
    store.delete(namespace, key)
    store.delete(namespace, key)
    assert store.get(namespace, key) is None
    assert store.search(namespace, query="azure") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [None, "doc"])
@pytest.mark.parametrize(
    "filter, expected",
    [
        ({"metadata": {"color": "blue"}}, ["alpha"]),
        ({"metadata": {"nested": {"owner": "ada"}}}, ["alpha"]),
        ({"metadata": {"nested": {"rank": {"$gte": 2}}}}, ["alpha"]),
        ({"metadata": {"detail.region": "eu"}}, ["alpha"]),
        ({"tags": [{"role": "reviewer"}, "memory"]}, ["alpha"]),
        ({"tags": [{"role": "reviewer"}]}, []),
        ({"metadata": {}}, ["alpha", "beta"]),
        ({"metadata": {"color": "black"}}, []),
        ({"metadata.nested.rank": {"gte": 2}}, ["alpha"]),
        ({"metadata.nested": {"$eq": {"rank": 3, "owner": "ada"}}}, ["alpha"]),
        ({"metadata": {"eq": "blue", "label": "blue"}}, ["scalar"]),
        ({"metadata": {"$unknown": "blue"}}, ["scalar"]),
        ({"tags": ["reviewer", "memory"]}, ["scalar"]),
        ({"kind": "doc"}, ["alpha", "beta", "scalar"]),
    ],
)
async def test_langgraph_store_structured_filters(filter, expected, query):
    store = OpenVikingStore(client=InMemoryOpenVikingClient())
    for key, value in {
        "alpha": {
            "kind": "doc",
            "metadata": {
                "color": "blue",
                "nested": {"rank": 3, "owner": "ada"},
                "detail.region": "eu",
            },
            "tags": [{"role": "reviewer", "active": True}, "memory"],
        },
        "beta": {
            "kind": "doc",
            "metadata": {"color": "red", "nested": {"rank": 1, "owner": "bob"}},
            "tags": [{"role": "reader", "active": True}, "memory"],
        },
        "scalar": {"kind": "doc", "metadata": "blue", "tags": ["reviewer", "memory"]},
    }.items():
        store.put(("users", "ada"), key, value)

    op = SearchOp(("users",), filter=filter, query=query)
    results = [
        store.search(("users",), filter=filter, query=query),
        await store.asearch(("users",), filter=filter, query=query),
        store.batch([op])[0],
        (await store.abatch([op]))[0],
    ]
    assert all(sorted(item.key for item in items) == expected for items in results)


def test_langgraph_store_semantic_search_keeps_peer_id_out_of_retrieval():
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client, actor_peer_id="peer-store")

    store.put(("users",), "ada", {"color": "azure"})
    store.search(("users",), query="azure", limit=5)

    assert "peer_id" not in client.find_calls[-1]


def test_langgraph_store_rejects_ttl_writes():
    store = OpenVikingStore(client=InMemoryOpenVikingClient())

    with pytest.raises(NotImplementedError, match="TTL is not supported"):
        store.put(("users", "ada"), "temporary", {"note": "expires"}, ttl=60)


@pytest.mark.parametrize("use_default", [False, True])
@pytest.mark.parametrize(
    ("field", "query", "expected_match"),
    [
        ("$", "solstice", True),
        ("", "solstice", True),
        ("items[0].text", "pulsar", True),
        ("items[-1].text", "galaxy", True),
        ("items[*].text", "galaxy", True),
        ("nested[*].parts[*].text", "aurora", True),
        ("metadata.*", "quasar", True),
        ("{text,metadata.title}", "nebula", True),
        ("text", "nebula", True),
        ("metadata.title", "quasar", True),
        ("absent", "solstice", False),
        ("literal[0]", "nova", True),
    ],
)
@pytest.mark.asyncio
async def test_langgraph_store_indexes_selected_field_paths(
    field, query, expected_match, use_default
):
    value = {
        "text": "nebula",
        "metadata": {"title": "quasar"},
        "items": [{"text": "pulsar"}, {"text": "galaxy"}],
        "nested": [{"parts": [{"text": "aurora"}]}],
        "literal[0]": "nova",
        "ignored": "solstice",
    }
    original = deepcopy(value)
    for mode in ("put", "aput", "batch", "abatch"):
        client = InMemoryOpenVikingClient()
        store = OpenVikingStore(client=client, index=[field] if use_default else None)
        index = None if use_default else [field]
        if mode == "put":
            store.put(("documents",), "note", value, index=index)
        elif mode == "aput":
            await store.aput(("documents",), "note", value, index=index)
        elif mode == "batch":
            store.batch([PutOp(("documents",), "note", value, index=index)])
        else:
            await store.abatch([PutOp(("documents",), "note", value, index=index)])

        assert store.get(("documents",), "note").value == value
        assert value == original
        expected_keys = ["note"] if expected_match else []
        assert [item.key for item in store.search(("documents",), query=query)] == expected_keys
        filter = {"metadata": {"title": "quasar"}}
        op = SearchOp(("documents",), query=query, filter=filter)
        results = [
            store.search(("documents",), query=query, filter=filter),
            await store.asearch(("documents",), query=query, filter=filter),
            store.batch([op])[0],
            (await store.abatch([op]))[0],
        ]
        assert all([item.key for item in items] == expected_keys for items in results)
        assert (
            store.search(("documents",), query=query, filter={"metadata": {"title": "absent"}})
            == []
        )
        if field not in {"$", ""}:
            assert store.search(("documents",), query="solstice") == []


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
    ("root_uri", "alias_prefix", "canonical_prefix"),
    [
        (
            "viking://~/memories/langgraph_store",
            "viking://~/memories",
            "viking://user/default/memories",
        ),
        (
            "viking://~/peers/support/memories/langgraph_store",
            "viking://~/peers/support/memories",
            "viking://user/default/peers/support/memories",
        ),
        (
            "viking://~/peers/support/resources/langgraph_store",
            "viking://~/peers/support/resources",
            "viking://user/default/peers/support/resources",
        ),
        (
            "viking://user/default/peers/support/memories/langgraph_store",
            "viking://user/default/peers/support/memories",
            "viking://user/default/peers/support/memories",
        ),
    ],
    ids=[
        "user-memory",
        "peer-memory",
        "peer-resource",
        "explicit-peer-memory",
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_langgraph_store_accepts_canonical_result_uris_for_home_alias_root(
    root_uri,
    alias_prefix,
    canonical_prefix,
    asynchronous,
):
    class CanonicalizingClient(InMemoryOpenVikingClient):
        def _canonicalize(self, value):
            if isinstance(value, str):
                return value.replace(f"{alias_prefix}/", f"{canonical_prefix}/", 1)
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

    async def read_results():
        return (
            await store.aget(("users", "ada"), "preferences"),
            await store.asearch(("users",)),
            await store.alist_namespaces(prefix=("users", "*")),
            await store.asearch(("users",), query="azure", limit=5),
        )

    direct, ordinary, namespaces, semantic = (
        asyncio.run(read_results())
        if asynchronous
        else (
            store.get(("users", "ada"), "preferences"),
            store.search(("users",)),
            store.list_namespaces(prefix=("users", "*")),
            store.search(("users",), query="azure", limit=5),
        )
    )
    assert direct.value == {"color": "azure"}
    assert [item.key for item in ordinary] == ["preferences"]
    assert namespaces == [("users", "ada")]
    assert [(item.namespace, item.key, item.value) for item in semantic] == [
        (("users", "ada"), "preferences", {"color": "azure"})
    ]


def test_langgraph_store_ignores_unrelated_canonical_result_uris():
    store = OpenVikingStore(
        client=InMemoryOpenVikingClient(),
        root_uri="viking://~/memories/langgraph_store",
    )

    assert (
        store._parse_index_uri(
            "viking://user/default/agent/support/memories/other_store/index/users/ada.md"
        )
        is None
    )
    assert (
        store._parse_index_uri("viking://user/support/memories/other_store/index/users/ada.md")
        is None
    )

    peer_store = OpenVikingStore(
        client=InMemoryOpenVikingClient(),
        root_uri="viking://~/peers/support/memories/langgraph_store",
    )
    for unrelated_root in (
        "viking://user/default/peers/other/memories/langgraph_store",
        "viking://user/default/memories/langgraph_store",
        "viking://agent/support/memories/langgraph_store",
        "viking://user/default/peers/support/memories/other_store",
        "viking://user/default/peers/support/resources/langgraph_store",
    ):
        assert peer_store._parse_index_uri(f"{unrelated_root}/index/users/ada.md") is None


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

    data_uri = "viking://~/memories/langgraph_store/data/users/ada.json"
    assert client.write_modes[0] == (data_uri, "create")
    assert (data_uri, "replace") in client.write_modes
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at
    assert second.value["color"] == "teal"


def test_pending_token_commit_does_not_create_missing_session():
    client = InMemoryOpenVikingClient()
    result = apply_commit_policy(
        client,
        "missing-commit-session",
        OpenVikingCommitPolicy(mode="pending_tokens", pending_token_threshold=1),
    )

    assert result is None
    assert "missing-commit-session" not in client.sessions


def test_pending_token_commit_uses_persisted_value_without_session_lookup():
    class RecordingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.get_session_calls = 0

        def get_session(self, session_id, auto_create=False):
            self.get_session_calls += 1
            return super().get_session(session_id, auto_create=auto_create)

    client = RecordingClient()
    client.create_session("persisted-hint-session")

    result = apply_commit_policy(
        client,
        "persisted-hint-session",
        OpenVikingCommitPolicy(mode="pending_tokens", pending_token_threshold=1),
        persisted_pending_tokens=0,
    )

    assert result is None
    assert client.get_session_calls == 0


@pytest.mark.parametrize("shape", ["human", "chat", "chat_chunk", "dict"])
@pytest.mark.parametrize("structured", [False, True])
def test_langgraph_middleware_injects_recall_and_captures_messages(shape, structured):
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "The user prefers azure deployments."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://~/memories",
        session_id_resolver=lambda state, runtime: "middleware-session",
        commit_on_after_agent=True,
    )

    captured_request = {}
    content = "What deployment color?"
    if structured:
        content = [
            {"type": "text", "text": content},
            {"type": "image_url", "image_url": {"url": "https://example.com/deployment.png"}},
        ]
    values = {
        "id": "middleware-user-1",
        "name": "customer",
        "content": content,
        "additional_kwargs": {"host_field": {"keep": True}},
    }
    if shape == "dict":
        user = {"role": "user", **values}
    elif shape == "human":
        user = HumanMessage(**values)
    else:
        message_type = ChatMessage if shape == "chat" else ChatMessageChunk
        user = message_type(role="user", **values)
    original = deepcopy(user)
    system = SystemMessage(
        id="policy",
        content=[{"type": "text", "text": "Stable agent policy."}],
        additional_kwargs={"host_field": {"keep": True}},
    )

    class Request:
        def __init__(self, state=None):
            self.state = state or {}
            self.messages = [user]
            self.system_message = system

        def override(self, **overrides):
            new_request = Request(self.state)
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        captured_request["request"] = request
        return AIMessage(content="ok")

    middleware.wrap_model_call(Request(), handler)
    first_request = captured_request["request"]
    recall_update = middleware.after_model({}, runtime=None)
    assert recall_update is not None
    assert first_request.system_message is system
    injected = first_request.messages[0]
    injected_values = injected if isinstance(injected, dict) else injected.model_dump()
    original_values = original if isinstance(original, dict) else original.model_dump()
    assert "azure deployments" in str(injected_values["content"])
    assert {k: v for k, v in injected_values.items() if k != "content"} == {
        k: v for k, v in original_values.items() if k != "content"
    }
    if structured:
        assert injected_values["content"][1:] == content
    assert user == original

    # A later model call for the same user turn replays the recorded block,
    # without another search or dropping context parts needed by capture.
    middleware.wrap_model_call(Request(recall_update), handler)
    replayed_request = captured_request["request"]
    assert replayed_request.messages == first_request.messages
    assert len(client.search_calls) == 1

    # Editing the same message ID requires fresh recall rather than a stale replay.
    client.records["viking://~/memories/profile.md"] = "Azure deployments must use Singapore."
    edited_request = Request(recall_update)
    edited_request.messages = [
        {**user, "content": "Which azure region?"}
        if isinstance(user, dict)
        else user.model_copy(update={"content": "Which azure region?"})
    ]
    middleware.wrap_model_call(edited_request, handler)
    assert len(client.search_calls) == 2
    edited = captured_request["request"].messages[0]
    edited_content = edited["content"] if isinstance(edited, dict) else edited.content
    assert "Singapore" in edited_content
    assert user == original

    middleware.after_agent(
        {
            **recall_update,
            "messages": [
                HumanMessage(content="Remember this."),
                AIMessage(content="I will."),
            ],
        },
        runtime=None,
    )
    assert client.sessions["middleware-session"] == []
    assert client.archives["middleware-session"]
    archived_messages = client.archives["middleware-session"][0]["messages"]
    assert len(archived_messages) == 2
    assistant_parts = archived_messages[1]["parts"]
    assert any(part["type"] == "context" for part in assistant_parts)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("interrupt_tools", [False, True])
@pytest.mark.asyncio
async def test_langgraph_middleware_replays_exact_recall_prefix_after_restart(
    asynchronous, streaming, capture, interrupt_tools
):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver
    from pydantic import Field

    class RecordingModel(BaseChatModel):
        requests: list[list[Any]] = Field(default_factory=list)
        response_count: int = 0

        @property
        def _llm_type(self):
            return "recording"

        def bind_tools(self, tools, **kwargs):
            return self

        def _reply(self, messages):
            self.requests.append(list(messages))
            self.response_count += 1
            if messages[-1].type == "tool":
                return AIMessage(content=f"answer-{self.response_count}")
            user = next(message for message in reversed(messages) if message.type == "human")
            return AIMessage(
                content="Checking the deployment.",
                tool_calls=[{"id": f"lookup-{user.id}", "name": "lookup_deployment", "args": {}}],
            )

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            response = self._reply(messages)
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=response.content, tool_calls=response.tool_calls)
            )

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            for chunk in self._stream(messages, stop, run_manager, **kwargs):
                yield chunk

    def lookup_deployment() -> str:
        """Check the current deployment status."""
        return "The deployment is ready."

    def request_bytes(messages):
        wire = convert_to_openai_messages(messages, text_format="block")
        return json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()

    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "The user prefers azure deployments."}
    )
    checkpointer = InMemorySaver()
    config = {"configurable": {"thread_id": "prefix-session"}}

    def build_agent(model, *, pause_before_tools=False):
        return create_agent(
            model,
            tools=[lookup_deployment],
            system_prompt="Stable agent policy.",
            middleware=[
                OpenVikingContextMiddleware(
                    client=client,
                    target_uri="viking://~/memories",
                    capture_on_after_agent=capture,
                    # Python 3.10 async tasks cannot resolve the graph's config context.
                    session_id_resolver=lambda _state, _runtime: "prefix-session",
                )
            ],
            checkpointer=checkpointer,
            interrupt_before=["tools"] if pause_before_tools else None,
        )

    async def run(agent, text):
        payload = None if text is None else {"messages": [{"role": "user", "content": text}]}
        if streaming:
            events = (
                [
                    event
                    async for event in agent.astream(
                        payload, config, stream_mode=["messages", "updates"]
                    )
                ]
                if asynchronous
                else list(agent.stream(payload, config, stream_mode=["messages", "updates"]))
            )
            assert any(mode == "updates" for mode, _ in events)
            # Python 3.10 lacks implicit async callback propagation in LangChain 1.0.
            if not asynchronous or sys.version_info >= (3, 11):
                assert any(
                    mode == "messages" and isinstance(event[0], AIMessageChunk)
                    for mode, event in events
                )
        elif asynchronous:
            await agent.ainvoke(payload, config)
        else:
            agent.invoke(payload, config)

    model = RecordingModel()
    agent = build_agent(model)
    for schema in (agent.get_input_schema(), agent.get_output_schema()):
        assert "openviking_recall_ledger" not in schema.model_json_schema().get("properties", {})
        assert "openviking_pending_context" not in schema.model_json_schema().get("properties", {})
    await run(agent, "first azure deployment prompt")
    client.records["viking://~/memories/profile.md"] = "Azure deployments must use Singapore."
    if interrupt_tools:
        agent = build_agent(model, pause_before_tools=True)
    await run(agent, "second azure deployment prompt")
    state = agent.get_state(config).values

    assert len(client.search_calls) == 2
    assert all(message.id for message in state["messages"] if message.type == "human")
    assert [message.content for message in state["messages"] if message.type == "human"] == [
        "first azure deployment prompt",
        "second azure deployment prompt",
    ]
    assert sum(len(ledger) for ledger in state["openviking_recall_ledger"].values()) == 2
    assert len(model.requests) == (3 if interrupt_tools else 4)
    assert "azure deployments" in model.requests[0][1].content
    assert "Singapore" in model.requests[2][-1].content

    # A fresh middleware and graph recover the ledger from the same checkpointer.
    restarted_model = RecordingModel()
    restarted_agent = build_agent(restarted_model)
    if interrupt_tools:
        assert agent.get_state(config).next == ("tools",)
        await run(restarted_agent, None)
        assert len(client.search_calls) == 2
        references = [
            part
            for message in client.sessions["prefix-session"]
            for part in message.get("parts", [])
            if part["type"] == "context"
        ]
        assert len(references) == (2 if capture else 0)
        if capture:
            assert references[-1] == {
                "type": "context",
                "uri": "viking://~/memories/profile.md",
                "context_type": "memory",
                "abstract": "Azure deployments must use Singapore.",
            }
    await run(restarted_agent, "third azure deployment prompt")
    restarted_state = restarted_agent.get_state(config).values

    assert len(client.search_calls) == 3
    requests = model.requests + restarted_model.requests
    for previous, current in zip(requests, requests[1:], strict=False):
        assert request_bytes(current[: len(previous)]) == request_bytes(previous)
        assert current[0].content == "Stable agent policy."
    assert sum(len(ledger) for ledger in restarted_state["openviking_recall_ledger"].values()) == 3
    captured = client.sessions["prefix-session"]
    if capture:
        assert len(captured) == 12
        parts = [part for message in captured for part in message.get("parts", [])]
        assert not any(
            "<openviking_context>" in part.get("text", "")
            for part in parts
            if part["type"] == "text"
        )
        assert len([part for part in parts if part["type"] == "tool"]) == 6
        assert len([part for part in parts if part["type"] == "context"]) == 3
        assert [
            message.content for message in restarted_state["messages"] if message.type == "human"
        ] == [
            "first azure deployment prompt",
            "second azure deployment prompt",
            "third azure deployment prompt",
        ]
        assert not restarted_state.get("openviking_pending_context")
    else:
        assert captured == []


@pytest.mark.parametrize("has_context", [False, True])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_langgraph_middleware_discards_recall_when_model_call_fails(has_context, error_type):
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "Azure deployments use a stale region."}
        if has_context
        else {}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda _state, _runtime: "empty-failure-session",
    )

    class Request:
        state = {}
        runtime = None
        messages = [HumanMessage(id="empty-failure-user", content="Find azure deployments.")]
        system_message = None

        def override(self, **overrides):
            request = Request()
            request.messages = overrides.get("messages", self.messages)
            return request

    def handler(_request):
        raise error_type("model failed")

    with pytest.raises(error_type, match="model failed"):
        middleware.wrap_model_call(Request(), handler)

    assert middleware.after_model({"messages": Request.messages}, runtime=None) is None
    client.records["viking://~/memories/profile.md"] = "Azure deployments now use Singapore."
    seen = []
    middleware.wrap_model_call(Request(), lambda request: seen.append(request))
    assert len(client.search_calls) == 2
    assert "Singapore" in seen[0].messages[0].content


def test_langgraph_middleware_batches_persistence_before_separate_commit():
    class RecordingClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.events = []
            self._inside_batch = False

        def add_message(self, *args, **kwargs):
            if not self._inside_batch:
                self.events.append("add_message")
            return super().add_message(*args, **kwargs)

        def batch_add_messages(self, *args, **kwargs):
            self.events.append("batch_add_messages")
            self._inside_batch = True
            try:
                return super().batch_add_messages(*args, **kwargs)
            finally:
                self._inside_batch = False

        def get_session(self, *args, **kwargs):
            self.events.append("get_session")
            return super().get_session(*args, **kwargs)

        def commit_session(self, *args, **kwargs):
            self.events.append("commit_session")
            return super().commit_session(*args, **kwargs)

    client = RecordingClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-batch-commit",
        commit_policy=OpenVikingCommitPolicy(
            mode="pending_tokens",
            pending_token_threshold=1,
        ),
    )

    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Persist me in one batch."),
                AIMessage(content="Then commit separately."),
            ]
        },
        runtime=None,
    )

    assert client.events == ["batch_add_messages", "commit_session"]
    assert client.archives["middleware-batch-commit"]


def test_langgraph_middleware_uses_peer_id_only_for_message_capture():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "The middleware peer prefers azure."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://~/memories",
        session_id_resolver=lambda state, runtime: "middleware-peer-session",
    )

    class Request:
        state = {"peer_id": "peer-middleware"}
        messages = [HumanMessage(content="What peer color?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            new_request.state = self.state
            return new_request

    middleware.wrap_model_call(Request(), lambda request: AIMessage(content="ok"))
    middleware.after_agent(
        {
            "peer_id": "peer-middleware",
            "messages": [
                HumanMessage(content="Remember middleware peer."),
                AIMessage(content="Stored."),
            ],
        },
        runtime=None,
    )

    assert "peer_id" not in client.search_calls[-1]
    assert [message.get("peer_id") for message in client.sessions["middleware-peer-session"]] == [
        "peer-middleware",
        "peer-middleware",
    ]


def test_langgraph_middleware_does_not_duplicate_active_messages_in_context():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "Middleware recall uses green context."}
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
        target_uri="viking://~/memories",
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
    recalled_content = captured_request["request"].messages[0].content
    assert captured_request["request"].system_message is None
    assert "Middleware recall uses green context" in recalled_content
    assert "OpenViking active duplicate user turn" not in recalled_content
    assert "OpenViking active duplicate assistant turn" not in recalled_content


def test_langgraph_middleware_uses_runtime_thread_id():
    client = InMemoryOpenVikingClient(
        {"viking://~/memories/profile.md": "Runtime thread users prefer teal."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://~/memories",
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
    assert captured_request["request"].system_message is None
    assert "Runtime thread users prefer teal" in captured_request["request"].messages[0].content
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
        {"viking://~/memories/profile.md": "Shared fallback should never be used."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://~/memories",
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
    client = InMemoryOpenVikingClient({"viking://~/memories/profile.md": "Retry cleanup context."})
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://~/memories",
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


def test_langgraph_middleware_retries_failed_batch_with_pending_context():
    class FailFirstBatchClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__({"viking://~/memories/profile.md": "Retained retry context."})
            self.batch_attempts = 0

        def batch_add_messages(self, session_id, messages, **kwargs):
            self.batch_attempts += 1
            if self.batch_attempts == 1:
                raise RuntimeError("batch write failed")
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = FailFirstBatchClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://~/memories",
        session_id_resolver=lambda state, runtime: "middleware-batch-retry",
    )

    class Request:
        messages = [HumanMessage(content="What retry context?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    middleware.wrap_model_call(Request(), lambda request: AIMessage(content="ok"))
    state = {
        "messages": [
            HumanMessage(content="What retry context?"),
            AIMessage(content="Retry context stored."),
        ]
    }

    with pytest.raises(RuntimeError, match="batch write failed"):
        middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)

    assert client.batch_attempts == 2
    assert len(client.sessions["middleware-batch-retry"]) == 2
    assistant_parts = client.sessions["middleware-batch-retry"][1]["parts"]
    assert sum(part["type"] == "context" for part in assistant_parts) == 1


def test_langgraph_middleware_chunks_batches_at_server_limit():
    class CappedBatchClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_sizes = []

        def batch_add_messages(self, session_id, messages, **kwargs):
            if len(messages) > 100:
                raise RuntimeError("server batch limit exceeded")
            self.batch_sizes.append(len(messages))
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = CappedBatchClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-large-batch",
    )
    state = {"messages": [HumanMessage(content=f"Message {index}") for index in range(101)]}

    middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)

    assert client.batch_sizes == [100, 1]
    assert len(client.sessions["middleware-large-batch"]) == 101


def test_langgraph_middleware_retries_only_failed_batch_chunk():
    class FailSecondBatchClient(InMemoryOpenVikingClient):
        def __init__(self):
            super().__init__()
            self.batch_sizes = []

        def batch_add_messages(self, session_id, messages, **kwargs):
            self.batch_sizes.append(len(messages))
            if len(self.batch_sizes) == 2:
                raise RuntimeError("second batch failed")
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = FailSecondBatchClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda state, runtime: "middleware-chunk-retry",
    )
    middleware._pending_context_parts[("middleware-chunk-retry", "")] = [
        {"type": "context", "uri": "viking://resources/retry.md"}
    ]
    messages = [HumanMessage(content="First"), AIMessage(content="Context holder")]
    messages.extend(HumanMessage(content=f"Message {index}") for index in range(98))
    messages.append(AIMessage(content="Retry only this message"))
    state = {"messages": messages}

    with pytest.raises(RuntimeError, match="second batch failed"):
        middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)
    middleware.after_agent(state, runtime=None)

    assert client.batch_sizes == [100, 1, 1]
    stored = client.sessions["middleware-chunk-retry"]
    assert len(stored) == 101
    assert sum(part["type"] == "context" for message in stored for part in message["parts"]) == 1
