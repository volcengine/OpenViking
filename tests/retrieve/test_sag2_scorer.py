# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.retrieve.sag2_scorer import Sag2Scorer


class Transport:
    def __init__(self, answers=None):
        self.answers = answers
        self.requests = []

    def request(self, *, state, questions):
        self.requests.append((state, questions))
        if self.answers is not None:
            return {"answers": self.answers}, 0.01, 200
        return (
            {
                "model": "jev-test",
                "answers": {key: {"type": "noul", "noul": 0.75} for key in questions},
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            0.01,
            200,
        )


class Client:
    provider = "jev"
    mode = "noul"
    model_name = "jev-test"

    def __init__(self, answers=None):
        self._transport = Transport(answers)
        self.usage = None

    def _estimate_tokens(self, text):
        return max(1, len(str(text)) // 4)

    def update_token_usage(self, **kwargs):
        self.usage = kwargs


def _candidate(record_id="a", text="evidence"):
    return {
        "id": record_id,
        "name": record_id,
        "uri": f"viking://resources/{record_id}",
        "level": 0,
        "abstract": text,
    }


@pytest.mark.asyncio
async def test_scorer_payload_usage_and_strict_ids():
    client = Client()
    scorer = Sag2Scorer(client, max_context_tokens=10000)
    result = await scorer.score("question", [_candidate()], [])
    assert result[0]["_score"] == 0.75
    state, questions = client._transport.requests[0]
    assert state["original_question"] == "question"
    assert questions["candidate_0"]["instructions"]["candidate_id"] == "a"
    assert client.usage["prompt_tokens"] == 10

    bad = Client({"unexpected": {"type": "noul", "noul": 0.5}})
    with pytest.raises(ValueError, match="incomplete or unexpected"):
        await Sag2Scorer(bad, max_context_tokens=10000).score("q", [_candidate()], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, float("nan"), -0.1, 1.1, "0.5"])
async def test_scorer_rejects_invalid_probability(value):
    client = Client({"candidate_0": {"type": "noul", "noul": value}})
    with pytest.raises(ValueError):
        await Sag2Scorer(client, max_context_tokens=10000).score("q", [_candidate()], [])


@pytest.mark.asyncio
async def test_scorer_batches_without_truncating_oversized_candidate():
    client = Client()
    scorer = Sag2Scorer(client, max_context_tokens=200)
    candidates = [_candidate("a", "a" * 120), _candidate("b", "b" * 120)]
    await scorer.score("q", candidates, [])
    assert len(client._transport.requests) == 2
    with pytest.raises(ValueError, match="not truncated"):
        await Sag2Scorer(client, max_context_tokens=20).score("q", [_candidate(text="x" * 200)], [])


def test_scorer_requires_noul_jev():
    client = Client()
    client.mode = "choice"
    with pytest.raises(ValueError, match="mode='noul'"):
        Sag2Scorer(client, max_context_tokens=100)
