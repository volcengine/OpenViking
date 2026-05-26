#!/usr/bin/env python3

from __future__ import annotations

import json

from tau2.gym.gym_agent import AgentGymEnv


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
        self.env = AgentGymEnv(domain=domain, task_id=task_id, user_llm="openai/doubao-seed-2-0-pro-260215")
        self.terminated = False

    def reset(self):
        user_query, info_dict = self.env.reset()
        self.user_query = user_query.lstrip("user: ")
        self.task = info_dict["task"]
        self.simulation_run = info_dict["simulation_run"]
        self.policy = info_dict["policy"]
        # All OpenAI tool schemas exposed to the agent: tau2's native tools plus
        # communicate_with_user (the agent's only channel to the user).
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

        if "tool: " in obs:
            obs = obs.lstrip("tool: ")
        if "user: " in obs:
            obs = obs.lstrip("user: ")
        self.terminated = terminated
        return obs
