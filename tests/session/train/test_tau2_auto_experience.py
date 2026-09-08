# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import benchmark.tau2.train.rollout_executor_vikingbot as module


@pytest.fixture
def memory_client(monkeypatch):
    from vikingbot.openviking_mount.ov_server import VikingClient

    client = SimpleNamespace(
        _memory_target_uri=lambda _: "viking://user/test/memories/",
        search=AsyncMock(return_value={"memories": []}),
        read_content=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(VikingClient, "create", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_auto_experience_uses_original_query_top2_bodies_and_strips_metadata(memory_client):
    root = "viking://user/test/memories/experiences/"
    memory_client.search.return_value = {
        "memories": [{"uri": root + name, "score": 0.9} for name in ("b.md", "a.md", "c.md")]
    }
    memory_client.read_content.side_effect = [
        '# B\n## Situation\nB body\n<!-- MEMORY_FIELDS {"status":"promoted"} -->\n',
        '# A\nA body\n<!-- MEMORY_FIELDS {"evidence":"private"} -->\n'
        '<!-- MEMORY_FIELDS {"score":42} -->',
    ]
    trace = {}
    query = "  I want to change my flight, not cancel it.\n"
    reminder = await module._load_auto_experience_reminder(query, trace=trace)
    memory_client.search.assert_awaited_once_with(query, target_uri=root.rstrip("/"), limit=2)
    assert [call.args[0] for call in memory_client.read_content.await_args_list] == [
        root + "b.md",
        root + "a.md",
    ]
    assert "B body" in reminder and "A body" in reminder
    assert reminder.index("B body") < reminder.index("A body")
    assert all(text not in reminder for text in ("MEMORY_FIELDS", "private", root, "score"))
    assert trace["injected_uris"] == [root + "b.md", root + "a.md"]
    assert trace["status"] == "injected"
    memory_client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["none", "duplicate", "outside", "metadata_only"])
async def test_auto_experience_handles_fewer_results(memory_client, kind):
    uri = "viking://user/test/memories/experiences/one.md"
    memory_client.search.return_value = {
        "memories": {
            "none": [],
            "duplicate": [{"uri": uri}, {"uri": uri}],
            "outside": [{"uri": "viking://user/other/memories/experiences/one.md"}],
            "metadata_only": [{"uri": uri}],
        }[kind]
    }
    memory_client.read_content.return_value = (
        '<!-- MEMORY_FIELDS {"status":"promoted"} -->' if kind == "metadata_only" else "body"
    )
    trace = {}
    result = await module._load_auto_experience_reminder("query", trace=trace)
    if kind == "duplicate":
        assert result.count("body") == 1
        assert trace["injected_uris"] == [uri]
        memory_client.read_content.assert_awaited_once_with(uri, level="read")
    else:
        assert result is None
        assert trace["injected_uris"] == []
        assert trace["status"] == "no_experience"
    memory_client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["search", "read", "empty_read"])
async def test_auto_experience_does_not_silently_hide_retrieval_failure(memory_client, failure):
    memory_client.search.return_value = {
        "memories": [{"uri": "viking://user/test/memories/experiences/one.md"}]
    }
    if failure == "search":
        memory_client.search.side_effect = RuntimeError("search unavailable")
    elif failure == "read":
        memory_client.read_content.side_effect = RuntimeError("read unavailable")
    else:
        memory_client.read_content.return_value = ""
    trace = {}
    with pytest.raises(RuntimeError):
        await module._load_auto_experience_reminder("query", trace=trace)
    assert trace["status"] == "error"
    memory_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_experience_blank_query_does_not_search(memory_client):
    trace = {}
    assert await module._load_auto_experience_reminder(" ", trace=trace) is None
    assert trace["status"] == "empty_query"
    memory_client.search.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_memory", [True, False])
async def test_auto_experience_injected_before_agent_without_skill(monkeypatch, has_memory):
    trace = {}
    expected = "[Experience Reminder]\n## Relevant Agent Experience\n\n## Situation\nBody"
    retrieve = AsyncMock(return_value=expected if has_memory else None)
    monkeypatch.setattr(module, "_load_auto_experience_reminder", retrieve)
    prepare_skill = AsyncMock(side_effect=AssertionError("must not install skill"))
    monkeypatch.setattr(module, "_prepare_experience_loader_skill", prepare_skill)
    monkeypatch.setattr(module, "_execute_required_experience_loader_read", prepare_skill)
    monkeypatch.setattr(module, "_make_tau2_plain_text_router", Mock(return_value=None))
    observed = {}

    class LoopResult:
        def __init__(self, messages):
            self.messages = messages

        def __iter__(self):
            return iter(("final", None, [], {}, 1))

    async def run_loop(**kwargs):
        observed.update(kwargs)
        return LoopResult(kwargs["messages"])

    build_messages = AsyncMock(
        return_value=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "original query"},
        ]
    )
    agent = SimpleNamespace(
        context=SimpleNamespace(build_messages=build_messages),
        _run_agent_loop=run_loop,
    )
    result = await module._run_agent(
        agent=agent,
        system_prompt="policy",
        user_prompt="original query",
        session_key=SimpleNamespace(),
        sender_id="tau2_user",
        keep_default_tools=True,
        loader_mode="auto_experience",
        auto_experience_trace=trace,
    )
    retrieve.assert_awaited_once_with("original query", trace=trace)
    assert observed["inject_write_experience"] is False
    assert observed["inject_constraint_experience"] is False
    assert agent.context.skills_enable is False
    assert build_messages.await_args.kwargs["experience_recall_enable"] is False
    assert result[7] is None  # No skill.
    assert result[2] == []  # No synthetic agent search/read tool calls.
    assert result[6] == (expected if has_memory else None)
    if has_memory:
        assert observed["messages"][2] == {"role": "user", "content": expected}
        assert "Body" in result[5]  # Existing memory coverage accounting sees injection.
    else:
        assert not result[5]
    assert observed["messages"][-1]["content"] == "original query"


