"""Exact content-write receipts, collected outside Compile model output.

The server uses its existing finalization owner to resolve these durable Tasks.
Byte acknowledgement, processing settlement and processing success are separate.
"""

from __future__ import annotations

import re
from typing import Any

from openviking.utils.path_safety import validate_safe_viking_uri_path

PROTOCOL = "content_task_receipts_v1"
MAX_RECEIPTS = 2048
KINDS = frozenset({"content_write", "content_batch_write"})


def publication_evidence(value: Any) -> dict[str, Any]:
    """Normalize producer claims; never trust an incoming processing conclusion."""
    result: dict[str, Any] = {
        "protocol": None,
        "mode": "legacy",
        "state": "unknown",
        "receipts": [],
        "incomplete": False,
        "reason_codes": [],
    }
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL:
        return result
    result["protocol"] = PROTOCOL
    mode, rows = value.get("mode"), value.get("receipts")
    if (
        not isinstance(mode, str)
        or mode not in {"async", "synchronous", "no_writes"}
        or not isinstance(rows, list)
    ):
        result.update(incomplete=True, reason_codes=["receipt_invalid"])
        return result
    result["mode"] = mode
    incomplete = value.get("incomplete", False)
    if type(incomplete) is not bool:
        incomplete = True
    result["incomplete"] = incomplete or len(rows) > MAX_RECEIPTS
    seen: dict[str, dict[str, str]] = {}
    receipts: list[dict[str, str]] = []
    for item in rows[:MAX_RECEIPTS]:
        try:
            if not isinstance(item, dict):
                raise ValueError
            task_id, uri, kind = item.get("task_id"), item.get("uri"), item.get("kind")
            if (
                not isinstance(task_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", task_id)
                or kind not in KINDS
            ):
                raise ValueError
            if not isinstance(uri, str) or len(uri) > 8192 or not uri.startswith("viking://"):
                raise ValueError
            uri = validate_safe_viking_uri_path(uri)
            receipt = {"task_id": task_id, "uri": uri, "kind": kind}
            if task_id in seen and seen[task_id] != receipt:
                raise ValueError
            if task_id not in seen:
                receipts.append(receipt)
                seen[task_id] = receipt
        except (ValueError, TypeError):
            result["incomplete"] = True
    result["receipts"] = receipts
    if (mode != "async" and receipts) or (mode == "async" and not receipts):
        result["incomplete"] = True
    if result["incomplete"]:
        result["reason_codes"] = ["receipt_unavailable"]
    elif mode == "synchronous":
        result["state"] = "complete"
    elif mode == "no_writes":
        result["state"] = "not_requested"
    else:
        result["state"] = "pending"
    return result


class PublicationReceipts:
    """A bounded Runtime collector: absent receipts cannot become a complete set."""

    def __init__(self):
        self.receipts: list[dict[str, Any]] = []
        self.incomplete = False
        self.wrote = False

    def add(self, task_id, uri, kind):
        self.wrote = True
        if len(self.receipts) >= MAX_RECEIPTS:
            self.incomplete = True
            return
        self.receipts.append({"task_id": task_id, "uri": uri, "kind": kind})

    def snapshot(self, *, uncertain=False, synchronous=False):
        return publication_evidence(
            {
                "protocol": PROTOCOL,
                "mode": "synchronous"
                if synchronous
                else "async"
                if self.wrote or uncertain
                else "no_writes",
                "receipts": self.receipts,
                "incomplete": self.incomplete or uncertain,
            }
        )


async def resolve_publication(value, *, tracker, account_id, user_id, target, ctx):
    """One existing-finalization step; no sleeps, queue scans or cached authority."""
    from openviking.concurrency import bounded_map
    from openviking.core.namespace import relative_uri_path, resolve_request_uri
    from openviking.service.task_domain import TaskStatus, collect_queue_work_errors
    from openviking.storage.content_write import _result_has_queue_errors

    result = publication_evidence(value)
    if result["protocol"] != PROTOCOL or result["mode"] != "async":
        return result
    expected = resolve_request_uri(target, ctx)

    async def resolve(receipt):
        try:
            uri = resolve_request_uri(receipt["uri"], ctx)
            valid = (
                uri == expected
                if receipt["kind"] == "content_batch_write"
                else bool(relative_uri_path(expected, uri))
            )
        except ValueError:
            valid = False
        if not valid:
            return receipt, "unknown", "dependency_mismatch"
        task = await tracker._store.get_durable_task(
            receipt["task_id"], account_id=account_id, user_id=user_id
        )
        if task is None:
            return receipt, "unknown", "dependency_missing"
        try:
            matches = (
                task.account_id == account_id
                and task.user_id == user_id
                and task.task_type == receipt["kind"]
                and resolve_request_uri(task.resource_id or "", ctx) == uri
            )
        except ValueError:
            matches = False
        if not matches:
            return receipt, "unknown", "dependency_mismatch"
        status = task.status
        if status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.CANCELLING}:
            return receipt, "pending", None
        if status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return (
                receipt,
                "failed",
                "processing_cancelled" if status == TaskStatus.CANCELLED else "processing_failed",
            )
        if status == TaskStatus.COMPLETED:
            if collect_queue_work_errors(task.result) or _result_has_queue_errors(task.result):
                return receipt, "failed", "processing_failed"
            return receipt, "complete", None
        return receipt, "unknown", "dependency_state_unknown"

    resolved = await bounded_map(result["receipts"], resolve, concurrency=8)
    states = {state for _, state, _ in resolved}
    if result["incomplete"]:
        states.add("unknown")
    result["task_states"] = [{**receipt, "state": state} for receipt, state, _ in resolved]
    result["state"] = next(
        (state for state in ("pending", "failed", "unknown") if state in states), "complete"
    )
    result["reason_codes"] = sorted(
        set(result["reason_codes"]) | {reason for _, _, reason in resolved if reason}
    )
    return result
