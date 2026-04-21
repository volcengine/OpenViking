from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_build_session_messages_maps_haystack_sessions_and_dates():
    module = _load_module(
        "longmemeval_import_to_ov",
        "benchmark/longmemeval/vikingbot/import_to_ov.py",
    )
    item = {
        "question_id": "qid-1",
        "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 03:24"],
        "haystack_session_ids": ["sess-a", "sess-b"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ],
            [
                {"role": "user", "content": "Degree?"},
            ],
        ],
    }

    sessions = module.build_session_messages(item)

    assert len(sessions) == 2
    assert sessions[0]["meta"]["sample_id"] == "qid-1"
    assert sessions[0]["meta"]["session_key"] == "sess-a"
    assert sessions[0]["meta"]["date_time"] == "2023/05/20 (Sat) 02:21"
    assert sessions[0]["messages"] == [
        {"role": "user", "text": "Hi", "index": 0},
        {"role": "assistant", "text": "Hello", "index": 1},
    ]
    assert sessions[1]["meta"]["session_key"] == "sess-b"


def test_load_longmemeval_qa_extracts_question_answer_and_date(tmp_path: Path):
    module = _load_module(
        "longmemeval_run_eval",
        "benchmark/longmemeval/vikingbot/run_eval.py",
    )
    data = [
        {
            "question_id": "qid-1",
            "question": "What degree did I graduate with?",
            "answer": "Business Administration",
            "question_date": "2023/05/30 (Tue) 23:40",
        },
        {
            "question_id": "qid-2",
            "question": "What tracker did I buy?",
            "answer": "Fitbit Inspire HR",
            "question_date": "2023/02/16 (Thu) 09:10",
        },
    ]
    input_path = tmp_path / "longmemeval.json"
    input_path.write_text(json.dumps(data), encoding="utf-8")

    qa_list = module.load_longmemeval_qa(str(input_path), sample_index=1)

    assert qa_list == [
        {
            "sample_id": "qid-2",
            "question": "What tracker did I buy?",
            "answer": "Fitbit Inspire HR",
            "question_time": "2023-02-16",
            "question_type": "",
            "evidence": [],
        }
    ]


def test_parse_longmemeval_datetime_returns_iso_date():
    module = _load_module(
        "longmemeval_run_eval",
        "benchmark/longmemeval/vikingbot/run_eval.py",
    )

    parsed = module.parse_longmemeval_datetime("2023/05/30 (Tue) 23:40")

    assert parsed.strftime("%Y-%m-%d") == "2023-05-30"


def test_build_vikingbot_chat_cmd_uses_sample_scoped_sender_and_session():
    module = _load_module(
        "longmemeval_run_eval",
        "benchmark/longmemeval/vikingbot/run_eval.py",
    )

    sender_id = module.build_sample_user_id("qid-1", "per-sample")
    session_id = module.build_sample_agent_id("qid-1", "per-sample")

    cmd = module.build_vikingbot_chat_cmd(
        question="What degree did I graduate with?",
        question_time="2023-05-30",
        sender_id=sender_id,
        session_id=session_id,
    )

    assert cmd[:3] == ["vikingbot", "chat", "-m"]
    assert "--sender" in cmd
    assert sender_id in cmd
    assert "--session" in cmd
    assert session_id in cmd
    assert cmd[-1] == "-e"


def test_eval_workspace_routing_uses_cli_chat_id_for_eval():
    module = _load_module(
        "vikingbot_openviking_routing",
        "bot/vikingbot/utils/openviking_routing.py",
    )
    schema = _load_module(
        "vikingbot_schema",
        "bot/vikingbot/config/schema.py",
    )

    session_key = schema.SessionKey(type="cli", channel_id="default", chat_id="lm_agent_123")

    workspace_id = module.resolve_openviking_workspace_id(
        session_key=session_key,
        sandbox_manager=None,
        eval_mode=True,
    )

    assert workspace_id == "lm_agent_123"


