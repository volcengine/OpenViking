# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rule-specific Jev System One judgments."""

import json
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
                logger.warning(
                    "[%s] Request items=%s payload=%s",
                    self.log_name,
                    item_count,
                    json.dumps(body, ensure_ascii=False),
                )
            started = time.monotonic()
            response = self._client.post(self.api_url, json=body)
            duration_seconds = time.monotonic() - started
            response.raise_for_status()
            data = response.json()
            if self.log_payloads:
                logger.warning(
                    "[%s] Response status=%s duration_ms=%.1f payload=%s",
                    self.log_name,
                    response.status_code,
                    duration_seconds * 1000,
                    json.dumps(data, ensure_ascii=False),
                )
            answers = data.get("answers")
            if not isinstance(answers, dict):
                logger.warning("[%s] Response has no answers object", self.log_name)
                return None
            return answers
        except httpx.HTTPStatusError as error:
            logger.error(
                "[%s] API error: %s %s",
                self.log_name,
                error.response.status_code,
                error.response.text,
            )
            return None
        except Exception as error:
            logger.error("[%s] Judgment failed: %s", self.log_name, error)
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
            logger.error("[RuleJev] Invalid answers: %s", error)
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
            logger.error("[ScenarioJev] Invalid answers: %s", error)
            return None


__all__ = [
    "RuleJevClient",
    "RuleJevInput",
    "RuleJevScore",
    "ScenarioJevClient",
]
