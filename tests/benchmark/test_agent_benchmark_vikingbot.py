"""Exercise the real repository agent loop with benchmark tools and recorded OV messages."""

import asyncio
import json
import os

import pytest

from benchmark.agent_benchmarks.agent_runtime import (
    build_agent,
    run_agent,
    trajectory_messages,
    workspace_tools,
)


def test_trajectory_keeps_assistant_answer_when_it_matches_the_task_text():
    messages = trajectory_messages("42", {"turns": [], "text": "42"})
    assert [message["role"] for message in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_repository_loop_executes_tools_and_preserves_training_trajectory(tmp_path):
    from vikingbot.agent.loop import AgentLoop
    from vikingbot.providers.base import LLMProvider, LLMResponse, ToolCallRequest

    class Provider(LLMProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def get_default_model(self):
            return "test"

        async def chat(self, messages, tools=None, **kwargs):
            assert {x["function"]["name"] for x in tools} == {
                "exec",
                "read_file",
                "write_file",
                "edit_file",
                "list_dir",
            }
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    content="Create the artifact.",
                    tool_calls=[
                        ToolCallRequest(
                            "w", "write_file", {"path": "answer.txt", "content": "42"}, 1
                        )
                    ],
                )
            if self.calls == 2:
                return LLMResponse(
                    content="Check the artifact.",
                    tool_calls=[ToolCallRequest("r", "read_file", {"path": "answer.txt"}, 1)],
                )
            assert messages[-1]["role"] == "tool" and messages[-1]["content"] == "42"
            return LLMResponse(content="Finished.")

    settings = {"model": "test", "temperature": 0, "max_iterations": 5}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = Provider()
    agent = build_agent(settings, workspace, tmp_path, provider=provider)
    assert isinstance(agent, AgentLoop)
    execution = await run_agent(
        settings,
        "Create and check an artifact.",
        workspace_tools(),
        workspace,
        tmp_path,
        {},
        timeout=10,
        agent=agent,
    )
    assert provider.calls == 3 and (workspace / "answer.txt").read_text() == "42"
    assert execution["runtime"] == "vikingbot.AgentLoop"
    assert [x["tool_name"] for x in execution["tool_executions"]] == ["write_file", "read_file"]
    messages = trajectory_messages("task", execution)
    parts = [x for m in messages for x in m["parts"] if x["type"] == "tool"]
    assert parts[0]["tool_input"] == {"path": "answer.txt", "content": "42"}
    assert parts[1]["tool_output"] == "42"
    assert any(p.get("text") == "Create the artifact." for m in messages for p in m["parts"])
    assert messages[-1]["parts"] == [{"type": "text", "text": "Finished."}]
    assert json.loads((tmp_path / "ov-recall.json").read_text())["enabled"] is False


@pytest.mark.asyncio
async def test_model_error_is_not_graded_as_a_successful_agent_answer(tmp_path, monkeypatch):
    from openviking.models.vlm import VLMFactory
    from openviking.models.vlm.base import VLMResponse

    class BrokenVLM:
        provider = "volcengine"
        thinking = True

        async def get_completion_async(self, **kwargs):
            return VLMResponse(content="upstream unavailable", finish_reason="error")

        def close(self):
            pass

    monkeypatch.setattr(VLMFactory, "create", lambda config: BrokenVLM())
    settings = {"model": "test", "temperature": 0, "max_iterations": 2}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(RuntimeError, match="model request failed"):
        await run_agent(settings, "task", [], workspace, tmp_path, {}, timeout=10)
    saved = json.loads((tmp_path / "execution.json").read_text())
    assert saved["status"] == "failed"
    assert not (tmp_path / "rollout.json").exists()


@pytest.mark.asyncio
async def test_container_tools_report_transport_failure_and_use_actual_path(tmp_path, monkeypatch):
    from benchmark.skillflow import run

    calls = []

    def execute(container, workdir, argv, **kw):
        calls.append((container, workdir, argv, kw))
        return {"exit_code": 1, "transport_error": True, "stdout": "", "stderr": "connection lost"}

    monkeypatch.setattr(run, "execute", execute)
    failures = []
    tools = {tool.name: tool for tool in run.container_tools("container-id", "/task", failures)}
    with pytest.raises(RuntimeError, match="transport"):
        await tools["write_file"].execute(None, path="artifact.txt", content="abc")
    assert json.loads(calls[0][3]["stdin"]) == ["artifact.txt", "abc"]
    assert calls[0][:2] == ("container-id", "/task")
    assert failures


@pytest.mark.asyncio
async def test_timeout_preserves_partial_turns_and_closes_runtime(tmp_path):
    from vikingbot.providers.base import LLMProvider

    class Waiting(LLMProvider):
        def get_default_model(self):
            return "test"

        async def chat(self, *args, **kw):
            await asyncio.Event().wait()

    settings = {"model": "test", "temperature": 0, "max_iterations": 2}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent = build_agent(settings, workspace, tmp_path, provider=Waiting())
    with pytest.raises(TimeoutError):
        await run_agent(settings, "task", [], workspace, tmp_path, {}, timeout=0.01, agent=agent)
    saved = json.loads((tmp_path / "execution.json").read_text())
    assert saved["status"] == "failed" and "turns" in saved
    assert not agent.sandbox_manager._sandboxes


def test_runtime_configuration_needs_no_external_agent_directory(tmp_path, monkeypatch):
    from openai.resources.chat.completions import Completions

    from benchmark.agent_benchmarks.worker import configure

    monkeypatch.setattr(Completions, "create", lambda self, **kwargs: kwargs)
    monkeypatch.setattr(
        "benchmark.agent_benchmarks.quota.install_quota_backoff", lambda output: None
    )
    for name in ("PATH", "EVALUATION_API_KEY", "EVALUATION_API_BASE", "EVALUATION_MODEL"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setenv("BENCHMARK_TEST_KEY", "private")
    settings = configure(
        {
            "runtime": {"api_key_env": "BENCHMARK_TEST_KEY"},
            "options": {"reasoning_effort": "low"},
            "output": str(tmp_path),
        }
    )
    assert settings["provider"] == "volcengine" and settings["reasoning_effort"] == "low"
    assert "private" not in (tmp_path / "runtime-settings.json").read_text()
    judge_kwargs = Completions.create(None, extra_body=None)
    assert judge_kwargs["extra_body"]["reasoning_effort"] == "low"
    assert judge_kwargs["extra_body"]["thinking"] == {"type": "enabled"}


def test_official_code_verifier_failure_is_distinct_from_invalid_submission():
    from benchmark.evoagentbench.run import official_reward

    with pytest.raises(RuntimeError, match="grader unavailable"):
        official_reward({"reward": 0, "error": "missing_tests"}, "code_implementation")
    assert official_reward({"reward": 0, "error": "no_code_extracted"}, "code_implementation") == 0
