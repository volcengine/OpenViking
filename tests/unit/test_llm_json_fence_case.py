# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest
from openviking.models.vlm import llm as vlm_llm
from openviking_cli.utils import llm as cli_llm
from pydantic import BaseModel


class Answer(BaseModel):
    answer: int


@pytest.mark.parametrize("parser", [cli_llm, vlm_llm])
@pytest.mark.parametrize("language", ["JSON", "JsOn"])
def test_fenced_json_tag_is_case_insensitive(parser, language):
    response = f'Progress: [1, 2]\n```{language}\n{{"answer": 42}}\n```'
    assert parser.parse_json_from_response(response) == {"answer": 42}


@pytest.mark.parametrize("parser", [cli_llm, vlm_llm])
def test_uppercase_fenced_array_wins_over_earlier_object(parser):
    response = 'Earlier: {"status": "partial"}\n```JSON\n[10, 20]\n```'
    assert parser.parse_json_from_response(response) == [10, 20]


@pytest.mark.parametrize("parser", [cli_llm, vlm_llm])
@pytest.mark.parametrize(
    "response",
    [
        '{"answer": 42}',
        'Progress: [1, 2]\n```json\n{"answer": 42}\n```',
        'Progress: [1, 2]\n```\n{"answer": 42}\n```',
    ],
)
def test_plain_and_existing_fences_remain_supported(parser, response):
    assert parser.parse_json_from_response(response) == {"answer": 42}


@pytest.mark.parametrize("parser", [cli_llm, vlm_llm])
def test_uppercase_fence_supports_model_validation(parser):
    response = 'Progress: [1, 2]\n```JSON\n{"answer": 42}\n```'
    assert parser.parse_json_to_model(response, Answer) == Answer(answer=42)


@pytest.mark.parametrize("parser", [cli_llm, vlm_llm])
def test_malformed_json_repair_still_works(parser):
    assert parser.parse_json_from_response('{"answer": 42,}') == {"answer": 42}


def test_vlm_response_object_with_uppercase_fence():
    response = SimpleNamespace(content='Progress: [1, 2]\n```JSON\n{"answer": 42}\n```')
    assert vlm_llm.parse_json_from_response(response) == {"answer": 42}
