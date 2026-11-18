"""One conservative completion contract for Runtime and server Compile owners.

Coverage describes input assignments and acknowledged publication, never semantic
fact coverage, complete citations, or exhaustive conflict detection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COVERAGE_KIND = "assignment_delivery_v1"
COVERAGE_STATES = frozenset({"delivered", "prepared", "pending", "failed", "unreferenced"})
FAILURE_CODES = frozenset(
    {
        "input_gaps",
        "execution_failures",
        "publication_conflicts",
        "unconfirmed_writes",
        "iteration_limit",
        "incomplete_reply",
        "delivery_unknown",
    }
)


def completion_evidence(value: Any, *, verified_noop: bool = False) -> dict[str, Any]:
    """Validate allowlisted evidence; absence/malformed/legacy data stays unknown.

    An explicit partial result always blocks progress even without the newer
    coverage marker. Diagnostics are codes only; never forward arbitrary strings.
    """
    value = value if isinstance(value, Mapping) else {}
    status = value.get("completion_status")
    declared_partial = status == "partial"
    counts = value.get("source_coverage")
    valid_counts = (
        value.get("coverage_kind") == COVERAGE_KIND
        and isinstance(counts, dict)
        and set(counts) <= COVERAGE_STATES
        and all(type(count) is int and 0 <= count <= 2**63 - 1 for count in counts.values())
    )
    codes = value.get("failure_summary")
    valid_codes = (
        isinstance(codes, list)
        and len(codes) <= 20
        and all(isinstance(code, str) and code in FAILURE_CODES for code in codes)
    )
    safe_codes = (
        list(
            dict.fromkeys(
                code for code in codes[:20] if isinstance(code, str) and code in FAILURE_CODES
            )
        )
        if isinstance(codes, list)
        else []
    )
    coverage = dict(counts) if valid_counts else {}
    gaps = any(count for state, count in coverage.items() if state != "delivered")
    if gaps and "input_gaps" not in safe_codes:
        safe_codes.append("input_gaps")
    if declared_partial or gaps or safe_codes:
        status = "partial"
    elif verified_noop or (
        status == "complete" and valid_counts and valid_codes and coverage.get("delivered", 0) > 0
    ):
        status = "complete"
    else:
        status = "unknown"
    return {
        "completion_status": status,
        "coverage_kind": COVERAGE_KIND if valid_counts or verified_noop else None,
        "source_coverage": coverage,
        "failure_summary": safe_codes[:20],
    }
