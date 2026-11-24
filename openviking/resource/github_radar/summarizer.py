# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""LLM-backed summarization for PR radar reports.

Two stages, mirroring the loopx triage/rank split:

  1. triage: batched structured calls, one per byte-bounded slice of PRs,
     producing a per-PR Chinese summary, topic, severity and impact note;
  2. synthesis: one call over every triage row for topic grouping, an
     editorial note and per-topic highlights.

The summarizer is strictly best-effort: every failure mode degrades to
``None`` (or a triage-only result when the second stage fails), so the caller
can render the deterministic template report instead. A summary error must
never reach the hosting watch run.

Hallucination guard: the model only emits prose and PR numbers. Every number
is checked against the collected set and unknown ones are dropped; titles,
urls, authors and timestamps stay deterministic projections of the gh rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from openviking.models.vlm.llm import parse_json_from_response
from openviking.resource.github_radar.models import PrSignal
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_ALLOWED_SEVERITIES = ("high", "medium", "low")
_PAYLOAD_BODY_LIMIT = 1500


class PrTriage(BaseModel):
    """Structured per-PR triage row returned by the first LLM stage."""

    number: int
    topic: str
    severity: str
    summary_zh: str
    impact: str
    recommended_action: str = ""


class TopicSection(BaseModel):
    """One topic group produced by the synthesis stage."""

    topic: str
    overview_zh: str
    highlight_numbers: List[int] = Field(default_factory=list)


class RadarSynthesis(BaseModel):
    editorial_note: str
    sections: List[TopicSection] = Field(default_factory=list)


@dataclass(frozen=True)
class RadarSummary:
    """Validated summary payload; ``synthesis`` is optional so a failed second
    stage still leaves the per-PR triage available to the renderer."""

    triage: Dict[int, PrTriage]
    synthesis: Optional[RadarSynthesis]


