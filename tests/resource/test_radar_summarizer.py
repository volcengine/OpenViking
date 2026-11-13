# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.resource.github_radar.models import PrSignal
from openviking.resource.github_radar.summarizer import RadarSummarizer

REPO = "volcengine/OpenViking"


def _pr(number: int, body: str = "hello") -> PrSignal:
    ts = "2026-11-10T08:00:00Z"
    return PrSignal(
        number=number,
        title=f"PR {number}",
        url=f"https://github.com/volcengine/OpenViking/pull/{number}",
        state="open",
        draft=False,
        author="alice",
        created_at=ts,
        updated_at=ts,
        merged_at=None,
        closed_at=None,
        labels=[],
        body=body,
    )


class FakeVLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def get_completion_async(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self._responses[0], Exception):
            raise self._responses.pop(0)
        return json.dumps(self._responses.pop(0), ensure_ascii=False)


class FakeResolver:
    def __init__(self, handle):
        self._handle = handle
        self.requested_accounts: list[str] = []

    async def get_vlm(self, account_id):
        self.requested_accounts.append(account_id)
        return self._handle


def _triage_response(numbers):
    return {
        "items": [
            {
                "number": number,
                "topic": "gateway",
                "severity": "high",
                "summary_zh": f"摘要 {number}",
                "impact": f"影响 {number}",
                "recommended_action": f"动作 {number}",
            }
            for number in numbers
        ]
    }


def _synthesis_response():
    return {
        "editorial_note": "本期以 gateway 改造为主",
        "sections": [
            {
                "topic": "gateway",
                "overview_zh": "多个 gateway PR 共同收敛工具访问控制",
                "highlight_numbers": [11, 99],
            }
        ],
    }


@pytest.mark.asyncio
async def test_summarize_drops_unknown_pr_numbers():
    handle = FakeVLM(
        [
            # 99 is not in the collected set and must be discarded.
            _triage_response([11, 12, 99]),
            _synthesis_response(),
        ]
    )
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    summary = await summarizer.summarize(REPO, [_pr(11), _pr(12)], "acct-1")

    assert summary is not None
    assert set(summary.triage) == {11, 12}
    assert summary.synthesis is not None
    # Unknown highlight number is filtered; only the real PR survives.
    assert summary.synthesis.sections[0].highlight_numbers == [11]


@pytest.mark.asyncio
async def test_summarize_normalizes_bad_severity():
    handle = FakeVLM(
        [
            {
                "items": [
                    {
                        "number": 11,
                        "topic": "gateway",
                        "severity": "catastrophic",
                        "summary_zh": "摘要",
                        "impact": "影响",
                    }
                ]
            },
            {
                "editorial_note": "安静的一天",
                "sections": [
                    {"topic": "gateway", "overview_zh": "综述", "highlight_numbers": [11]}
                ],
            },
        ]
    )
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    summary = await summarizer.summarize(REPO, [_pr(11)], "acct-1")

    assert summary is not None
    assert summary.triage[11].severity == "low"


@pytest.mark.asyncio
async def test_vlm_failure_returns_none():
    handle = FakeVLM([RuntimeError("model unavailable")])
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    assert await summarizer.summarize(REPO, [_pr(11)], "acct-1") is None


@pytest.mark.asyncio
async def test_synthesis_failure_keeps_triage_only():
    handle = FakeVLM(
        [
            _triage_response([11]),
            # Sections reference an unknown topic -> synthesis rejected.
            {
                "editorial_note": "x",
                "sections": [
                    {"topic": "nonexistent", "overview_zh": "y", "highlight_numbers": [11]}
                ],
            },
        ]
    )
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    summary = await summarizer.summarize(REPO, [_pr(11)], "acct-1")

    assert summary is not None
    assert 11 in summary.triage
    assert summary.synthesis is None


@pytest.mark.asyncio
async def test_batching_splits_oversized_payloads():
    big_body = "x" * 20_000
    prs = [_pr(11, body=big_body), _pr(12, body=big_body)]
    handle = FakeVLM(
        [
            _triage_response([11]),
            _triage_response([12]),
            _synthesis_response(),
        ]
    )
    # Each PR serializes past the small budget, so one triage call per PR.
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=500)
    summary = await summarizer.summarize(REPO, prs, "acct-1")

    assert summary is not None
    assert set(summary.triage) == {11, 12}
    # Two triage calls + one synthesis call.
    assert len(handle.prompts) == 3


@pytest.mark.asyncio
async def test_empty_pr_list_is_a_noop():
    handle = FakeVLM([])
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    assert await summarizer.summarize(REPO, [], "acct-1") is None
    assert handle.prompts == []