def test_auto_experience_configuration_excludes_memory_tools_and_native():
    from benchmark.tau2.train.rollout_executor import make_tau2_rollout_executor

    assert module.normalize_tau2_experience_loader_mode(" AUTO_EXPERIENCE ") == "auto_experience"
    executor = make_tau2_rollout_executor(
        backend="vikingbot", options={"loader_mode": "auto_experience"}
    )
    assert executor.loader_mode == "auto_experience"
    with pytest.raises(ValueError, match="requires rollout backend 'vikingbot'"):
        make_tau2_rollout_executor(backend="native", options={"loader_mode": "auto_experience"})
    names = [
        "read_file",
        "openviking_search",
        "search_experience",
        "read_experience",
        "load_relevant_experience",
    ]
    tools = SimpleNamespace(tool_names=names, unregister=names.remove, register=Mock())
    provider = SimpleNamespace(list_openai_tools=lambda: [])
    module._configure_tools(
        SimpleNamespace(tools=tools),
        provider,
        keep_default_tools=True,
        loader_mode="auto_experience",
    )
    assert names == ["read_file"]
    tools.register.assert_not_called()
    prompt = module._build_system_prompt(
        "policy", keep_default_tools=True, rollout_language="default", loader_mode="auto_experience"
    )
    assert "experience_loader" not in prompt
    assert "automatically provided" in prompt


@pytest.mark.asyncio
async def test_auto_experience_accepts_resolved_home_alias(memory_client):
    memory_client._memory_target_uri = lambda _: "viking://~/memories/"
    memory_client.search.return_value = {
        "memories": [{"uri": "viking://user/test/memories/experiences/one.md"}]
    }
    memory_client.read_content.return_value = "visible body"
    assert "visible body" in await module._load_auto_experience_reminder("query")


@pytest.mark.asyncio
async def test_auto_context_can_disable_stale_skills_in_full_profile(tmp_path, monkeypatch):
    from vikingbot.agent.context import ContextBuilder
    from vikingbot.config.schema import SessionKey

    context = ContextBuilder(tmp_path, skills_enable=False, system_prompt_profile="full")
    context._skills = SimpleNamespace(
        get_always_skills=Mock(side_effect=AssertionError("must not load old skills")),
        build_skills_summary=Mock(side_effect=AssertionError("must not advertise old skills")),
    )
    monkeypatch.setattr(context, "_get_identity", AsyncMock(return_value="identity"))
    monkeypatch.setattr(context, "_load_bootstrap_files", Mock(return_value=""))
    prompt = await context.build_system_prompt(
        SessionKey(type="cli", channel_id="tau2", chat_id="auto"), ov_tools_enable=False
    )
    assert "Required skill" not in prompt
    assert "# Skills" not in prompt


def test_auto_experience_service_cli_and_factory(monkeypatch):
    import sys

    from benchmark.tau2.train import service_app

    monkeypatch.setattr(sys, "argv", ["tau2-service", "--loader-mode", "auto_experience"])
    assert service_app.parse_args().loader_mode == "auto_experience"
    monkeypatch.setattr(service_app, "create_dataset_service_app", lambda **kwargs: kwargs)
    factory = Mock(return_value=object())
    monkeypatch.setattr(service_app, "make_tau2_rollout_executor", factory)
    app = service_app.create_app(rollout_backend="vikingbot", loader_mode="auto_experience")
    app["make_rollout_executor"]({})
    assert factory.call_args.kwargs["options"]["loader_mode"] == "auto_experience"


def test_auto_experience_batch_cli_and_request_options(monkeypatch):
    import sys
    import importlib
    from openviking.session.train import batch_runner
    run_batch_train_eval = importlib.import_module("openviking.session.train.run_batch_train_eval")

    monkeypatch.setattr(
        sys,
        "argv",
        ["batch", "--dataset", "tau2", "--domain", "airline", "--loader-mode", "auto_experience"],
    )
    assert run_batch_train_eval.parse_args().loader_mode == "auto_experience"
    config = batch_runner.BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        loader_mode="auto_experience",
        benchmark_service_url="http://localhost:1949",
    )
    factory = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(batch_runner, "RemoteRolloutExecutor", factory)
    batch_runner._build_pipeline(config, SimpleNamespace())
    assert factory.call_args.kwargs["options"]["loader_mode"] == "auto_experience"
