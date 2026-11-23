import pytest

from openviking.retrieve import intent_analyzer as intent_module
from openviking.retrieve.intent_analyzer import IntentAnalyzer
from openviking_cli.retrieve.types import ContextType
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


class RecordingModel:
    def __init__(self, response: str, model: str | None = None):
        self.response = response
        self.model = model
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
async def test_intent_analyzer_normalizes_non_string_reasoning(monkeypatch):
    planner = RecordingModel(
        """{
          "reasoning": {"cause": "malformed output"},
          "queries": [
            {
              "query": "planned query",
              "context_type": "memory",
              "intent": "test intent",
              "priority": 1
            }
          ]
        }"""
    )
    monkeypatch.setattr(intent_module, "render_prompt", lambda prompt_id, variables: "prompt")

    result = await IntentAnalyzer(query_planner=planner).analyze(
        compression_summary="",
        messages=[],
        current_message="where is my preference?",
    )

    assert result.reasoning == "{'cause': 'malformed output'}"


@pytest.mark.asyncio
async def test_intent_analyzer_handles_bare_array_response(monkeypatch):
    planner = RecordingModel(
        """[
          {
            "query": "bare array query",
            "context_type": "memory",
            "intent": "test intent",
            "priority": 1
          }
        ]"""
    )
    monkeypatch.setattr(intent_module, "render_prompt", lambda prompt_id, variables: "prompt")

    result = await IntentAnalyzer(query_planner=planner).analyze(
        compression_summary="",
        messages=[],
        current_message="where is my preference?",
    )

    assert len(result.queries) == 1
    assert result.queries[0].query == "bare array query"
    assert result.reasoning == ""


@pytest.mark.asyncio
async def test_intent_analyzer_handles_compact_object_response(monkeypatch):
    planner = RecordingModel(
        """{
          "queries": [
            {
              "query": "compact query",
              "context_type": "resource",
              "priority": 2
            }
          ]
        }"""
    )
    monkeypatch.setattr(intent_module, "render_prompt", lambda prompt_id, variables: "prompt")

    result = await IntentAnalyzer(query_planner=planner).analyze(
        compression_summary="",
        messages=[],
        current_message="find the relevant documentation",
    )

    assert len(result.queries) == 1
    assert result.queries[0].query == "compact query"
    assert result.queries[0].context_type == ContextType.RESOURCE
    assert result.queries[0].intent == ""
    assert result.queries[0].priority == 2
    assert result.reasoning == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "prompt_id"),
    [
        ("ollama/guoxuter/ov_intent_analysis_sft:v4_q8", "retrieval.ov_intent_analysis_sft_v4"),
        ("ollama/guoxuter/ov_intent_analysis_sft:v7_q8", "retrieval.ov_intent_analysis_sft_v7"),
        ("ollama/qwen3.5:4b", "retrieval.intent_analysis"),
    ],
)
async def test_intent_analyzer_uses_the_bundled_prompt_for_the_resolved_model(
    monkeypatch, model, prompt_id
):
    planner = RecordingModel(_query_plan_response("planned query"), model=model)
    rendered = []
    render_prompt = intent_module.render_prompt

    def record_prompt(template_id, variables):
        rendered.append(template_id)
        return render_prompt(template_id, variables)

    monkeypatch.setattr(intent_module, "render_prompt", record_prompt)
    result = await IntentAnalyzer(query_planner=planner).analyze(
        compression_summary="The user prefers explicit rollback procedures.",
        messages=[],
        current_message="where is my preference?",
    )

    assert rendered == [prompt_id]
    [prompt] = planner.prompts
    assert "where is my preference?" in prompt
    [query] = result.queries
    assert query.query == "planned query"
    assert query.context_type == ContextType.MEMORY
    assert query.intent == "test intent"
    assert query.priority == 1
    assert result.reasoning == "test"
