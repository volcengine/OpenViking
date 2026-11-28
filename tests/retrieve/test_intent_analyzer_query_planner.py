from typing import cast
from unittest.mock import AsyncMock

import pytest

from openviking.config.vlm import VLMHandle
from openviking.message import ContextPart, Message, TextPart
from openviking.models.vlm.backends.litellm_vlm import OLLAMA_DEFAULT_NUM_CTX
from openviking.retrieve import intent_analyzer as intent_module
from openviking.retrieve.intent_analyzer import IntentAnalyzer
from openviking.session.checkpoints import CheckpointPlanner
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.retrieve.types import ContextType
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


class RecordingModel:
    def __init__(
        self,
        response: str,
        model: str | None = None,
        *,
        max_input_tokens: int | None = None,
    ):
        self.response = response
        self.model = model
        self.max_input_tokens = max_input_tokens
        self.prompts: list[str] = []

    async def get_completion_async(self, prompt: str):
        self.prompts.append(prompt)
        if self.max_input_tokens is not None:
            tokens = estimate_text_tokens(prompt)
            if tokens > self.max_input_tokens:
                raise ValueError(f"Planner input exceeds context window: {tokens} tokens")
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
        ("ov_intent_analysis_rl", "retrieval.ov_intent_analysis_rl"),
        ("ollama/qwen3.5:4b", "retrieval.intent_analysis"),
    ],
)
async def test_intent_analyzer_uses_the_bundled_prompt_for_the_resolved_model(
    monkeypatch, model, prompt_id
):
    planner = RecordingModel(_query_plan_response("planned query"), model=model)
    overview = "正在调查数据库连接问题"
    current_message = "查一下刚才那个错误对应的文档"
    checkpoint_abstract = "数据库报告 SQLSTATE 53300，连接池大小为 12。"
    checkpoint_uri = "viking://session/test/checkpoints/in-window"
    messages = [
        Message(id="1", role="user", parts=[TextPart("old conversation text")]),
        Message(
            id="2",
            role="assistant",
            parts=[ContextPart(abstract="out-of-window checkpoint abstract")],
            message_kind="checkpoint",
        ),
        Message(
            id="3",
            role="user",
            parts=[
                TextPart("刚才的连接失败了。"),
                ContextPart(abstract="ordinary context abstract"),
            ],
        ),
        Message(
            id="4",
            role="assistant",
            parts=[
                ContextPart(abstract=" \t\n"),
                ContextPart(uri=checkpoint_uri, abstract=checkpoint_abstract),
                ContextPart(abstract="later checkpoint abstract"),
            ],
            message_kind="checkpoint",
        ),
        Message(
            id="5",
            role="assistant",
            parts=[ContextPart(abstract=" \t\n")],
            message_kind="checkpoint",
        ),
        Message(
            id="6",
            role="user",
            parts=[ContextPart(abstract="context-only ordinary message")],
        ),
        Message(
            id="7",
            role="assistant",
            parts=[
                TextPart("我已经记录了错误。"),
                ContextPart(abstract="unused checkpoint abstract"),
            ],
            message_kind="checkpoint",
        ),
    ]
    rendered = []
    render_prompt = intent_module.render_prompt

    def record_prompt(template_id, variables):
        rendered.append(template_id)
        return render_prompt(template_id, variables)

    monkeypatch.setattr(intent_module, "render_prompt", record_prompt)
    result = await IntentAnalyzer(query_planner=planner).analyze(
        compression_summary=overview,
        messages=messages,
        current_message=current_message,
    )

    assert rendered == [prompt_id]
    [prompt] = planner.prompts
    assert prompt.count(checkpoint_abstract) == 1
    assert (
        f"### Session Summary\n{overview}\n\n"
        "### Recent Conversation\n"
        "[user]: 刚才的连接失败了。\n"
        f"[assistant]: {checkpoint_abstract}\n"
        "[assistant]: 我已经记录了错误。\n\n"
        f"### Current Message\n{current_message}"
    ) in prompt
    for excluded in (
        "old conversation text",
        "out-of-window checkpoint abstract",
        "ordinary context abstract",
        "context-only ordinary message",
        checkpoint_uri,
        "later checkpoint abstract",
        "unused checkpoint abstract",
    ):
        assert excluded not in prompt
    [query] = result.queries
    assert query.query == "planned query"
    assert query.context_type == ContextType.MEMORY
    assert query.intent == "test intent"
    assert query.priority == 1
    assert result.reasoning == "test"


@pytest.mark.asyncio
async def test_intent_analyzer_plans_with_a_long_restored_legacy_checkpoint():
    anchor = Message(id="user", role="user", parts=[TextPart("排查连接池故障")])
    refs = []
    metadata = {}
    abstracts = []
    for index in range(1, 25):
        archive_id = f"archive_{index:03d}"
        archive_uri = f"viking://session/test/history/{archive_id}"
        refs.append({"index": index, "archive_id": archive_id, "archive_uri": archive_uri})
        abstract = f"归档开始 {index}。" + "连接池排查记录。" * 80 + f"归档结束 {index}。"
        abstracts.append(abstract)
        metadata[archive_uri] = {
            "checkpoints": [
                {
                    "turn_anchor_message_id": anchor.id,
                    "source_message_ids": [f"assistant-{index}"],
                    "abstract": abstract,
                }
            ]
        }
    archives = AsyncMock()
    archives.completed_refs.return_value = list(reversed(refs))
    archives.read_meta.side_effect = metadata.__getitem__
    messages = await CheckpointPlanner(archives).insert_terminal_checkpoints([anchor], refs[-1])

    _, checkpoint = messages
    [part] = checkpoint.parts
    assert isinstance(part, ContextPart)
    restored = part.abstract
    assert restored == "\n\n".join(abstracts)
    assert estimate_text_tokens(restored) > OLLAMA_DEFAULT_NUM_CTX

    planner = RecordingModel(
        _query_plan_response("连接池故障排查"),
        model="ollama/guoxuter/ov_intent_analysis_sft:v7_q8",
        max_input_tokens=OLLAMA_DEFAULT_NUM_CTX,
    )
    plan = await IntentAnalyzer(query_planner=cast(VLMHandle, planner)).analyze(
        compression_summary="正在调查数据库连接问题",
        messages=messages,
        current_message="查一下刚才那个错误对应的文档",
    )

    [query] = plan.queries
    assert query.query == "连接池故障排查"
    [prompt] = planner.prompts
    assert "归档开始 1。" in prompt
    assert "归档结束 24。" in prompt
    assert abstracts[11] not in prompt
