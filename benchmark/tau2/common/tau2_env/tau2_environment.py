#!/usr/bin/env python3

from __future__ import annotations

import importlib
import json
import os
import time
from collections.abc import Callable
from functools import wraps
from typing import Any
from uuid import uuid4

from openviking.utils.model_retry import is_retryable_rate_limit_error, rate_limit_retry_delay
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

DEFAULT_TAU2_USER_LLM = "openai/doubao-seed-2-0-code-preview-260215"

_TAU2_GENERATE_REFERENCE_MODULES = (
    "tau2.agent.llm_agent",
    "tau2.user.user_simulator",
    "tau2.evaluator.evaluator_nl_assertions",
    "tau2.environment.utils.interface_agent",
)


def _is_tau2_retryable_rate_limit_error(exc: BaseException) -> bool:
    return is_retryable_rate_limit_error(exc)


def _tau2_rate_limit_retry_delay(attempt: int) -> float:
    return rate_limit_retry_delay(attempt)


def _wrap_tau2_generate_with_rate_limit_retry(generate: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(generate, "_openviking_tau2_rate_limit_retry", False):
        return generate

    @wraps(generate)
    def generate_with_rate_limit_retry(*args: Any, **kwargs: Any) -> Any:
        attempt = 1
        while True:
            try:
                return generate(*args, **kwargs)
            except Exception as exc:
                if not _is_tau2_retryable_rate_limit_error(exc):
                    raise
                delay = _tau2_rate_limit_retry_delay(attempt)
                logger.warning(
                    "tau2 LiteLLM generate rate limited; retrying attempt=%d delay=%.1fs error=%s",
                    attempt,
                    delay,
                    exc,
                )
                time.sleep(delay)
                attempt += 1

    generate_with_rate_limit_retry._openviking_tau2_rate_limit_retry = True
    generate_with_rate_limit_retry._openviking_original_generate = generate
    return generate_with_rate_limit_retry


def _install_tau2_litellm_rate_limit_retry() -> None:
    """Patch tau2-bench's sync LiteLLM generate path with rate-limit retry.

    AgentGymEnv's user simulator and orchestrator call tau2.utils.llm_utils.generate
    through synchronous module globals imported with ``from ... import generate``.
    Those calls run in tau2's own worker thread, so a sync sleep-based retry is
    safe and does not block the OpenViking service event loop.
    """
    try:
        llm_utils = importlib.import_module("tau2.utils.llm_utils")
    except Exception as exc:
        logger.debug("tau2 llm_utils unavailable for rate-limit retry patch: %s", exc)
        return

    original = getattr(llm_utils, "_openviking_original_generate", None)
    current = getattr(llm_utils, "generate", None)
    if not callable(current):
        return
    if getattr(current, "_openviking_tau2_rate_limit_retry", False):
        wrapped = current
        original = getattr(current, "_openviking_original_generate", original)
    else:
        original = current
        wrapped = _wrap_tau2_generate_with_rate_limit_retry(original)
        llm_utils.generate = wrapped
        llm_utils._openviking_original_generate = original

    for module_name in _TAU2_GENERATE_REFERENCE_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        module_generate = getattr(module, "generate", None)
        if (
            module_generate is original
            or module_generate is current
            or getattr(module_generate, "_openviking_tau2_rate_limit_retry", False)
        ):
            module.generate = wrapped


def _install_tau2_litellm_unknown_cost_suppression() -> None:
    """Suppress noisy LiteLLM cost lookup errors for private/proxy model names.

    tau2-bench logs an ERROR whenever LiteLLM cannot find a public price entry
    for models such as Doubao private gateway names. Cost accounting is not used
    by our rollout evaluator, and at high concurrency those repeated ERROR logs
    add significant IO noise. Replace cost lookup failures with a zero-cost
    result while preserving normal behavior for mapped models.
    """

    try:
        llm_utils = importlib.import_module("tau2.utils.llm_utils")
    except Exception as exc:
        logger.debug("tau2 llm_utils unavailable for cost suppression patch: %s", exc)
        return

    for name in ("get_response_cost", "get_cost"):
        current = getattr(llm_utils, name, None)
        if not callable(current) or getattr(current, "_openviking_tau2_cost_suppressed", False):
            continue

        @wraps(current)
        def suppressed_cost(*args: Any, __fn: Callable[..., Any] = current, **kwargs: Any) -> Any:
            try:
                return __fn(*args, **kwargs)
            except Exception as exc:
                text = str(exc)
                if "model isn't mapped" not in text and "not mapped" not in text:
                    raise
                logger.debug("suppressed tau2 LiteLLM cost lookup failure: %s", exc)
                return 0.0

        suppressed_cost._openviking_tau2_cost_suppressed = True
        suppressed_cost._openviking_original_cost_fn = current
        setattr(llm_utils, name, suppressed_cost)


try:
    from tau2.gym.gym_agent import AgentGymEnv
except ModuleNotFoundError:
    AgentGymEnv = None


class CommunicateWithUser:
    """The agent's only channel for talking to the user.

    tau2's environment has no native "speak to the user" action, so we add this
    tool: whatever ``content`` the agent passes is delivered to the tau2 user
    simulator, and the simulator's reply comes back as the tool result. This class
    owns both the tool's schema (``openai_schema``, consumed by
    ``Tau2BenchToolProvider``) and its execution (``forward``, invoked by
    ``Tau2BenchEnv.tool_call``).
    """

    name = "communicate_with_user"
    description = (
        "say something to the user. Note that the customer cannot see the answer "
        "returned in `final_answer`. You must communicate with the customer "
        "exclusively through this tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "the content to say to the user",
            }
        },
        "required": ["content"],
    }

    def __init__(self, env):
        # ``env`` is the underlying tau2 AgentGymEnv.
        self.env = env

    def forward(self, content: str):
        """Deliver ``content`` to the tau2 user simulator.

        Returns the raw gym step tuple ``(obs, reward, terminated, truncated, info)``;
        ``Tau2BenchEnv.tool_call`` cleans the observation and tracks termination.
        """
        response = self.env.tool_call(self.name, {"content": content})
        return response

    @classmethod
    def openai_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.parameters,
            },
        }


