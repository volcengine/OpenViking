# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rule-specific Jev System One judgments."""

import time
from dataclasses import dataclass
from typing import Dict, List, TypeVar

import httpx

from openviking.models.jev import (
    MODE_NOUL,
    resolve_jev_target,
    validate_jev_probability,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

JevJudgeClientT = TypeVar("JevJudgeClientT", bound="_JevJudgeClient")


@dataclass(frozen=True)
class RuleJevInput:
    name: str
    content: str
    applies_when: str
    exception: str

    def __post_init__(self) -> None:
        normalized = self.applies_when.strip()
        if normalized.casefold() in {"none", "(none)", "[]", "[ ]"}:
            raise ValueError("Rule Jev input requires applies_when")
        object.__setattr__(self, "applies_when", normalized)


@dataclass(frozen=True)
class RuleJevScore:
    applies_when: float
    exception: float


class _JevJudgeClient:
    """Shared System One transport for Rule-domain judgments."""

    log_name = "JevJudge"

    def __init__(self, config) -> None:
        self.api_url, self.model_name, extra_headers = resolve_jev_target(config)
        self.log_payloads = config.log_payloads
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                **extra_headers,
            },
            timeout=config.timeout,
        )

    @classmethod
    def from_config(
        cls: type[JevJudgeClientT],
        config,
    ) -> JevJudgeClientT | None:
        if not config or not config.is_available():
            return None
        mode = getattr(config, "mode", MODE_NOUL)
        if mode != MODE_NOUL:
            raise ValueError("Rule-domain Jev judgments require mode='noul'")
        return cls(config)

    def _request_answers(self, body: dict, *, item_count: int) -> dict | None:
        try:
            if self.log_payloads:
                logger.debug(
                    "judgment request summary operation=%s total=%d payload=true",
                    self.log_name,
                    item_count,
                    extra={"operation": self.log_name, "payload": True, "total": item_count},
                )
            started = time.monotonic()
            response = self._client.post(self.api_url, json=body)
            duration_seconds = time.monotonic() - started
            response.raise_for_status()
            data = response.json()
            if self.log_payloads:
                logger.debug(
                    "judgment response summary operation=%s http_status_code=%d duration_ms=%.1f payload=true",
                    self.log_name,
                    response.status_code,
                    duration_seconds * 1000,
                    extra={
                        "operation": self.log_name,
                        "payload": True,
                        "http_status_code": response.status_code,
                        "duration_ms": duration_seconds * 1000,
                    },
                )
            answers = data.get("answers")
            if not isinstance(answers, dict):
                logger.info(
                    "judgment answers missing; skipping judgment operation=%s degraded=true",
                    self.log_name,
                    extra={"operation": self.log_name, "degraded": True},
                )
                return None
            return answers
        except httpx.HTTPStatusError as error:
            logger.info(
                "judgment request rejected; skipping judgment operation=%s http_status_code=%d degraded=true",
                self.log_name,
                error.response.status_code,
                extra={
                    "operation": self.log_name,
                    "http_status_code": error.response.status_code,
                    "degraded": True,
                },
            )
            return None
        except Exception as error:
            logger.info(
                "judgment failed; skipping judgment operation=%s error_type=%s degraded=true",
                self.log_name,
                type(error).__name__,
                extra={
                    "operation": self.log_name,
                    "error_type": type(error).__name__,
                    "degraded": True,
                },
            )
            return None

    def close(self) -> None:
        self._client.close()