@pytest.mark.asyncio
async def test_eval_identity_adds_retrieval_policy():
    module = _load_module(
        "vikingbot_agent_context",
        "bot/vikingbot/agent/context.py",
    )

    builder = module.ContextBuilder(Path("."), eval=True)
    identity = await builder._get_identity(session_key=None)

    assert "## Eval Retrieval Policy" in identity
    assert "search OpenViking memory before concluding that information is missing" in identity
    assert "Do not say you lack information" in identity
    assert "Base every benchmark answer on concrete OpenViking evidence" in identity
    assert "If multiple tool results are available, reconcile them" in identity
    assert "Prefer the shortest answer that is fully supported" in identity
    assert "Search results are summaries" in identity
    assert "inspect the full content of the most relevant result" in identity


@pytest.mark.asyncio
async def test_eval_loop_forces_retry_before_answering_without_tools():
    module = _load_module(
        "vikingbot_agent_loop",
        "bot/vikingbot/agent/loop.py",
    )

    class FakeResponse:
        def __init__(self, content, has_tool_calls=False):
            self.content = content
            self.has_tool_calls = has_tool_calls
            self.tool_calls = []
            self.reasoning_content = None
            self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    class FakeProvider:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, tools, model, session_id):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return FakeResponse("I don't know.")
            return FakeResponse("Final grounded answer.")

    class FakeContext:
        def add_assistant_message(self, messages, content, tool_call_dicts, reasoning_content=None):
            return messages + [{"role": "assistant", "content": content}]

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            return messages + [{"role": "tool", "content": result}]

    class FakeSessionKey:
        def safe_name(self):
            return "cli__default__lm_test"

    loop = module.AgentLoop.__new__(module.AgentLoop)
    loop.max_iterations = 3
    loop.bus = None
    loop.provider = FakeProvider()
    loop.tools = SimpleNamespace(
        get_definitions=lambda: [],
        execute=None,
    )
    loop.context = FakeContext()
    loop.model = "fake-model"
    loop.sandbox_manager = None
    loop._eval = True

    final_content, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "Question"}],
        session_key=FakeSessionKey(),
        sender_id="lm_user_test",
        publish_events=False,
    )

    assert final_content == "Final grounded answer."
    assert tools_used == []
    assert iteration == 2
    assert loop.provider.calls == 2
    reminder_messages = loop.provider.messages[1]
    assert any(
        msg.get("role") == "user"
        and "use openviking_search before giving your final answer" in msg.get("content", "")
        for msg in reminder_messages
    )


@pytest.mark.asyncio
async def test_eval_loop_adds_grounding_reflection_after_tool_results():
    module = _load_module("vikingbot_agent_loop_grounding", "bot/vikingbot/agent/loop.py")

    class FakeResponse:
        def __init__(self, content, has_tool_calls=False, tool_calls=None):
            self.content = content
            self.has_tool_calls = has_tool_calls
            self.tool_calls = tool_calls or []
            self.reasoning_content = None
            self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    class FakeToolCall:
        def __init__(self):
            self.id = "call-1"
            self.name = "openviking_search"
            self.arguments = {"query": "degree"}
            self.tokens = 0

    class FakeProvider:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, tools, model, session_id):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return FakeResponse(
                    "Let me search first.",
                    has_tool_calls=True,
                    tool_calls=[FakeToolCall()],
                )
            return FakeResponse("Final grounded answer.")

    class FakeContext:
        def add_assistant_message(self, messages, content, tool_call_dicts, reasoning_content=None):
            return messages + [{"role": "assistant", "content": content}]

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            return messages + [{"role": "tool", "content": result}]

    class FakeSessionKey:
        def safe_name(self):
            return "cli__default__lm_test"

    loop = module.AgentLoop.__new__(module.AgentLoop)
    loop.max_iterations = 3
    loop.bus = None
    loop.provider = FakeProvider()
    loop.tools = SimpleNamespace(
        get_definitions=lambda: [],
        execute=None,
    )
    async def fake_execute(name, arguments, session_key, sandbox_manager, sender_id, eval_mode):
        return "Found one supported fact."
    loop.tools.execute = fake_execute
    loop.context = FakeContext()
    loop.model = "fake-model"
    loop.sandbox_manager = None
    loop._eval = True

    final_content, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[
            {
                "role": "user",
                "content": "Current date: 2023-05-30. Answer the question directly: What degree did I graduate with?",
            }
        ],
        session_key=FakeSessionKey(),
        sender_id="lm_user_test",
        publish_events=False,
    )

    assert final_content == "Final grounded answer."
    assert len(tools_used) == 1
    assert iteration == 3
    reminder_messages = loop.provider.messages[1]
    assert any(
        msg.get("role") == "user"
        and "Base your answer only on the retrieved OpenViking evidence" in msg.get("content", "")
        and "If multiple facts conflict, resolve the conflict explicitly" in msg.get("content", "")
        and "Search results are summaries" in msg.get("content", "")
        and "openviking_multi_read" in msg.get("content", "")
        for msg in reminder_messages
    )


