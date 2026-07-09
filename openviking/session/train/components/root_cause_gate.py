# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LLM RootCauseGate for trajectory extraction drafts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from openviking.prompts.manager import PromptManager
from openviking.session.memory.dataclass import ResolvedOperations
from openviking.session.memory.extract_loop import PostValidationRetryDecision
from openviking.session.memory.utils import parse_json_with_stability
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking.telemetry import bind_telemetry_stage, tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RootCauseGate:
    """Review trajectory extraction drafts and request deeper full-schema retries.

    The gate owns all trajectory/root-cause semantics.  ExtractLoop only sees the
    returned generic ``PostValidationRetryDecision`` and therefore does not need
    to understand gates, trajectories, or root-cause quality.
    """

    vlm: Any
    thinking: bool = True
    max_followups: int = 2
    rounds: list[dict[str, Any]] = field(default_factory=list)
    _followups_sent: int = 0
    _final_recorded: bool = False

    async def __call__(
        self,
        operations: ResolvedOperations,
        retry_count: int,
        *,
        messages: list[dict[str, Any]] | None = None,
        latest_draft: Any = None,
    ) -> PostValidationRetryDecision | None:
        draft = latest_draft if latest_draft is not None else operations
        if not _operations_include_trajectories(draft):
            return None

        verdict = await self._judge(messages or [], draft, round_idx=len(self.rounds))
        self.rounds.append({"round": len(self.rounds), "verdict": verdict.trace_payload()})

        if verdict.pass_:
            self._record_trace(final_status="passed")
            return None

        if self._followups_sent < self.max_followups:
            self._followups_sent += 1
            return PostValidationRetryDecision(
                retry=True,
                instruction=_followup_message(verdict),
                include_latest_draft=True,
            )

        self._record_trace(final_status="discarded")
        tracer.info(
            "RootCauseGate discarded trajectory extraction draft after "
            f"{self.max_followups} follow-up rounds",
            console=True,
        )
        return PostValidationRetryDecision(discard=True)

    async def _judge(
        self,
        messages: list[dict[str, Any]],
        latest_draft: Any,
        *,
        round_idx: int,
    ) -> "_RootCauseGateVerdict":
        gate_messages = _build_gate_messages(messages, latest_draft)
        try:
            with bind_telemetry_stage("memory_extract_root_cause_gate"):
                response = await self.vlm.get_completion_async(
                    messages=gate_messages,
                    tools=None,
                    tool_choice=None,
                    thinking=self.thinking,
                )
        except Exception as exc:
            logger.warning("RootCauseGate model call failed: %s", exc, exc_info=True)
            return _RootCauseGateVerdict(
                pass_=False,
                need_followup=True,
                root_cause_quality="gate_call_failed",
                reason=f"gate model call failed: {exc}",
                followup_message=_default_followup_message(),
            )

        content = response if isinstance(response, str) else getattr(response, "content", "") or ""
        tracer.info(f"root_cause_gate_response_round_{round_idx}={content}")
        verdict, error = parse_json_with_stability(
            content=content,
            model_class=_RootCauseGateVerdict,
            expected_fields=[
                "pass",
                "need_followup",
                "root_cause_quality",
                "reason",
                "followup_message",
            ],
        )
        if error is not None or verdict is None:
            reason = f"gate verdict could not be parsed: {error}"
            tracer.error(reason)
            return _RootCauseGateVerdict(
                pass_=False,
                need_followup=True,
                root_cause_quality="gate_parse_error",
                reason=reason,
                followup_message=(
                    "The prior trajectory draft could not be accepted because the root-cause "
                    "quality was not verified. Rewrite the complete JSON object matching the "
                    "original schema. Make the ideal experience source-bound, runtime-injectable, "
                    "narrow, and tied to the earliest material boundary. Output ONLY complete JSON."
                ),
            )
        return verdict

    def _record_trace(self, *, final_status: str) -> None:
        if self._final_recorded:
            return
        self._final_recorded = True
        payload = {
            "root_cause_gate": {
                "max_rounds": self.max_followups,
                "final_status": final_status,
                "rounds": self.rounds,
            }
        }
        tracer.info(json.dumps(payload, ensure_ascii=False))