@pytest.mark.asyncio
async def test_triage_batch_failure_keeps_earlier_batch_rows():
    big_body = "x" * 20_000
    prs = [_pr(11, body=big_body), _pr(12, body=big_body)]
    handle = FakeVLM(
        [
            _triage_response([11]),
            RuntimeError("model unavailable"),  # second triage batch fails
            _synthesis_response(),
        ]
    )
    # Small budget forces one triage call per PR.
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=500)
    summary = await summarizer.summarize(REPO, prs, "acct-1")

    assert summary is not None
    # Rows from the successful first batch survive; the failed PR is skipped.
    assert set(summary.triage) == {11}
    assert summary.synthesis is not None


@pytest.mark.asyncio
async def test_synthesis_exception_keeps_triage_only_result():
    handle = FakeVLM(
        [
            _triage_response([11]),
            RuntimeError("model unavailable"),  # synthesis transport failure
        ]
    )
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    summary = await summarizer.summarize(REPO, [_pr(11)], "acct-1")

    assert summary is not None
    assert 11 in summary.triage
    assert summary.synthesis is None


@pytest.mark.asyncio
async def test_scalar_items_skips_batch_without_typeerror():
    handle = FakeVLM([{"items": 123}])
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)

    # Degrades to None (template report) instead of raising TypeError.
    assert await summarizer.summarize(REPO, [_pr(11)], "acct-1") is None


@pytest.mark.asyncio
async def test_scalar_sections_keeps_triage_only_result():
    handle = FakeVLM(
        [
            _triage_response([11]),
            {"editorial_note": "n", "sections": 5},  # scalar, not a list
        ]
    )
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    summary = await summarizer.summarize(REPO, [_pr(11)], "acct-1")

    # The triage-only contract survives a malformed synthesis payload.
    assert summary is not None
    assert 11 in summary.triage
    assert summary.synthesis is None


@pytest.mark.asyncio
async def test_triage_allowlist_is_scoped_to_each_batch():
    big_body = "x" * 20_000  # forces one PR per triage batch
    prs = [_pr(11, body=big_body), _pr(12, body=big_body)]

    class Handle:
        def __init__(self): self.calls = 0
        async def get_completion_async(self, prompt):
            self.calls += 1
            if self.calls == 1:
                # Batch 1 (PR #11): grounded summary.
                return json.dumps({"items": [
                    {"number": 11, "topic": "gateway", "severity": "high",
                     "summary_zh": "批次1的正确摘要", "impact": "i",
                     "recommended_action": ""}
                ]}, ensure_ascii=False)
            # Batch 2 (PR #12) wrongly names #11: must be rejected.
            return json.dumps({"items": [
                {"number": 12, "topic": "gateway", "severity": "low",
                 "summary_zh": "12 摘要", "impact": "i", "recommended_action": ""},
                {"number": 11, "topic": "gateway", "severity": "low",
                 "summary_zh": "越权覆盖", "impact": "i", "recommended_action": ""}
            ]}, ensure_ascii=False)

    class Resolver:
        async def get_vlm(self, account_id): return Handle()

    summary = await RadarSummarizer(Resolver(), batch_bytes=500).summarize(
        REPO, prs, "acct-1"
    )
    assert summary is not None
    # The batch-1 grounded row survives untouched.
    assert summary.triage[11].summary_zh == "批次1的正确摘要"
    assert summary.triage[11].severity == "high"
    assert summary.triage[12].summary_zh == "12 摘要"


@pytest.mark.asyncio
async def test_synthesis_highlights_reject_prs_without_triage_input():
    # Both PRs are collected, but triage only produces a row for #11, so the
    # synthesis payload contains just #11. A highlight naming #12 must be
    # filtered against the actual synthesis input, not all collected PRs.
    class Handle:
        def __init__(self): self.calls = 0
        async def get_completion_async(self, prompt):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"items": [
                    {"number": 11, "topic": "gateway", "severity": "high",
                     "summary_zh": "x", "impact": "i", "recommended_action": ""}
                ]})
            return json.dumps({"editorial_note": "n", "sections": [
                {"topic": "gateway", "overview_zh": "o",
                 "highlight_numbers": [11, 12]}
            ]})

    class Resolver:
        async def get_vlm(self, account_id): return Handle()

    summary = await RadarSummarizer(Resolver(), batch_bytes=60_000).summarize(
        REPO, [_pr(11), _pr(12)], "acct-1"
    )
    assert summary is not None and summary.synthesis is not None
    assert summary.synthesis.sections[0].highlight_numbers == [11]


@pytest.mark.asyncio
async def test_scalar_highlight_numbers_is_treated_as_empty():
    handle = FakeVLM(
        [
            _triage_response([11]),
            {
                "editorial_note": "n",
                "sections": [
                    {
                        "topic": "gateway",
                        "overview_zh": "x",
                        "highlight_numbers": 99,  # scalar, not a list
                    }
                ],
            },
        ]
    )
    summarizer = RadarSummarizer(FakeResolver(handle), batch_bytes=60_000)
    summary = await summarizer.summarize(REPO, [_pr(11)], "acct-1")

    assert summary is not None
    assert summary.synthesis is not None
    assert summary.synthesis.sections[0].highlight_numbers == []