@pytest.mark.asyncio
async def test_eval_loop_forces_multi_read_before_finishing_after_search_only():
    module = _load_module("vikingbot_agent_loop_multiread", "bot/vikingbot/agent/loop.py")

    class FakeResponse:
        def __init__(self, content, has_tool_calls=False, tool_calls=None):
            self.content = content
            self.has_tool_calls = has_tool_calls
            self.tool_calls = tool_calls or []
            self.reasoning_content = None
            self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    class FakeToolCall:
        def __init__(self):
            self.id = "call-1"
            self.name = "openviking_search"
            self.arguments = {"query": "degree"}
            self.tokens = 0

    class FakeProvider:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, tools, model, session_id):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return FakeResponse("Let me search first.", has_tool_calls=True, tool_calls=[FakeToolCall()])
            if self.calls == 2:
                return FakeResponse("Business Administration")
            return FakeResponse("Business Administration")

    class FakeContext:
        def add_assistant_message(self, messages, content, tool_call_dicts, reasoning_content=None):
            return messages + [{"role": "assistant", "content": content}]

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            return messages + [{"role": "tool", "content": result}]

    class FakeSessionKey:
        def safe_name(self):
            return "cli__default__lm_test"

    loop = module.AgentLoop.__new__(module.AgentLoop)
    loop.max_iterations = 4
    loop.bus = None
    loop.provider = FakeProvider()
    loop.tools = SimpleNamespace(get_definitions=lambda: [], execute=None)

    async def fake_execute(name, arguments, session_key, sandbox_manager, sender_id, eval_mode):
        return "1. [user_memory] uri=viking://user/foo/memories/events/2023/01/01/sample.md score=0.9 abstract=degree info"

    loop.tools.execute = fake_execute
    loop.context = FakeContext()
    loop.model = "fake-model"
    loop.sandbox_manager = None
    loop._eval = True

    final_content, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "What degree did I graduate with?"}],
        session_key=FakeSessionKey(),
        sender_id="lm_user_test",
        publish_events=False,
    )

    assert final_content == "Business Administration"
    assert len(tools_used) == 1
    assert iteration == 3
    followup_messages = loop.provider.messages[2]
    assert any(
        msg.get("role") == "user"
        and "Before finalizing your answer, inspect the full content" in msg.get("content", "")
        and "openviking_multi_read" in msg.get("content", "")
        for msg in followup_messages
    )