class Tau2BenchEnv:
    def __init__(self, domain: str, task_id: str):
        if AgentGymEnv is not None:
            self._impl = _GymTau2BenchEnv(domain, task_id)
        else:
            self._impl = _NativeTau2BenchEnv(domain, task_id)

    def reset(self):
        self._impl.reset()
        self.env = self._impl.env
        self.terminated = self._impl.terminated
        self.user_query = self._impl.user_query
        self.task = self._impl.task
        self.simulation_run = self._impl.simulation_run
        self.policy = self._impl.policy
        self.tool_schemas = self._impl.tool_schemas
        self.ground_truth = self._impl.ground_truth
        self.user_scenario = self._impl.user_scenario

    def tool_call(self, tool_name: str, arguments: dict) -> str:
        response = self._impl.tool_call(tool_name, arguments)
        self.terminated = self._impl.terminated
        return response

    def append_agent_message(self, content: str) -> None:
        append_message = getattr(self._impl, "append_agent_message", None)
        if callable(append_message):
            append_message(content)

    def _get_reward(self):
        return self._impl._get_reward()


class _GymTau2BenchEnv:
    def __init__(self, domain: str, task_id: str):
        _install_tau2_litellm_rate_limit_retry()
        _install_tau2_litellm_unknown_cost_suppression()
        self.env = AgentGymEnv(
            domain=domain,
            task_id=task_id,
            user_llm=os.getenv("TAU2_USER_LLM") or DEFAULT_TAU2_USER_LLM,
        )
        self.terminated = False

    def reset(self):
        user_query, info_dict = self.env.reset()
        self.user_query = user_query.lstrip("user: ")
        self.task = info_dict["task"]
        self.simulation_run = info_dict["simulation_run"]
        self.policy = info_dict["policy"]
        self.tool_schemas = [tool.openai_schema for tool in info_dict["tools"]]
        self.tool_schemas.append(CommunicateWithUser.openai_schema())
        self.ground_truth = str(self.task.evaluation_criteria)
        self.user_scenario = self.task.user_scenario

    def tool_call(self, tool_name: str, arguments: dict) -> str:
        if self.terminated:
            return "Task Terminated"

        if tool_name == CommunicateWithUser.name:
            obs, reward, terminated, truncated, info = self.env.step(arguments["content"])
        else:
            action = {"name": tool_name, "arguments": arguments}
            obs, reward, terminated, truncated, info = self.env.step(json.dumps(action))

        self.terminated = terminated
        return _clean_obs(obs)

    def append_agent_message(self, content: str) -> None:
        if not content.strip():
            return
        simulation = self._simulation_run_from_env_info()
        if simulation is None:
            return

        from tau2.data_model.message import AssistantMessage

        simulation.messages.append(AssistantMessage(role="assistant", content=content))
        self.env._simulation_run = simulation
        self.simulation_run = simulation.model_dump_json(indent=2)

    def _get_reward(self):
        reward, reward_info = self.env._get_reward()
        try:
            return reward, json.loads(reward_info)
        except (TypeError, json.JSONDecodeError):
            return reward, reward_info

    def _simulation_run_from_env_info(self):
        simulation_run_json = self.env._get_info().get("simulation_run")
        if not simulation_run_json:
            return None
        try:
            from tau2.data_model.simulation import SimulationRun

            return SimulationRun.model_validate_json(simulation_run_json)
        except Exception:
            return None


