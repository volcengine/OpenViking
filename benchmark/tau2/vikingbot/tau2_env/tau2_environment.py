#!/usr/bin/env python3

from __future__ import annotations

import json

from tau2.gym.gym_agent import AgentGymEnv
from smolagents import Tool



class CommunicateWithUser(Tool):
    name = "communicate_with_user"
    description = (
        "say something to the user. You must communicate with the customer exclusively through this tool."
    )
    inputs = {
        "content": {
            "type": "string",
            "description": "the content to say to the user",
        }
    }
    output_type = "string"

    def __init__(self, env, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.env = env

    def forward(self, content: str) -> str:
        response = self.env.tool_call(self.name, {"content": content})
        return response


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
        # Optionally build smolagents tools if needed later
        self.tools = [
            create_tool_from_json_schema(tool.openai_schema, self)
            for tool in info_dict["tools"]
        ]
        self.tools.append(CommunicateWithUser(self))
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


def create_tool_from_json_schema(schema, env):
    function_def = schema["function"]
    tool_name = function_def["name"]
    description = function_def["description"]
    parameters = function_def["parameters"]

    properties = parameters.get("properties", {})
    required_names = parameters.get("required", [])
    optional_names = [name for name in properties.keys() if name not in required_names]

    inputs = {}

    for name, prop in properties.items():
        inputs[name] = {"type": prop.get("type"), "description": prop.get("description", "")}
        if name not in required_names:
            inputs[name]["nullable"] = True

    params_list = []
    for name in required_names:
        params_list.append(name)

    for name in optional_names:
        params_list.append(f"{name} = None")

    params_str = ", ".join(params_list)
    if params_str:
        params_str = ", " + params_str

    func_code = f"""
def forward(self{params_str}):
    params = {{}}
"""
    for name in properties:
        func_code += f"    if {name} is not None: params['{name}'] = {name}\n"

    func_code += f"    return self.env.tool_call('{tool_name}', params)\n"

    local_vars = {}
    exec(func_code, {}, local_vars)
    forward_fn = local_vars["forward"]

    tool_cls = type(
        f"Tool{tool_name}",
        (Tool,),
        {
            "name": tool_name,
            "description": description,
            "inputs": inputs,
            "output_type": "string",
            "forward": forward_fn,
        },
    )

    tool = tool_cls()
    tool.env = env
    return tool
