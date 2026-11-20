from types import SimpleNamespace

import pytest

from openviking.retrieve import intent_analyzer as intent_module
from openviking.retrieve.intent_analyzer import IntentAnalyzer
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


class RecordingModel:
    def __init__(self, response: str, model: str | None = None):
        self.response = response
        self.model = model
        self.prompts: list[str] = []

    async def get_completion_async(self, prompt: str):
        self.prompts.append(prompt)
        return self.response


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

    planner = config.get_query_planner()
    assert planner is config.query_planner
    assert planner.provider == "litellm"
    assert planner.model == "ollama/qwen3.5:4b"
    assert planner.api_base == "http://127.0.0.1:11434"
    assert planner.extra_request_body == {"think": False}


def test_openviking_config_uses_vlm_when_query_planner_is_absent_or_empty():
    for config_dict in ({}, {"query_planner": {}}):
        config = OpenVikingConfig.from_dict(config_dict)
        assert config.get_query_planner() is config.vlm


@pytest.mark.parametrize(
    ("model", "prompt_id"),
    [
        ("ollama/guoxuter/ov_intent_analysis_sft:v7_q8", "retrieval.ov_intent_analysis_sft_v7"),
        ("ollama/guoxuter/ov_intent_analysis_sft:v4_q8", "retrieval.ov_intent_analysis_sft_v4"),
        ("ollama/qwen3.5:4b", "retrieval.intent_analysis"),
    ],
)
def test_query_planner_selects_bundled_prompt(model, prompt_id):
    planner = SimpleNamespace(model=model)
    assert intent_module.resolve_intent_analysis_prompt_id(planner) == prompt_id


@pytest.mark.parametrize(
    ("model", "response", "expected"),
    [
        pytest.param(
            "ollama/guoxuter/ov_intent_analysis_sft:v7_q8",
            '[{"query": "deployment preferences", "context_type": "memory", "priority": 1}]',
            ("deployment preferences", "memory", "", 1, ""),
            id="bare-array",
        ),
        pytest.param(
            "ollama/guoxuter/ov_intent_analysis_sft:v4_q8",
            '{"queries": [{"query": "deployment guide", "context_type": "resource", "priority": 2}]}',
            ("deployment guide", "resource", "", 2, ""),
            id="compact-object",
        ),
        pytest.param(
            "ollama/qwen3.5:4b",
            '{"reasoning": {"cause": "missing runbook"}, "queries": '
            '[{"query": "deployment runbook", "context_type": "resource", "intent": "find steps"}]}',
            ("deployment runbook", "resource", "find steps", 3, "{'cause': 'missing runbook'}"),
            id="non-string-reasoning",
        ),
    ],
)
async def test_intent_analyzer_renders_request_and_normalizes_planner_output(
    model, response, expected
):
    planner = RecordingModel(response, model=model)
    result = await IntentAnalyzer(query_planner=planner).analyze(
        compression_summary="We deployed the search service yesterday.",
        messages=[],
        current_message="How do we deploy it again?",
    )

    (prompt,) = planner.prompts
    assert "We deployed the search service yesterday." in prompt
    assert "How do we deploy it again?" in prompt
    (query,) = result.queries
    assert (
        query.query,
        query.context_type.value,
        query.intent,
        query.priority,
        result.reasoning,
    ) == expected