class _NativeTau2BenchEnv:
    def __init__(self, domain: str, task_id: str):
        self.domain = domain
        self.task_id = task_id
        self.env = None
        self.terminated = False
        self.simulation_run = None

    def reset(self):
        from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
        from tau2.registry import registry

        self._evaluate_simulation = evaluate_simulation
        self._evaluation_type = EvaluationType.ALL
        self.env = registry.get_env_constructor(self.domain)()
        tasks = registry.get_tasks_loader(self.domain)()
        task_by_id = {str(task.id): task for task in tasks}
        self.task = task_by_id[self.task_id]
        self.env.set_state(
            initialization_data=(
                self.task.initial_state.initialization_data
                if self.task.initial_state is not None
                else None
            ),
            initialization_actions=(
                self.task.initial_state.initialization_actions
                if self.task.initial_state is not None
                else None
            ),
            message_history=(
                self.task.initial_state.message_history
                if self.task.initial_state is not None
                and self.task.initial_state.message_history is not None
                else []
            ),
        )
        self.policy = self.env.get_policy()
        self.tool_schemas = [tool.openai_schema for tool in self.env.get_tools()]
        self.tool_schemas.append(CommunicateWithUser.openai_schema())
        self.user_query = str(self.task.user_scenario)
        self.ground_truth = str(self.task.evaluation_criteria)
        self.user_scenario = self.task.user_scenario
        self._messages = []

    def tool_call(self, tool_name: str, arguments: dict) -> str:
        from tau2.data_model.message import AssistantMessage, ToolCall

        if self.terminated:
            return "Task Terminated"

        if tool_name == CommunicateWithUser.name:
            # tau2 evaluates required customer-facing information by scanning
            # AssistantMessage text content. Record this synthetic communication
            # as assistant text so the native fallback matches gym trajectories.
            self._messages.append(
                AssistantMessage(role="assistant", content=str(arguments["content"]))
            )
            return (
                "User simulator is unavailable in this tau2 version; "
                "continue using tools and final answer."
            )

        tool_call = ToolCall(
            id=f"call_{uuid4().hex}",
            name=tool_name,
            arguments=arguments,
            requestor="assistant",
        )
        assistant_message = AssistantMessage(role="assistant", tool_calls=[tool_call])
        tool_message = self.env.get_response(tool_call)
        self._messages.extend([assistant_message, tool_message])
        return _clean_obs(tool_message.content or "")

    def append_agent_message(self, content: str) -> None:
        from tau2.data_model.message import AssistantMessage

        if content.strip():
            self._messages.append(AssistantMessage(role="assistant", content=content))

    def _get_reward(self):
        from tau2.data_model.simulation import SimulationRun
        from tau2.utils.utils import get_now

        now = get_now()
        simulation = SimulationRun(
            id=f"native_tau2_{self.domain}_{self.task_id}_{uuid4().hex}",
            task_id=self.task.id,
            start_time=now,
            end_time=now,
            duration=0.0,
            termination_reason="agent_stop",
            reward_info=None,
            messages=self._messages,
        )
        reward_info = self._evaluate_simulation(
            domain=self.domain,
            task=self.task,
            simulation=simulation,
            evaluation_type=self._evaluation_type,
            solo_mode=False,
        )
        simulation.reward_info = reward_info
        self.simulation_run = simulation
        return reward_info.reward, reward_info


def _clean_obs(obs: str) -> str:
    if "tool: " in obs:
        obs = obs.removeprefix("tool: ")
    if "user: " in obs:
        obs = obs.removeprefix("user: ")
    return obs