class RadarSummarizer:
    """Runs the two-stage LLM summary for one radar pass."""

    def __init__(self, vlm_resolver: Any, *, batch_bytes: int) -> None:
        self._resolver = vlm_resolver
        self._batch_bytes = batch_bytes

    async def summarize(
        self,
        repo: str,
        prs: List[PrSignal],
        account_id: str,
    ) -> Optional[RadarSummary]:
        if not prs:
            return None
        try:
            handle = await self._resolver.get_vlm(account_id)
            triage = await self._run_triage(handle, repo, prs)
            if not triage:
                logger.warning("[PrRadar] summary triage produced no valid rows for %s", repo)
                return None

            synthesis = await self._run_synthesis(handle, repo, prs, triage)
            if synthesis is None:
                # Keep the triage-only result: per-PR Chinese notes still add
                # value even without topic-level editorial grouping.
                return RadarSummary(triage=triage, synthesis=None)
            return RadarSummary(triage=triage, synthesis=synthesis)
        except Exception:  # noqa: BLE001 -- caller falls back to the template
            logger.exception("[PrRadar] summary failed for %s, degrading to template report", repo)
            return None

    # -- stage 1: per-PR triage --------------------------------------------

    async def _run_triage(
        self,
        handle: Any,
        repo: str,
        prs: List[PrSignal],
    ) -> Dict[int, PrTriage]:
        template = self._load_prompt("triage.md")
        rows: Dict[int, PrTriage] = {}
        for batch in self._chunk_by_bytes(prs):
            # The allowlist must match the numbers actually sent in THIS
            # prompt: a response naming a PR from another batch would
            # otherwise overwrite the grounded summary earned earlier.
            batch_allowed = {pr.number for pr in batch}
            payload = [self._pr_payload(pr) for pr in batch]
            prompt = template.format(
                repo=repo,
                pr_count=len(payload),
                payload=json.dumps(payload, ensure_ascii=False, indent=1),
            )
            # A failed batch must not discard rows already earned by earlier
            # batches; skip it and keep triaging the rest.
            try:
                data = await self._call_json(handle, prompt)
            except Exception:  # noqa: BLE001 -- best-effort enrichment
                logger.exception(
                    "[PrRadar] triage batch failed for %s, skipping %s PR(s)",
                    repo,
                    len(batch),
                )
                continue
            if not isinstance(data, dict):
                continue
            raw_items = data.get("items")
            # A scalar (LLM/json_repair artifact) is not iterable; skip the
            # batch instead of letting TypeError escape and voiding the rows
            # earned by earlier batches.
            if not isinstance(raw_items, list):
                continue
            for raw_row in raw_items:
                triage_row = self._parse_triage_row(raw_row, batch_allowed)
                if triage_row is not None:
                    rows[triage_row.number] = triage_row
        return rows

    @staticmethod
    def _parse_triage_row(
        raw: Any, allowed_numbers: set[int]
    ) -> Optional[PrTriage]:
        if not isinstance(raw, dict):
            return None
        number = raw.get("number")
        if type(number) is not int or number not in allowed_numbers:
            # Unknown or fabricated PR number: never let it into the report.
            return None
        severity = str(raw.get("severity") or "low").lower()
        if severity not in _ALLOWED_SEVERITIES:
            severity = "low"
        topic = str(raw.get("topic") or "other").strip().lower() or "other"
        try:
            return PrTriage(
                number=number,
                topic=topic,
                severity=severity,
                summary_zh=str(raw.get("summary_zh") or "").strip(),
                impact=str(raw.get("impact") or "").strip(),
                recommended_action=str(raw.get("recommended_action") or "").strip(),
            )
        except ValidationError:
            logger.warning("[PrRadar] dropping invalid triage row for PR #%s", number)
            return None

    # -- stage 2: topic synthesis -------------------------------------------

    async def _run_synthesis(
        self,
        handle: Any,
        repo: str,
        prs: List[PrSignal],
        triage: Dict[int, PrTriage],
    ) -> Optional[RadarSynthesis]:
        template = self._load_prompt("synthesis.md")
        # Highlights may only name PRs present in the synthesis payload, i.e.
        # the triage rows actually produced in stage 1 — not every collected
        # PR, some of which were never part of this model input.
        allowed = set(triage)
        known_topics = {row.topic for row in triage.values()}
        payload = [
            {
                "number": row.number,
                "topic": row.topic,
                "severity": row.severity,
                "summary_zh": row.summary_zh,
                "impact": row.impact,
            }
            for row in sorted(triage.values(), key=lambda item: item.number)
        ]
        prompt = template.format(
            repo=repo,
            pr_count=len(payload),
            payload=json.dumps(payload, ensure_ascii=False, indent=1),
        )
        # A synthesis transport/parse failure must not void the triage rows
        # earned by stage 1: return None so the caller keeps a triage-only
        # result instead of bubbling up and discarding the whole summary.
        try:
            data = await self._call_json(handle, prompt)
        except Exception:  # noqa: BLE001 -- best-effort enrichment
            logger.exception(
                "[PrRadar] synthesis call failed for %s, keeping a triage-only result",
                repo,
            )
            return None
        if not isinstance(data, dict):
            return None
        try:
            raw_sections = data.get("sections")
            # A scalar sections value must degrade to triage-only, not raise
            # TypeError past the ValidationError guard below and discard the
            # stage-1 triage rows entirely.
            if not isinstance(raw_sections, list):
                return None
            valid_sections = [
                section for section in raw_sections
                if isinstance(section, dict)
                and str(section.get("topic") or "").strip().lower() in known_topics
            ]
            allow_combined = len(triage) <= 3 and len(valid_sections) == 1
            sections: List[TopicSection] = []
            for raw_section in valid_sections:
                topic = str(raw_section.get("topic") or "").strip().lower()
                raw_highlights = raw_section.get("highlight_numbers")
                if not isinstance(raw_highlights, list):
                    raw_highlights = []
                highlights = [
                    number
                    for number in raw_highlights
                    if type(number) is int and number in allowed
                    and (allow_combined or triage[number].topic == topic)
                ]
                sections.append(
                    TopicSection(
                        topic=topic,
                        overview_zh=str(raw_section.get("overview_zh") or "").strip(),
                        highlight_numbers=highlights,
                    )
                )
            if not sections:
                return None
            return RadarSynthesis(
                editorial_note=str(data.get("editorial_note") or "").strip(),
                sections=sections,
            )
        except ValidationError:
            logger.warning("[PrRadar] synthesis schema validation failed for %s", repo)
            return None

    # -- helpers ------------------------------------------------------------

    async def _call_json(self, handle: Any, prompt: str) -> Optional[Any]:
        response = await handle.get_completion_async(prompt=prompt)
        return parse_json_from_response(response)

    def _chunk_by_bytes(self, prs: List[PrSignal]) -> List[List[PrSignal]]:
        """Split by serialized UTF-8 byte size, mirroring loopx's budget rule:
        one long PR body must not blow up a single model call."""
        batches: List[List[PrSignal]] = []
        current: List[PrSignal] = []
        size = 0
        for pr in prs:
            cost = len(
                json.dumps(self._pr_payload(pr), ensure_ascii=False).encode("utf-8")
            )
            if current and size + cost > self._batch_bytes:
                batches.append(current)
                current, size = [], 0
            current.append(pr)
            size += cost
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _pr_payload(pr: PrSignal) -> Dict[str, Any]:
        return {
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "draft": pr.draft,
            "labels": pr.labels,
            "body": (pr.body or "")[:_PAYLOAD_BODY_LIMIT],
        }

    @staticmethod
    def _load_prompt(name: str) -> str:
        return (
            resources.files(__package__)
            .joinpath("prompts", name)
            .read_text(encoding="utf-8")
        )
