# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Deterministic markdown rendering for one radar pass.

The MVP renderer is deliberately template-driven (no LLM in the critical path):
every claim is a direct projection of a fetched PR row with its number, so the
report is reproducible and cheap. An LLM summary can wrap this later without
changing the collector contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

from openviking.integrations.github_radar.models import PrSignal, fmt_ts, utc_now


def _bucket(pr: PrSignal) -> str:
    if pr.merged_at:
        return "merged"
    if pr.state == "closed":
        return "closed"
    return "open"


def _render_one(pr: PrSignal) -> List[str]:
    flags = []
    if pr.draft:
        flags.append("draft")
    if pr.labels:
        flags.append(",".join(pr.labels[:5]))
    suffix = f" ({' '.join(flags)})" if flags else ""
    lines = [
        f"- [#{pr.number}]({pr.url}) {pr.title}{suffix} — @{pr.author or 'unknown'}",
        f"  - updated: {pr.updated_at or 'n/a'}",
    ]
    if pr.merged_at:
        lines.append(f"  - merged: {pr.merged_at}")
    return lines


def render_report(repo: str, prs: List[PrSignal]) -> str:
    """Render PRs into a markdown report grouped by lifecycle bucket."""
    generated = fmt_ts(utc_now())
    groups: dict[str, List[PrSignal]] = {"merged": [], "open": [], "closed": []}
    for pr in prs:
        groups[_bucket(pr)].append(pr)

    title = [
        f"# PR Radar — {repo}",
        "",
        f"Generated: {generated} · PRs in this report: {len(prs)}",
        "",
    ]

    sections: List[str] = []
    heading = {
        "merged": "## ✅ Merged",
        "open": "## 🟢 Open",
        "closed": "## ⚪ Closed without merge",
    }
    for key in ("merged", "open", "closed"):
        bucket = groups[key]
        if not bucket:
            continue
        bucket.sort(key=lambda item: item.updated_at, reverse=True)
        sections.append(heading[key])
        sections.append("")
        for pr in bucket:
            sections.extend(_render_one(pr))
        sections.append("")

    if not sections:
        sections = ["_No pull requests in this window._", ""]

    return "\n".join(title + sections)


def report_stamp(moment: datetime | None = None) -> str:
    """Filesystem-safe timestamp for archived reports (one watch may run many times a day)."""
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def joined_numbers(prs: Iterable[PrSignal]) -> List[int]:
    return sorted({pr.number for pr in prs})
