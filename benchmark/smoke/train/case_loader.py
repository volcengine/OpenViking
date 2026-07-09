#!/usr/bin/env python3
"""Small deterministic CaseLoader for OpenViking train-service smoke tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from openviking.session.train import Case, Rubric, RubricCriterion

SmokeSplit = Literal["train", "test"]


SMOKE_CASES: dict[SmokeSplit, list[dict[str, Any]]] = {
    "train": [
        {
            "task_id": "refund_success",
            "domain": "tickets",
            "user_query": "请为票据 T-100 办理退款，并告知客户退款码。",
            "expected_answer": "已为票据 T-100 退款 25.00 美元，退款码是 RF-100。",
            "actual_answer": "已为票据 T-100 退款 25.00 美元，退款码是 RF-100。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-100"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "T-100", "amount": 25.0},
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-100"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "T-100", "amount": 25.0},
                    "type": "write",
                },
            ],
            "passed": True,
            "feedback": [],
        },
        {
            "task_id": "refund_wrong_amount",
            "domain": "tickets",
            "user_query": "请按可退的全额为票据 T-200 办理退款。",
            "expected_answer": "已为票据 T-200 退款 45.00 美元，退款码是 RF-200。",
            "actual_answer": "已为票据 T-200 退款 40.00 美元，退款码是 RF-200。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-200"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "T-200", "amount": 45.0},
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-200"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "T-200", "amount": 40.0},
                    "type": "write",
                },
            ],
            "passed": False,
            "feedback": [
                "期望对 T-200 调用 issue_refund 时 amount=45.0，但实际 rollout 使用了 amount=40.0。"
            ],
        },
        {
            "task_id": "refund_missing_notice",
            "domain": "tickets",
            "user_query": "请为票据 T-300 办理退款，并在回复里包含退款码。",
            "expected_answer": "已为票据 T-300 退款 30.00 美元，退款码是 RF-300。",
            "actual_answer": "已为票据 T-300 退款 30.00 美元。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-300"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "T-300", "amount": 30.0},
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-300"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "T-300", "amount": 30.0},
                    "type": "write",
                },
            ],
            "passed": False,
            "feedback": ["数据库写入动作正确，但最终回复遗漏了退款码 RF-300。"],
        },
        {
            "task_id": "complex_multi_leg_refund",
            "domain": "tickets",
            "user_query": (
                "客户的联程票 T-900 包含 A 段和 B 段：A 段要保留但需要换成 OPEN-A-NEW，"
                "B 段取消后只能退差额 68.50 美元。请完成处理，并回复新票券和退款码。"
            ),
            "expected_answer": (
                "已将票据 T-900 的 A 段换成 OPEN-A-NEW，B 段已退差额 68.50 美元，"
                "退款码是 RF-900B。"
            ),
            "actual_answer": "已取消票据 T-900 并退款 120.00 美元，退款码是 RF-900。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-900"}, "type": "read"},
                {
                    "name": "exchange_coupon",
                    "arguments": {
                        "ticket_id": "T-900",
                        "segment": "A",
                        "new_coupon": "OPEN-A-NEW",
                    },
                    "type": "write",
                },
                {
                    "name": "issue_partial_refund",
                    "arguments": {"ticket_id": "T-900", "segment": "B", "amount": 68.5},
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "T-900"}, "type": "read"},
                {
                    "name": "cancel_ticket",
                    "arguments": {"ticket_id": "T-900", "amount": 120.0},
                    "type": "write",
                },
            ],
            "passed": False,
            "feedback": [
                "应沉淀经验：复杂联程退款先换券再退差额；先对保留航段调用 exchange_coupon，"
                "再只对退票航段调用 issue_partial_refund，并在回复里同时说明新票券和退款码。"
            ],
            "experience_markers": ["复杂联程退款先换券再退差额"],
        },
    ],
    "test": [
        {
            "task_id": "eval_refund_success",
            "domain": "tickets",
            "user_query": "请为票据 E-100 办理退款，并提供退款码。",
            "expected_answer": "已为票据 E-100 退款 15.00 美元，退款码是 ERF-100。",
            "actual_answer": "已为票据 E-100 退款 15.00 美元，退款码是 ERF-100。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "E-100"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "E-100", "amount": 15.0},
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "E-100"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "E-100", "amount": 15.0},
                    "type": "write",
                },
            ],
            "passed": True,
            "feedback": [],
        },
        {
            "task_id": "eval_missing_notice",
            "domain": "tickets",
            "user_query": "请为票据 E-200 办理退款，并包含退款码。",
            "expected_answer": "已为票据 E-200 退款 20.00 美元，退款码是 ERF-200。",
            "actual_answer": "已为票据 E-200 退款 20.00 美元。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "E-200"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "E-200", "amount": 20.0},
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "E-200"}, "type": "read"},
                {
                    "name": "issue_refund",
                    "arguments": {"ticket_id": "E-200", "amount": 20.0},
                    "type": "write",
                },
            ],
            "passed": False,
            "feedback": ["最终回复遗漏了必需的退款码 ERF-200。"],
        },
        {
            "task_id": "eval_complex_multi_leg_refund",
            "domain": "tickets",
            "user_query": (
                "客户的联程票 E-900 包含去程和返程：去程要保留但换成 OPEN-E-NEW，"
                "返程取消后只退差额 72.25 美元。请处理并告知新票券和退款码。"
            ),
            "expected_answer": (
                "已将票据 E-900 的去程换成 OPEN-E-NEW，返程已退差额 72.25 美元，"
                "退款码是 ERF-900R。"
            ),
            "actual_answer": "已取消票据 E-900 并退款 140.00 美元，退款码是 ERF-900。",
            "expected_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "E-900"}, "type": "read"},
                {
                    "name": "exchange_coupon",
                    "arguments": {
                        "ticket_id": "E-900",
                        "segment": "outbound",
                        "new_coupon": "OPEN-E-NEW",
                    },
                    "type": "write",
                },
                {
                    "name": "issue_partial_refund",
                    "arguments": {
                        "ticket_id": "E-900",
                        "segment": "return",
                        "amount": 72.25,
                    },
                    "type": "write",
                },
            ],
            "actual_actions": [
                {"name": "lookup_ticket", "arguments": {"ticket_id": "E-900"}, "type": "read"},
                {
                    "name": "cancel_ticket",
                    "arguments": {"ticket_id": "E-900", "amount": 140.0},
                    "type": "write",
                },
            ],
            "passed": False,
            "feedback": [
                "缺少复杂联程退款经验提示时，rollout 错误地整票取消；需要先换券再按航段退差额。"
            ],
            "experience_markers": ["复杂联程退款先换券再退差额"],
        },
    ],
}


@dataclass(slots=True)
class SmokeCaseLoader:
    """Load a tiny deterministic benchmark split as training Cases."""

    domain: str = "all"
    split: str = "train"
    batch_size: int | None = None
    task_indices: list[int] | None = None

    async def batches(self, context: Any = None) -> AsyncIterator[list[Case]]:
        del context
        cases = self.load_cases()
        size = self.batch_size or 1
        if size <= 0:
            raise ValueError("batch_size must be > 0")
        for start in range(0, len(cases), size):
            yield cases[start : start + size]

    def load_cases(self) -> list[Case]:
        entries = self.load_entries()
        return [self._case_from_entry(task_no, entry) for task_no, entry in entries]

    def load_entries(self) -> list[tuple[int, dict[str, Any]]]:
        entries = _domain_entries(normalize_smoke_split(self.split), self.domain)
        if self.task_indices is None:
            return list(enumerate(entries))
        selected: list[tuple[int, dict[str, Any]]] = []
        for index in self.task_indices:
            if index < 0:
                raise ValueError("task_indices must be >= 0")
            try:
                selected.append((index, entries[index]))
            except IndexError as exc:
                raise ValueError(
                    f"task index out of range for split {self.split!r}: {index} "
                    f"(size={len(entries)})"
                ) from exc
        return selected

    def split_exists(self) -> bool:
        return bool(_domain_entries(normalize_smoke_split(self.split), self.domain))

    def _case_from_entry(self, task_no: int, entry: dict[str, Any]) -> Case:
        split = normalize_smoke_split(self.split)
        domain = normalize_smoke_domain(self.domain, strict=False)
        task_id = str(entry["task_id"])
        data_split = f"{domain}_{split}"
        return Case(
            name=f"smoke_{data_split}_{task_no}_{task_id}",
            task_signature=f"smoke:{domain}:{split}:{task_id}",
            input={
                "dataset": "smoke",
                "domain": domain,
                "split": split,
                "data_split": data_split,
                "task_no": task_no,
                "task_id": task_id,
                "user_query": entry["user_query"],
                "expected_answer": entry["expected_answer"],
                "expected_actions": entry["expected_actions"],
                "smoke_case": entry,
            },
            rubric=Rubric(
                name=f"smoke_{data_split}_{task_no}_rubric",
                description="Smoke rollout 必须满足动作检查和沟通检查。",
                criteria=[
                    RubricCriterion(
                        name="smoke_success",
                        description="脚本化 smoke rollout 达到期望结果。",
                        required=True,
                        weight=1.0,
                    )
                ],
            ),
            metadata={"source": "smoke", "domain": domain, "split": split},
        )


def normalize_smoke_split(value: str | None) -> SmokeSplit:
    split = str(value or "train").strip().lower()
    if split in {"dev", "eval", "validation"}:
        split = "test"
    if split not in {"train", "test"}:
        raise ValueError("Smoke split must be train or test")
    return split  # type: ignore[return-value]


def normalize_smoke_domain(value: str | None, *, strict: bool = True) -> str:
    domain = str(value or "all").strip().lower()
    if domain in {"", "smoke"}:
        domain = "all"
    domains = {str(case["domain"]) for split_cases in SMOKE_CASES.values() for case in split_cases}
    if strict and domain != "all" and domain not in domains:
        raise ValueError(f"Unsupported smoke domain: {value!r}")
    return domain


def _domain_entries(split: SmokeSplit, domain: str | None) -> list[dict[str, Any]]:
    normalized_domain = normalize_smoke_domain(domain)
    entries = [dict(item) for item in SMOKE_CASES[split]]
    if normalized_domain == "all":
        return entries
    return [entry for entry in entries if entry.get("domain") == normalized_domain]
