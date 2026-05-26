#!/usr/bin/env python3

from __future__ import annotations

import json

from tau2.gym.gym_agent import AgentGymEnv


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
        # Keep raw tool schemas from tau2 for MCP exposure
        self.tool_schemas = [tool.openai_schema for tool in info_dict["tools"]]
        self.ground_truth = str(self.task.evaluation_criteria)
        self.user_scenario = self.task.user_scenario

    def tool_call(self, tool_name: str, arguments: dict) -> str:
        if self.terminated:
            return "Task Terminated"

        if tool_name == "communicate_with_user":
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