@pytest.mark.asyncio
async def test_eval_loop_retries_after_evidence_based_refusal():
    module = _load_module("vikingbot_agent_loop_refusal", "bot/vikingbot/agent/loop.py")

    class FakeResponse:
        def __init__(self, content, has_tool_calls=False, tool_calls=None):
            self.content = content
            self.has_tool_calls = has_tool_calls
            self.tool_calls = tool_calls or []
            self.reasoning_content = None
            self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    class FakeToolCall:
        def __init__(self):
            self.id = "call-1"
            self.name = "openviking_multi_read"
            self.arguments = {"uris": ["viking://user/foo/memories/events/2023/01/01/sample.md"]}
            self.tokens = 0

    class FakeProvider:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, tools, model, session_id):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return FakeResponse("Reading evidence.", has_tool_calls=True, tool_calls=[FakeToolCall()])
            if self.calls == 2:
                return FakeResponse("There is no retrieved evidence to answer this question.")
            return FakeResponse("Business Administration")

    class FakeContext:
        def add_assistant_message(self, messages, content, tool_call_dicts, reasoning_content=None):
            return messages + [{"role": "assistant", "content": content}]

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            return messages + [{"role": "tool", "content": result}]

    class FakeSessionKey:
        def safe_name(self):
            return "cli__default__lm_test"

    loop = module.AgentLoop.__new__(module.AgentLoop)
    loop.max_iterations = 4
    loop.bus = None
    loop.provider = FakeProvider()
    loop.tools = SimpleNamespace(get_definitions=lambda: [], execute=None)

    async def fake_execute(name, arguments, session_key, sandbox_manager, sender_id, eval_mode):
        return "Full memory content: The degree was Business Administration."

    loop.tools.execute = fake_execute
    loop.context = FakeContext()
    loop.model = "fake-model"
    loop.sandbox_manager = None
    loop._eval = True

    final_content, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "What degree did I graduate with?"}],
        session_key=FakeSessionKey(),
        sender_id="lm_user_test",
        publish_events=False,
    )

    assert final_content == "Business Administration"
    assert len(tools_used) == 1
    assert iteration == 3
    followup_messages = loop.provider.messages[2]
    assert any(
        msg.get("role") == "user"
        and "You already retrieved OpenViking evidence" in msg.get("content", "")
        and "Do not answer with" in msg.get("content", "")
        for msg in followup_messages
    )


@pytest.mark.asyncio
async def test_eval_loop_retries_after_conflict_style_answer():
    module = _load_module("vikingbot_agent_loop_conflict", "bot/vikingbot/agent/loop.py")

    class FakeResponse:
        def __init__(self, content, has_tool_calls=False, tool_calls=None):
            self.content = content
            self.has_tool_calls = has_tool_calls
            self.tool_calls = tool_calls or []
            self.reasoning_content = None
            self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    class FakeToolCall:
        def __init__(self):
            self.id = "call-1"
            self.name = "openviking_multi_read"
            self.arguments = {"uris": ["viking://user/foo/memories/events/2023/01/01/sample.md"]}
            self.tokens = 0

    class FakeProvider:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, tools, model, session_id):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return FakeResponse("Reading evidence.", has_tool_calls=True, tool_calls=[FakeToolCall()])
            if self.calls == 2:
                return FakeResponse(
                    "There is conflicting evidence regarding the result. Two sources say 27:12 and one says 25:50."
                )
            return FakeResponse("25:50")

    class FakeContext:
        def add_assistant_message(self, messages, content, tool_call_dicts, reasoning_content=None):
            return messages + [{"role": "assistant", "content": content}]

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            return messages + [{"role": "tool", "content": result}]

    class FakeSessionKey:
        def safe_name(self):
            return "cli__default__lm_test"

    loop = module.AgentLoop.__new__(module.AgentLoop)
    loop.max_iterations = 4
    loop.bus = None
    loop.provider = FakeProvider()
    loop.tools = SimpleNamespace(get_definitions=lambda: [], execute=None)

    async def fake_execute(name, arguments, session_key, sandbox_manager, sender_id, eval_mode):
        return "Full memory content with conflicting candidate values."

    loop.tools.execute = fake_execute
    loop.context = FakeContext()
    loop.model = "fake-model"
    loop.sandbox_manager = None
    loop._eval = True

    final_content, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "What was my personal best time?"}],
        session_key=FakeSessionKey(),
        sender_id="lm_user_test",
        publish_events=False,
    )

    assert final_content == "25:50"
    assert len(tools_used) == 1
    assert iteration == 3
    followup_messages = loop.provider.messages[2]
    assert any(
        msg.get("role") == "user"
        and "Resolve the conflict" in msg.get("content", "")
        and "choose the single best-supported answer" in msg.get("content", "")
        for msg in followup_messages
    )


