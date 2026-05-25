from types import SimpleNamespace

import pytest

from openviking.retrieve import intent_analyzer as intent_module
from openviking.retrieve.intent_analyzer import IntentAnalyzer
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


class RecordingModel:
    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    async def get_completion_async(self, prompt: str):
        self.prompts.append(prompt)
        return self.response


def _query_plan_response(query: str) -> str:
    return f"""{{
      "reasoning": "test",
      "queries": [
        {{
          "query": "{query}",
          "context_type": "memory",
          "intent": "test intent",
          "priority": 1
        }}
      ]
    }}"""


def test_openviking_config_accepts_query_planner_vlm_config():
    config = OpenVikingConfig.from_dict(
        {
            "query_planner": {
                "provider": "litellm",
                "model": "ollama/qwen3.5:4b",
                "api_base": "http://127.0.0.1:11434",
                "extra_request_body": {"think": False},
            }
        }
    )

    assert config.query_planner is not None
    assert config.query_planner.provider == "litellm"
    assert config.query_planner.model == "ollama/qwen3.5:4b"
    assert config.query_planner.api_base == "http://127.0.0.1:11434"
    assert config.query_planner.extra_request_body == {"think": False}


def test_openviking_config_uses_vlm_when_query_planner_is_absent_or_empty():
    missing_config = OpenVikingConfig.from_dict({})
    assert missing_config.get_query_planner() is missing_config.vlm

    empty_config = OpenVikingConfig.from_dict({"query_planner": {}})
    assert empty_config.get_query_planner() is empty_config.vlm


@pytest.mark.asyncio
async def test_intent_analyzer_uses_query_planner_when_configured(monkeypatch):
    planner = RecordingModel(_query_plan_response("planned query"))
    vlm = RecordingModel(_query_plan_response("vlm query"))
    config = SimpleNamespace(get_query_planner=lambda: planner)

    monkeypatch.setattr(intent_module, "get_openviking_config", lambda: config)

    result = await IntentAnalyzer().analyze(
        compression_summary="",
        messages=[],
        current_message="where is my preference?",
    )

    assert result.queries[0].query == "planned query"
    assert len(planner.prompts) == 1
    assert vlm.prompts == []


@pytest.mark.asyncio
async def test_intent_analyzer_falls_back_to_vlm_without_query_planner(monkeypatch):
    vlm = RecordingModel(_query_plan_response("vlm query"))
    config = SimpleNamespace(get_query_planner=lambda: vlm)

    monkeypatch.setattr(intent_module, "get_openviking_config", lambda: config)

    result = await IntentAnalyzer().analyze(
        compression_summary="",
        messages=[],
        current_message="where is my preference?",
    )

    assert result.queries[0].query == "vlm query"
    assert len(vlm.prompts) == 1
