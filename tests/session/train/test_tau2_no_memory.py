from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from benchmark.tau2.train import rollout_executor_vikingbot as module


def test_none_configuration(monkeypatch):
    import sys
    from benchmark.tau2.train import service_app
    from benchmark.tau2.train.rollout_executor import make_tau2_rollout_executor
    import importlib
    from openviking.session.train import batch_runner
    run_batch_train_eval = importlib.import_module("openviking.session.train.run_batch_train_eval")

    assert module.normalize_tau2_experience_loader_mode(" NONE ") == "none"
    executor = make_tau2_rollout_executor(backend="vikingbot", options={"loader_mode": "none"})
    assert executor.loader_mode == "none"
    monkeypatch.setattr(sys, "argv", ["service", "--loader-mode", "none"])
    assert service_app.parse_args().loader_mode == "none"
    monkeypatch.setattr(sys, "argv", ["batch", "--dataset", "tau2", "--domain", "airline", "--loader-mode", "none"])
    assert run_batch_train_eval.parse_args().loader_mode == "none"
    config = batch_runner.BatchTrainEvalConfig(dataset="tau2", domain="airline", loader_mode="none")
    assert config.loader_mode == "none"
    prompt = module._build_system_prompt("Airline policy", keep_default_tools=False, rollout_language="default", loader_mode="none")
    assert "Airline policy" in prompt
    assert "experience" not in prompt.lower()
    assert "skill" not in prompt.lower()
    names = ["read_file", "exec", "openviking_search", "search_experience", "read_experience"]
    registry = SimpleNamespace(tool_names=names, unregister=names.remove, register=Mock())
    module._configure_tools(SimpleNamespace(tools=registry), SimpleNamespace(list_openai_tools=lambda: []), keep_default_tools=False, loader_mode="none")
    assert names == []


@pytest.mark.asyncio
async def test_none_runs_without_skill_or_any_memory(monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("memory/skill must not be called"))
    for name in ["_load_auto_experience_reminder", "_prepare_experience_loader_skill", "_execute_required_experience_loader_read"]:
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(module, "_make_tau2_plain_text_router", Mock(return_value=None))
    observed = {}

    class Result:
        def __init__(self, messages):
            self.messages = messages

        def __iter__(self):
            return iter(("final", None, [], {}, 1))

    async def loop(**kwargs):
        observed.update(kwargs)
        return Result(kwargs["messages"])

    build = AsyncMock(return_value=[{"role": "system", "content": "identity"}, {"role": "user", "content": "query"}])
    agent = SimpleNamespace(context=SimpleNamespace(build_messages=build, skills_enable=True), _run_agent_loop=loop)
    result = await module._run_agent(agent=agent, system_prompt="policy", user_prompt="query", session_key=SimpleNamespace(), sender_id="user", keep_default_tools=False, loader_mode="none")
    forbidden.assert_not_called()
    assert agent.context.skills_enable is False
    assert build.await_args.kwargs["ov_tools_enable"] is False
    assert build.await_args.kwargs["experience_recall_enable"] is False
    assert observed["inject_write_experience"] is False
    assert observed["inject_constraint_experience"] is False
    assert not result[5] and result[6] is None and result[7] is None
    assert len(observed["messages"]) == 3