@pytest.mark.asyncio
async def test_eval_loop_retries_for_concise_direct_answer():
    module = _load_module("vikingbot_agent_loop_concise", "bot/vikingbot/agent/loop.py")

    class FakeResponse:
        def __init__(self, content, has_tool_calls=False, tool_calls=None):
            self.content = content
            self.has_tool_calls = has_tool_calls
            self.tool_calls = tool_calls or []
            self.reasoning_content = None
            self.usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    class FakeToolCall:
        def __init__(self):
            self.id = "call-1"
            self.name = "openviking_multi_read"
            self.arguments = {"uris": ["viking://user/foo/memories/events/2023/01/01/sample.md"]}
            self.tokens = 0

    class FakeProvider:
        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, tools, model, session_id):
            self.calls += 1
            self.messages.append(messages)
            if self.calls == 1:
                return FakeResponse("Reading evidence.", has_tool_calls=True, tool_calls=[FakeToolCall()])
            if self.calls == 2:
                return FakeResponse(
                    "Based on the retrieved OpenViking evidence, the answer is Business Administration, and this comes from your graduation-related memory."
                )
            return FakeResponse("Business Administration")

    class FakeContext:
        def add_assistant_message(self, messages, content, tool_call_dicts, reasoning_content=None):
            return messages + [{"role": "assistant", "content": content}]

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            return messages + [{"role": "tool", "content": result}]

    class FakeSessionKey:
        def safe_name(self):
            return "cli__default__lm_test"

    loop = module.AgentLoop.__new__(module.AgentLoop)
    loop.max_iterations = 4
    loop.bus = None
    loop.provider = FakeProvider()
    loop.tools = SimpleNamespace(get_definitions=lambda: [], execute=None)

    async def fake_execute(name, arguments, session_key, sandbox_manager, sender_id, eval_mode):
        return "Full memory content: The degree was Business Administration."

    loop.tools.execute = fake_execute
    loop.context = FakeContext()
    loop.model = "fake-model"
    loop.sandbox_manager = None
    loop._eval = True

    final_content, tools_used, token_usage, iteration = await loop._run_agent_loop(
        messages=[{"role": "user", "content": "What degree did I graduate with?"}],
        session_key=FakeSessionKey(),
        sender_id="lm_user_test",
        publish_events=False,
    )

    assert final_content == "Business Administration"
    assert len(tools_used) == 1
    assert iteration == 3
    followup_messages = loop.provider.messages[2]
    assert any(
        msg.get("role") == "user"
        and "Restate the answer as the shortest direct answer" in msg.get("content", "")
        and "Do not include sourcing commentary" in msg.get("content", "")
        for msg in followup_messages
    )


def test_build_full_eval_steps_supports_skip_import():
    module = _load_module(
        "longmemeval_run_full_eval",
        "benchmark/longmemeval/vikingbot/run_full_eval.py",
    )

    steps = module.build_steps(
        python_executable="/usr/bin/python3",
        input_path="/tmp/longmemeval.json",
        output_path="/tmp/result.csv",
        skip_import=True,
    )

    assert [step["name"] for step in steps] == ["eval", "judge", "stats"]
    assert steps[0]["cmd"] == [
        "/usr/bin/python3",
        "benchmark/longmemeval/vikingbot/run_eval.py",
        "/tmp/longmemeval.json",
        "--output",
        "/tmp/result.csv",
        "--threads",
        "20",
        "--timeout",
        "300",
    ]


