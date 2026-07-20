# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Candidate-local retry support for rejected gate results."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ._shared import (
    _preview_text,
)
from .models import GateReport, _decision_target_name


def default_experience_gate_contract() -> str:
    """Prompt-facing contract enforced by the default experience gates."""

    return """## Gate Contract (enforced)
Your experience output will be rejected unless every experience satisfies these gates:

1. Causal eligibility
- Non-success trajectories are eligible for experience learning by default,
  including creating new experiences.
- Treat Experience Repair Signal as advisory context, not as an authorization gate:
  legacy Action=skip, Recommended operation=skip, Existing target experience=none,
  or Trigger boundary=none must not suppress a reusable repair for a failed or
  partially failed trajectory.
- Existing target experience=none only means no existing loaded memory should be modified;
  it must not suppress creating a brand-new experience when New experience action=create
  or when the first reward-changing mistake is reusable and preventable.
- Do not output experiences for Outcome=success.

2. Skill-loader readability
- Experience content must include exactly the runtime-facing sections used by the
  skill experience loader: `## Situation`, `## Reminder`, `## Procedure`, and
  `## Anti-pattern`.
- `## Situation` must include non-empty `Applies when`, `Does not apply when`,
  `Evidence binding`, and `Decision boundary` fields. These let a future agent
  decide whether and when to apply the experience using runtime-visible facts.
- `Does not apply when` must describe a task-pattern mismatch, not a temporal
  stage such as "still reading/writing", "before final_response", or "before
  writes complete"; the skill loader may read the experience before the later
   boundary where it becomes actionable.

3. Specific behavior delta
- Reject generic reminders whose entire behavior change is only to check all
  requirements, ensure compliance, or review carefully. The experience must
  name a discriminating runtime condition and a concrete corrective action.

4. Explicit language binding
- Never infer an output language solely from an audience's geography. Follow an
  explicit language instruction; otherwise preserve the user's language choice
  or ask when the choice materially affects the deliverable.

5. Evidence-safe missing data
- Do not invent required values, use guessed placeholders, or make assumptions
  merely to fill a required schema. Only use a placeholder or assumption when
  the user explicitly permits it; otherwise ask, preserve a clearly marked
  unavailable value, or disclose the limitation.

6. Portable runtime wording
- Replace source-case literals such as example numbers, date/month ranges,
  spreadsheet tab names, and helper script filenames with semantic runtime
  bindings. Keep an exact literal only when an authoritative runtime source
  requires that same invariant across future cases.

7. Final semantic quality
- A final experience is semantically rechecked when merge planning combines
  multiple sources, changes an existing experience, or materially rewrites an
  extracted candidate. It must not turn genre conventions into hidden
  requirements, hardcode factual outputs, combine unrelated repairs, or
  prescribe behavior unsupported by runtime evidence.

If you cannot satisfy this contract, output no experience changes."""


def build_gate_retry_instruction(
    report: GateReport,
    *,
    prior_reports: list[GateReport] | None = None,
) -> str:
    repair = report.retry_repair_prompt()
    if not repair:
        return ""
    targets = report.retriable_rejected_targets()
    lines = [
        "Your previous experience output was rejected by training gates.",
        "Retry only the rejected candidates listed below. Already accepted candidates are retained "
        "outside this retry and must not be repeated or rewritten.",
        "Repair each candidate independently; do not merge candidates or add unrelated experiences.",
        "Return complete operations containing only the repaired rejected candidates. If one cannot "
        "satisfy all gate requirements, omit that candidate.",
    ]
    if targets:
        lines.extend(["", f"Retry targets: {', '.join(targets)}"])
    history = _gate_retry_history(prior_reports or [], targets=set(targets))
    if history:
        lines.extend(
            [
                "",
                "Earlier failed attempts for these candidates (avoid repeating the same defect):",
                history,
            ]
        )
    lines.extend(["", "Current gate repair instructions:", repair])
    return "\n".join(lines)


def _gate_retry_history(prior_reports: list[GateReport], *, targets: set[str]) -> str:
    """Return compact candidate-local feedback from earlier failed attempts."""

    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for attempt_index, prior in enumerate(prior_reports[-2:], start=max(1, len(prior_reports) - 1)):
        for decision in prior.decisions:
            target = _decision_target_name(decision)
            if decision.action != "reject" or (targets and target not in targets):
                continue
            key = (target, decision.gate_name, decision.reason)
            if key in seen:
                continue
            seen.add(key)
            reason = _preview_text(decision.reason, limit=300)
            repair = _preview_text(decision.repair_prompt, limit=300)
            line = f"- attempt={attempt_index} target={target} [{decision.gate_name}]: {reason}"
            if repair:
                line += f" Required repair: {repair}"
            lines.append(line)
            if len(lines) >= 12:
                return "\n".join(lines)
    return "\n".join(lines)


def candidate_retry_draft(draft: Any, *, target_names: set[str]) -> Any:
    """Keep only rejected candidates in the draft shown during a repair retry.

    ExtractLoop drafts are dynamically generated Pydantic models, while tests and
    some callers use resolved operations. This helper handles both shapes and
    fails open to the original draft when candidate names cannot be located.
    """

    if draft is None or not target_names:
        return draft
    result = deepcopy(draft)
    matched = False
    found_candidate_collection = False
    for field_name in ("experiences", "write_uris", "edit_uris", "upsert_operations"):
        values = getattr(result, field_name, None)
        if not isinstance(values, list):
            continue
        found_candidate_collection = True
        selected = [
            value for value in values if _draft_candidate_names(value).intersection(target_names)
        ]
        if selected:
            matched = True
        setattr(result, field_name, selected)
    if not found_candidate_collection or not matched:
        return draft
    for field_name in ("delete_ids", "delete_file_contents"):
        if isinstance(getattr(result, field_name, None), list):
            setattr(result, field_name, [])
    return result


def _draft_candidate_names(value: Any) -> set[str]:
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        try:
            value = dumper(mode="python")
        except TypeError:
            value = dumper()
    elif hasattr(value, "__dict__"):
        value = vars(value)
    names: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "experience_name" and nested:
                names.add(str(nested))
            elif key == "uris" and isinstance(nested, list):
                for uri in nested:
                    text = str(uri or "")
                    if text:
                        names.add(text.rstrip("/").split("/")[-1].removesuffix(".md"))
            elif isinstance(nested, (dict, list, tuple)):
                names.update(_draft_candidate_names(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            names.update(_draft_candidate_names(nested))
    return names