class RuleJevClient(_JevJudgeClient):
    """Jev client for Rule applicability and exception judgments."""

    log_name = "RuleJev"

    @staticmethod
    def _questions(rules: List[RuleJevInput]) -> Dict[str, dict]:
        questions: Dict[str, dict] = {}
        for index, rule in enumerate(rules):
            if rule.applies_when:
                questions[f"applies_when_{index}"] = {
                    "type": MODE_NOUL,
                    "instructions": {
                        "rule_index": index,
                        "question": (
                            "Does rules[rule_index] apply to task_context? Judge only "
                            "applicability, not whether the Rule is satisfied or violated."
                        ),
                    },
                    "criteria": {
                        "true": rule.applies_when,
                    },
                }
            if rule.exception:
                questions[f"exception_{index}"] = {
                    "type": MODE_NOUL,
                    "instructions": {
                        "rule_index": index,
                        "question": (
                            "Does an exception to rules[rule_index] apply to task_context?"
                        ),
                    },
                    "criteria": {
                        "true": rule.exception,
                    },
                }
        return questions

    @staticmethod
    def _answer(
        answers: dict,
        key: str,
        *,
        expected: bool,
        default: float,
    ) -> float:
        if not expected:
            return default
        if key not in answers:
            raise ValueError(f"Missing Rule Jev answer: {key}")
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("type") != MODE_NOUL:
            raise ValueError(f"Missing or malformed Rule Jev answer: {key}")
        return validate_jev_probability(answer.get("noul"), label="Rule Jev probability")

    def judge_rules(
        self,
        task_context: str,
        rules: List[RuleJevInput],
    ) -> List[RuleJevScore] | None:
        if not rules:
            return []
        questions = self._questions(rules)
        if not questions:
            return [RuleJevScore(applies_when=1.0, exception=0.0) for _ in rules]
        body = {
            "model": self.model_name,
            "state": {
                "task_context": task_context,
                "rules": [{"name": rule.name, "content": rule.content} for rule in rules],
            },
            "questions": questions,
        }
        answers = self._request_answers(body, item_count=len(rules))
        if answers is None:
            return None
        try:
            return [
                RuleJevScore(
                    applies_when=self._answer(
                        answers,
                        f"applies_when_{index}",
                        expected=bool(rule.applies_when),
                        default=1.0,
                    ),
                    exception=self._answer(
                        answers,
                        f"exception_{index}",
                        expected=bool(rule.exception),
                        default=0.0,
                    ),
                )
                for index, rule in enumerate(rules)
            ]
        except Exception as error:
            logger.info(
                "rule answers invalid; skipping judgment error_type=%s degraded=true",
                type(error).__name__,
                extra={"error_type": type(error).__name__, "degraded": True},
            )
            return None


class ScenarioJevClient(_JevJudgeClient):
    """Jev client for deciding whether recalled Scenarios are unrelated."""

    log_name = "ScenarioJev"

    @staticmethod
    def _questions(scenarios: List[str]) -> Dict[str, dict]:
        return {
            f"unrelated_{index}": {
                "type": MODE_NOUL,
                "instructions": {
                    "scenario_index": index,
                    "question": (
                        "Is scenarios[scenario_index] completely unrelated to task_context?"
                    ),
                },
                "criteria": {
                    "true": (
                        "The Scenario has no plausible connection to the current task, "
                        "object, operation, evidence, or situation."
                    ),
                    "false": (
                        "The Scenario is relevant, may apply, or there is any uncertainty "
                        "about whether it could apply."
                    ),
                },
            }
            for index in range(len(scenarios))
        }

    def judge_scenarios(
        self,
        task_context: str,
        scenarios: List[str],
    ) -> List[float] | None:
        if not scenarios:
            return []
        body = {
            "model": self.model_name,
            "state": {
                "task_context": task_context,
                "scenarios": scenarios,
            },
            "questions": self._questions(scenarios),
        }
        answers = self._request_answers(body, item_count=len(scenarios))
        if answers is None:
            return None
        try:
            scores = []
            for index in range(len(scenarios)):
                key = f"unrelated_{index}"
                answer = answers.get(key)
                if not isinstance(answer, dict) or answer.get("type") != MODE_NOUL:
                    raise ValueError(f"Missing or malformed Scenario Jev answer: {key}")
                scores.append(
                    validate_jev_probability(
                        answer.get("noul"),
                        label="Scenario irrelevance probability",
                    )
                )
            return scores
        except Exception as error:
            logger.info(
                "scenario answers invalid; preserving candidates error_type=%s degraded=true",
                type(error).__name__,
                extra={"error_type": type(error).__name__, "degraded": True},
            )
            return None


__all__ = [
    "RuleJevClient",
    "RuleJevInput",
    "RuleJevScore",
    "ScenarioJevClient",
]