def test_create_judge_client_supports_openai_and_azure(monkeypatch):
    module = _load_module(
        "longmemeval_judge",
        "benchmark/longmemeval/vikingbot/judge.py",
    )

    calls: list[tuple[str, dict]] = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            calls.append(("openai", kwargs))

    class FakeAsyncAzureOpenAI:
        def __init__(self, **kwargs):
            calls.append(("azure", kwargs))

    monkeypatch.setattr(module, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(module, "AsyncAzureOpenAI", FakeAsyncAzureOpenAI)

    module.create_llm_client(
        "openai",
        base_url="https://example.com/v1",
        token="token-a",
    )
    module.create_llm_client(
        "azure",
        base_url="https://example.openai.azure.com",
        token="token-b",
        api_version="2024-03-01-preview",
    )

    assert calls == [
        (
            "openai",
            {
                "base_url": "https://example.com/v1",
                "api_key": "token-a",
            },
        ),
        (
            "azure",
            {
                "api_key": "token-b",
                "azure_endpoint": "https://example.openai.azure.com",
                "api_version": "2024-03-01-preview",
            },
        ),
    ]


def test_create_judge_client_uses_default_azure_api_version(monkeypatch):
    module = _load_module(
        "longmemeval_judge_default_azure",
        "benchmark/longmemeval/vikingbot/judge.py",
    )

    calls: list[dict] = []

    class FakeAsyncAzureOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(module, "AsyncAzureOpenAI", FakeAsyncAzureOpenAI)

    module.create_llm_client(
        "azure",
        base_url="https://example.openai.azure.com",
        token="token-b",
    )

    assert calls == [
        {
            "api_key": "token-b",
            "azure_endpoint": "https://example.openai.azure.com",
            "api_version": module.DEFAULT_AZURE_API_VERSION,
        }
    ]


def test_get_ungraded_rows_supports_force_rejudge():
    module = _load_module(
        "longmemeval_judge_force",
        "benchmark/longmemeval/vikingbot/judge.py",
    )

    rows = [
        {
            "question": "q1",
            "answer": "a1",
            "response": "r1",
            "result": "CORRECT",
            "reasoning": "old reasoning",
        },
        {
            "question": "q2",
            "answer": "a2",
            "response": "r2",
            "result": "",
            "reasoning": "",
        },
    ]

    ungraded_default = module.get_ungraded_rows(rows, force=False)
    assert ungraded_default == [1]
    assert rows[0]["result"] == "CORRECT"
    assert rows[0]["reasoning"] == "old reasoning"

    ungraded_force = module.get_ungraded_rows(rows, force=True)
    assert ungraded_force == [0, 1]
    assert rows[0]["result"] == ""
    assert rows[0]["reasoning"] == ""


def test_build_sample_agent_id_uses_per_sample_namespace():
    module = _load_module(
        "longmemeval_import_to_ov",
        "benchmark/longmemeval/vikingbot/import_to_ov.py",
    )

    shared = module.build_sample_agent_id("sample-1", "shared")
    per_sample = module.build_sample_agent_id("sample-1", "per-sample")
    per_sample_again = module.build_sample_agent_id("sample-1", "per-sample")
    other_sample = module.build_sample_agent_id("sample-2", "per-sample")

    assert shared == "default"
    assert per_sample.startswith("lm_")
    assert per_sample == per_sample_again
    assert per_sample != other_sample


def test_build_sample_user_id_uses_per_sample_namespace():
    module = _load_module(
        "longmemeval_import_to_ov",
        "benchmark/longmemeval/vikingbot/import_to_ov.py",
    )

    shared = module.build_sample_user_id("sample-1", "shared")
    per_sample = module.build_sample_user_id("sample-1", "per-sample")
    per_sample_again = module.build_sample_user_id("sample-1", "per-sample")
    other_sample = module.build_sample_user_id("sample-2", "per-sample")

    assert shared == "default"
    assert per_sample.startswith("lm_user_")
    assert per_sample == per_sample_again
    assert per_sample != other_sample


def test_resolve_parallel_uses_fallback_and_validates():
    module = _load_module(
        "longmemeval_import_to_ov",
        "benchmark/longmemeval/vikingbot/import_to_ov.py",
    )

    assert module._resolve_parallel(None, 8) == 8
    assert module._resolve_parallel(64, 8) == 64

    with pytest.raises(ValueError):
        module._resolve_parallel(0, 8)


@pytest.mark.asyncio
async def test_run_import_deferred_submits_before_waiting(monkeypatch):
    module = _load_module(
        "longmemeval_import_to_ov",
        "benchmark/longmemeval/vikingbot/import_to_ov.py",
    )

    item = {
        "question_id": "qid-1",
        "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 03:24"],
        "haystack_session_ids": ["sess-a", "sess-b"],
        "haystack_sessions": [
            [{"role": "user", "content": "Hi"}],
            [{"role": "user", "content": "Bye"}],
        ],
    }

    event_log: list[tuple[str, str, str, str]] = []
    records: list[dict] = []

    async def fake_submit(
        messages,
        openviking_url,
        submit_semaphore,
        session_time=None,
        agent_id="default",
        user_id="default",
        sample_id=None,
        session_key=None,
    ):
        session_name = messages[0]["text"]
        event_log.append(("submit", session_name, user_id, agent_id))
        return {
            "token_usage": None,
            "task_id": f"task-{session_name}",
            "trace_id": "",
            "user_id": user_id,
            "agent_id": agent_id,
        }

    async def fake_wait(
        openviking_url,
        task_id,
        wait_semaphore,
        agent_id="default",
        user_id="default",
        sample_id=None,
        session_key=None,
    ):
        event_log.append(("wait", task_id, user_id, agent_id))
        return {
            "embedding": 1,
            "vlm": 2,
            "llm_input": 3,
            "llm_output": 4,
            "total": 10,
        }

    monkeypatch.setattr(module, "load_longmemeval_data", lambda path, sample_index=None: [item])
    monkeypatch.setattr(module, "load_ingest_record", lambda: {})
    monkeypatch.setattr(module, "load_success_csv", lambda _: set())
    monkeypatch.setattr(module, "save_ingest_record", lambda record: None)
    monkeypatch.setattr(module, "write_error_record", lambda record, error_path: None)
    monkeypatch.setattr(module, "write_success_record", lambda record, csv_path: records.append(record))
    monkeypatch.setattr(module, "is_already_ingested", lambda *args, **kwargs: False)
    monkeypatch.setattr(module, "mark_ingested", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "submit_viking_ingest", fake_submit)
    monkeypatch.setattr(module, "wait_for_viking_task", fake_wait)

    args = SimpleNamespace(
        input="/tmp/longmemeval.json",
        sample=None,
        sessions=None,
        parallel=2,
        clear_ingest_record=False,
        force_ingest=False,
        success_csv="/tmp/success.csv",
        error_log="/tmp/error.log",
        openviking_url="http://localhost:1933",
        wait_mode="deferred",
        agent_id_mode="per-sample",
        user_id_mode="per-sample",
        submit_parallel=None,
        wait_parallel=None,
    )

    await module.run_import(args)

    assert [event[0] for event in event_log] == ["submit", "submit", "wait", "wait"]
    submit_user_ids = [event[2] for event in event_log if event[0] == "submit"]
    wait_user_ids = [event[2] for event in event_log if event[0] == "wait"]
    submit_agent_ids = [event[3] for event in event_log if event[0] == "submit"]
    wait_agent_ids = [event[3] for event in event_log if event[0] == "wait"]
    assert len(set(submit_user_ids)) == 1
    assert submit_user_ids == wait_user_ids
    assert len(set(submit_agent_ids)) == 1
    assert submit_agent_ids == wait_agent_ids
    assert len(records) == 2


