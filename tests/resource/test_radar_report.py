# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.resource.github_radar.models import PrSignal
from openviking.resource.github_radar.report import (
    _md_text,
    render_enriched_report,
    render_report,
    report_stamp,
)
from openviking.resource.github_radar.summarizer import (
    PrTriage,
    RadarSummary,
    RadarSynthesis,
    TopicSection,
)

REPO = "volcengine/OpenViking"
_TS = "2026-11-10T08:00:00Z"


def _pr(number: int = 11, **overrides) -> PrSignal:
    fields = {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/volcengine/OpenViking/pull/{number}",
        "state": "open",
        "draft": False,
        "author": "alice",
        "created_at": _TS,
        "updated_at": _TS,
        "merged_at": None,
        "closed_at": None,
        "labels": [],
        "body": "hello",
    }
    fields.update(overrides)
    return PrSignal(**fields)


def _triage(number: int = 11, **overrides) -> PrTriage:
    fields = {
        "number": number,
        "topic": "gateway",
        "severity": "high",
        "summary_zh": "摘要内容",
        "impact": "影响面",
        "recommended_action": "下一步动作",
    }
    fields.update(overrides)
    return PrTriage(**fields)


def test_md_text_folds_newlines_and_escapes_inline_markdown():
    safe = _md_text("line1\nline2 *bold* [x](y) `code`")
    assert "\n" not in safe
    assert "line1 line2" in safe
    assert "\\*bold\\*" in safe
    assert "\\[x\\]\\(y\\)" in safe
    assert "\\`code\\`" in safe


def test_report_escapes_markdown_in_pr_title():
    pr = _pr(title="evil *bold* [link](javascript:alert(1))\n- injected item")
    report = render_report(REPO, [pr])

    # The embedded newline cannot inject a second list item: the folded text
    # stays on the PR's own line instead of starting a new "- " line.
    assert not any(
        line.startswith("- injected item") for line in report.splitlines()
    )
    assert "\\*bold\\*" in report
    # Link-destination parens are neutralized, no raw javascript link.
    assert "](javascript:alert(1))" not in report


def test_report_keeps_clean_markdown_for_normal_titles():
    report = render_report(REPO, [_pr(title="fix: gateway routing")])
    assert (
        "[#11](https://github.com/volcengine/OpenViking/pull/11) fix: gateway routing"
        in report
    )


def test_triage_only_report_omits_llm_disclaimer_and_topics():
    summary = RadarSummary(triage={11: _triage()}, synthesis=None)
    report = render_enriched_report(REPO, [_pr()], summary)

    assert "由 LLM" not in report
    assert "主题综述" not in report
    # Per-PR triage notes still reach the deterministic detail section.
    assert "摘要内容" in report
    assert "完整明细" in report


def test_llm_prose_newlines_are_folded_in_detail_lines():
    summary = RadarSummary(
        triage={
            11: _triage(summary_zh="第一行\n第二行", impact="", recommended_action="")
        },
        synthesis=None,
    )
    report = render_enriched_report(REPO, [_pr()], summary)
    assert "第一行 第二行" in report


def test_report_stamp_is_unique_for_consecutive_calls():
    # Even within the same UTC second, archive filenames must not collide.
    stamps = {report_stamp() for _ in range(10)}
    assert len(stamps) == 10


def test_synthesis_report_keeps_disclaimer_and_topic_layer():
    synthesis = RadarSynthesis(
        editorial_note="编者按",
        sections=[
            TopicSection(topic="gateway", overview_zh="综述", highlight_numbers=[11])
        ],
    )
    report = render_enriched_report(
        REPO, [_pr()], RadarSummary(triage={11: _triage()}, synthesis=synthesis)
    )

    assert "由 LLM" in report
    assert "主题综述" in report
    assert "编者按" in report