class _RootCauseGateVerdict(BaseModel):
    """LLM gate verdict for trajectory root-cause extraction quality."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    pass_: bool = Field(False, alias="pass")
    need_followup: bool = True
    root_cause_quality: str = "unclear"
    reason: str = ""
    followup_message: str = ""

    def trace_payload(self) -> dict[str, Any]:
        return {
            "pass": self.pass_,
            "need_followup": self.need_followup,
            "root_cause_quality": self.root_cause_quality,
            "reason": self.reason,
            "followup_message": self.followup_message,
        }


def _operations_include_trajectories(operations: Any) -> bool:
    trajectories = getattr(operations, "trajectories", None)
    if trajectories is None and isinstance(operations, dict):
        trajectories = operations.get("trajectories")
    return bool(trajectories)


def _followup_message(verdict: _RootCauseGateVerdict) -> str:
    followup = (verdict.followup_message or "").strip()
    return followup or _default_followup_message()


def _default_followup_message() -> str:
    return (
        "Your previous trajectory extraction did not identify a sufficient reusable root cause. "
        "Rewrite the complete JSON object matching the original schema. Focus on the earliest "
        "material boundary, visible runtime source binding, a directly injectable ideal "
        "experience, preserve rules, and over-broad/wrong-scope rejects. Output ONLY the "
        "complete JSON object."
    )


def _build_gate_messages(
    extract_messages: list[dict[str, Any]],
    latest_draft: Any,
) -> list[dict[str, str]]:
    template = _load_template()
    variables = {
        "extract_messages_json": _messages_to_json(extract_messages),
        "latest_draft_json": _serialize_draft(latest_draft),
    }
    return [
        {
            "role": "system",
            "content": TemplateUtils.render(template["system_prompt"], variables, strip=True),
        },
        {
            "role": "user",
            "content": TemplateUtils.render(template["user_prompt"], variables, strip=True),
        },
    ]


def _load_template() -> dict[str, str]:
    configured_path = PromptManager._resolve_templates_dir(None) / "memory" / "root_cause_gate.yaml"
    bundled_path = PromptManager._get_bundled_templates_dir() / "memory" / "root_cause_gate.yaml"
    template_path: Path = configured_path if configured_path.exists() else bundled_path
    with open(template_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if data.get("template_type") != "root_cause_gate":
        raise ValueError(f"Invalid root-cause gate template: {template_path}")
    if not data.get("system_prompt") or not data.get("user_prompt"):
        raise ValueError(f"Root-cause gate template missing prompts: {template_path}")
    return {
        "system_prompt": str(data["system_prompt"]),
        "user_prompt": str(data["user_prompt"]),
    }


def _messages_to_json(messages: list[dict[str, Any]]) -> str:
    sanitized = []
    for message in messages:
        item = {
            "role": message.get("role"),
            "content": message.get("content"),
        }
        if "tool_calls" in message:
            item["tool_calls"] = message.get("tool_calls")
        if "name" in message:
            item["name"] = message.get("name")
        if "tool_call_id" in message:
            item["tool_call_id"] = message.get("tool_call_id")
        sanitized.append(item)
    try:
        return JsonUtils.dumps(sanitized, indent=2) or "[]"
    except Exception:
        return json.dumps(sanitized, ensure_ascii=False, indent=2, default=str)


def _serialize_draft(latest_draft: Any) -> str:
    try:
        serialized = JsonUtils.dumps(latest_draft, indent=2)
        if serialized is not None:
            return serialized
    except Exception:
        pass
    return json.dumps(latest_draft, ensure_ascii=False, indent=2, default=str)