@pytest.mark.asyncio
async def test_openviking_search_scopes_to_sample_memory(monkeypatch):
    module = _load_module(
        "vikingbot_ov_file",
        "bot/vikingbot/agent/tools/ov_file.py",
    )

    calls: list[tuple[str, str, str, int]] = []

    class FakeClient:
        admin_user_id = "default"

        async def search_memory(self, query, user_id, agent_user_id, limit=30):
            calls.append((query, user_id, agent_user_id, limit))
            return {
                "user_memory": [
                    SimpleNamespace(
                        uri="viking://user/lm_user_x/memories/entities/education/user_degree.md",
                        abstract="Business Administration degree",
                        score=0.91,
                    )
                ],
                "agent_memory": [],
            }

    async def fake_get_client(self, tool_context):
        return FakeClient()

    monkeypatch.setattr(module.VikingSearchTool, "_get_client", fake_get_client)

    tool = module.VikingSearchTool()
    tool_context = SimpleNamespace(
        workspace_id="lm_agent_x",
        sender_id="lm_user_x",
    )

    result = await tool.execute(tool_context, query="user graduation degree")

    assert calls == [("user graduation degree", "lm_user_x", "default", 30)]
    assert "viking://user/lm_user_x/memories/entities/education/user_degree.md" in result
