# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Deterministic markdown rendering for one radar pass.

The MVP renderer is deliberately template-driven (no LLM in the critical path):
every claim is a direct projection of a fetched PR row with its number, so the
report is reproducible and cheap. An LLM summary can wrap this later without
changing the collector contract.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from openviking.resource.github_radar.models import PrSignal, fmt_ts, utc_now
from openviking.resource.github_radar.summarizer import PrTriage, RadarSummary

_BUCKET_HEADING = {
    "merged": "## ✅ Merged",
    "open": "## 🟢 Open",
    "closed": "## ⚪ Closed without merge",
}

# Inline punctuation that can break a link, emphasis or a blockquote when
# interpolated from external text (PR titles, labels, LLM prose). Line-start
# markers such as "#" or "-" cannot occur after newline folding, so they are
# deliberately left alone to keep the raw markdown readable.
_MD_SPECIAL = re.compile(r"([\\`*_\[\]()~|>])")


def _md_text(value: Any) -> str:
    """Neutralize external text for safe inline markdown interpolation.

    Line breaks are folded into spaces so a title or LLM note cannot inject
    extra list items or headings, and inline control characters are
    backslash-escaped so crafted titles cannot break links or emphasis.
    """
    text = " ".join(part.strip() for part in str(value or "").splitlines()).strip()
    return _MD_SPECIAL.sub(r"\\\1", text)


def _bucket(pr: PrSignal) -> str:
    if pr.merged_at:
        return "merged"
    if pr.state == "closed":
        return "closed"
    return "open"


def _render_one(pr: PrSignal, triage: Optional[PrTriage] = None) -> List[str]:
    flags = []
    if pr.draft:
        flags.append("draft")
    if pr.labels:
        flags.append(_md_text(",".join(pr.labels[:5])))
    suffix = f" ({' '.join(flags)})" if flags else ""
    lines = [
        f"- [#{pr.number}]({_md_text(pr.url)}) {_md_text(pr.title)}{suffix} "
        f"— @{_md_text(pr.author or 'unknown')}",
        f"  - updated: {pr.updated_at or 'n/a'}",
    ]
    if pr.merged_at:
        lines.append(f"  - merged: {pr.merged_at}")
    if triage is not None:
        if triage.summary_zh:
            lines.append(f"  - 摘要：{_md_text(triage.summary_zh)}")
        if triage.impact:
            lines.append(f"  - 影响面：{_md_text(triage.impact)}（{triage.severity}）")
        if triage.recommended_action:
            lines.append(f"  - 下一步：{_md_text(triage.recommended_action)}")
    return lines


def _render_header(
    repo: str,
    prs: List[PrSignal],
    generated: str,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
) -> List[str]:
    lines = [
        f"# PR Radar — {repo}",
        "",
        f"Generated: {generated} · PRs in this report: {len(prs)}",
    ]
    if window_start and window_end:
        lines.append(f"Window: {window_start} ~ {window_end}")
    lines.append("")
    return lines


def _render_details(
    prs: List[PrSignal], triage: Optional[Dict[int, PrTriage]] = None
) -> List[str]:
    """Deterministic lifecycle-bucketed detail list. Always exhaustive, so the
    report keeps a factual baseline even when the LLM summary is unavailable."""
    groups: dict[str, List[PrSignal]] = {"merged": [], "open": [], "closed": []}
    for pr in prs:
        groups[_bucket(pr)].append(pr)

    sections: List[str] = []
    for key in ("merged", "open", "closed"):
        bucket = groups[key]
        if not bucket:
            continue
        bucket.sort(key=lambda item: item.updated_at, reverse=True)
        sections.append(_BUCKET_HEADING[key])
        sections.append("")
        for pr in bucket:
            row_triage = triage.get(pr.number) if triage else None
            sections.extend(_render_one(pr, row_triage))
        sections.append("")

    if not sections:
        sections = ["_No pull requests in this window._", ""]
    return sections


def _render_topic_sections(summary: RadarSummary, prs: List[PrSignal]) -> List[str]:
    """LLM editorial layer: topic overviews plus highlighted PRs. Every fact
    (title/link/author/timestamps) is still projected from the deterministic
    PrSignal; the model only contributes prose."""
    if summary.synthesis is None:
        return []
    pr_by_number = {pr.number: pr for pr in prs}
    lines = ["## 🧭 主题综述", ""]
    if summary.synthesis.editorial_note:
        lines.extend([f"> {_md_text(summary.synthesis.editorial_note)}", ""])
    for section in summary.synthesis.sections:
        lines.append(f"### {_md_text(section.topic)}")
        lines.append("")
        if section.overview_zh:
            lines.extend([_md_text(section.overview_zh), ""])
        for number in section.highlight_numbers:
            pr = pr_by_number.get(number)
            triage_row = summary.triage.get(number)
            if pr is None:
                continue
            lines.append(
                f"- **[#{pr.number}]({_md_text(pr.url)}) {_md_text(pr.title)}** "
                f"— @{_md_text(pr.author or 'unknown')}"
            )
            if triage_row is not None:
                if triage_row.summary_zh:
                    lines.append(f"  - 摘要：{_md_text(triage_row.summary_zh)}")
                if triage_row.impact:
                    lines.append(
                        f"  - 影响面：{_md_text(triage_row.impact)}（{triage_row.severity}）"
                    )
                if triage_row.recommended_action:
                    lines.append(f"  - 下一步：{_md_text(triage_row.recommended_action)}")
        lines.append("")
    return lines


def render_report(
    repo: str,
    prs: List[PrSignal],
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
) -> str:
    """Render PRs into a markdown report grouped by lifecycle bucket."""
    generated = fmt_ts(utc_now())
    return "\n".join(
        _render_header(repo, prs, generated, window_start, window_end)
        + _render_details(prs)
    )


def render_enriched_report(
    repo: str,
    prs: List[PrSignal],
    summary: RadarSummary,
    window_start: Optional[str] = None,
    window_end: Optional[str] = None,
) -> str:
    """Render an LLM-enriched report: editorial topic layer on top, then the
    exhaustive deterministic detail list with per-PR triage notes."""
    generated = fmt_ts(utc_now())
    lines = _render_header(repo, prs, generated, window_start, window_end)
    if summary.synthesis is not None:
        # Only claim an LLM editorial layer when one was actually produced;
        # a triage-only summary contributes nothing above the detail list.
        lines.append(
            "> 上方主题综述由 LLM 基于本期 PR 标题与正文生成；"
            "PR 标题、链接、作者、时间等事实以文末完整明细为准。"
        )
        lines.append("")
    lines.extend(_render_topic_sections(summary, prs))
    lines.append("## 📋 完整明细")
    lines.append("")
    lines.extend(_render_details(prs, summary.triage))
    return "\n".join(lines)


def report_stamp(moment: datetime | None = None) -> str:
    """Filesystem-safe timestamp for archived reports (one watch may run many times a day)."""
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def joined_numbers(prs: Iterable[PrSignal]) -> List[int]:
    return sorted({pr.number for pr in prs})
