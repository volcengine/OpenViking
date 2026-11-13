# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.resource.github_radar.summarizer import RadarSummarizer, batches
from tests.utils.radar_fixtures import config, pr


def explanation(number, topic):
    return {
        "number": number,
        "summary": "中文解读",
        "topic": topic,
        "priority": "medium",
        "impact": "影响",
        "advice": "建议",
    }


def test_batches_bounds_oversized_single_record():
    result = batches([{"number": 1, "body": "中文" * 10000}], 2000)
    assert len(result) == 1
    assert len(json.dumps(result[0], ensure_ascii=False).encode()) <= 2000
    assert result[0][0]["number"] == 1


async def test_summarize_rejects_unknown_ids_and_cross_topic_highlights():
    vlm = SimpleNamespace(
        get_completion_async=AsyncMock(
            side_effect=[
                json.dumps(
                    {
                        "prs": [
                            explanation(1, "storage"),
                            explanation(2, "gateway"),
                            explanation(99, "storage"),
                        ]
                    }
                ),
                json.dumps({"overview": "存储", "highlights": [1, 2, 99, True]}),
                json.dumps({"overview": "网关", "highlights": [2, 1]}),
            ]
        )
    )
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=vlm))
    result = await RadarSummarizer(resolver, "tenant").summarize([pr(1), pr(2)], config())
    assert result.mode == "llm"
    assert set(result.explanations) == {1, 2}
    assert result.highlights == {"storage": [1], "gateway": [2]}
    resolver.get_vlm.assert_awaited_once_with("tenant")
    assert vlm.get_completion_async.await_count == 3


async def test_summarize_topic_failure_keeps_pr_explanations():
    vlm = SimpleNamespace(
        get_completion_async=AsyncMock(
            side_effect=[
                json.dumps({"prs": [explanation(1, "storage")]}),
                OSError("model unavailable"),
            ]
        )
    )
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=vlm))
    result = await RadarSummarizer(resolver, "tenant").summarize([pr()], config())
    assert result.mode == "pr_summaries"
    assert list(result.explanations) == [1]
    assert result.overviews == {}
    assert vlm.get_completion_async.await_count == 2


async def test_summarize_disabled_and_total_failure_use_template():
    resolver = SimpleNamespace(get_vlm=AsyncMock(side_effect=OSError("unavailable")))
    summarizer = RadarSummarizer(resolver, "tenant")
    assert (await summarizer.summarize([pr()], config(summary_enabled=False))).mode == "template"
    resolver.get_vlm.assert_not_awaited()
    assert (await summarizer.summarize([pr()], config())).mode == "template"
    resolver.get_vlm.assert_awaited_once()
