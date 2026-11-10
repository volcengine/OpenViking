from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import permutations
from statistics import mean
from typing import Any

from benchmark.memory_organization.models import OrganizationCase, fact_lines, fact_markers


@dataclass(frozen=True, slots=True)
class Grade:
    content_organization_success: bool
    organization_success: bool
    file_tree_accuracy: float
    fact_recall: float
    placement_precision: float
    replacement_accuracy: float
    duplicate_fact_count: int
    altered_fact_count: int
    unexpected_fact_count: int
    misplaced_fact_count: int
    missing_fact_count: int
    unexpected_file_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade_result(
    case: OrganizationCase,
    actual_files: dict[str, str],
    actual_replacements: dict[str, str] | None = None,
) -> Grade:
    occurrences: dict[str, list[str]] = {}
    actual_groups: dict[str, set[str]] = {}
    for topic, content in actual_files.items():
        markers = fact_markers(content)
        actual_groups[topic] = set(markers)
        for marker in markers:
            occurrences.setdefault(marker, []).append(topic)

    expected_facts = {
        marker: text
        for content in case.initial_files.values()
        for marker, text in fact_lines(content)
    }
    actual_fact_lines = [
        (topic, marker, text)
        for topic, content in actual_files.items()
        for marker, text in fact_lines(content)
    ]
    valid_fact_markers = {
        marker for _topic, marker, text in actual_fact_lines if expected_facts.get(marker) == text
    }
    altered_facts = sum(
        marker in expected_facts and expected_facts[marker] != text
        for _topic, marker, text in actual_fact_lines
    )
    unexpected_facts = sum(
        marker not in expected_facts for _topic, marker, _text in actual_fact_lines
    )

    expected_markers = case.expected_markers
    expected_groups = {topic: set(markers) for topic, markers in case.expected_files.items()}
    present = expected_markers & valid_fact_markers
    fact_recall = len(present) / len(expected_markers) if expected_markers else 1.0

    # File names are model-chosen and may have valid aliases (food vs
    # food_preferences). Score the partition of facts, not literal topic names.
    expected_sets = list(expected_groups.values())
    actual_items = [(topic, markers) for topic, markers in actual_groups.items() if markers]
    actual_sets = [markers for _topic, markers in actual_items]
    matched_intersections = _best_partition_intersection(expected_sets, actual_sets)
    # Count unique fact-to-file assignments for placement. Repeated occurrences
    # within one file are reported separately as duplicates, not double-counted
    # as placement errors.
    total_assignments = sum(len(markers) for markers in actual_sets)
    placement_precision = matched_intersections / total_assignments if total_assignments else 0.0
    misplaced = max(0, total_assignments - matched_intersections)
    duplicates = sum(max(0, len(topics) - 1) for topics in occurrences.values())

    expected_partition = sorted(tuple(sorted(group)) for group in expected_sets)
    actual_partition = sorted(tuple(sorted(group)) for group in actual_sets)
    exact_group_matches = sum(group in actual_partition for group in expected_partition)
    file_tree_accuracy = exact_group_matches / max(
        len(expected_partition), len(actual_partition), 1
    )

    expected_replacements = case.expected_replacements
    actual_replacements = actual_replacements or {}
    replacement_checks: list[bool] = []
    for source, expected_target in expected_replacements.items():
        target_group = expected_groups[expected_target]
        actual_targets = {
            topic for topic, markers in actual_groups.items() if markers == target_group
        }
        if source in actual_targets:
            replacement_checks.append(source not in actual_replacements)
        else:
            replacement_checks.append(actual_replacements.get(source) in actual_targets)
    replacement_checks.extend(
        source in expected_replacements
        for source in actual_replacements
        if source not in expected_replacements
    )
    replacement_accuracy = (
        sum(replacement_checks) / len(replacement_checks) if replacement_checks else 1.0
    )

    missing = len(expected_markers - present)
    unexpected_files = max(0, len(actual_files) - len(expected_sets))
    content_organization_success = (
        actual_partition == expected_partition
        and len(actual_files) == len(expected_sets)
        and missing == 0
        and misplaced == 0
        and duplicates == 0
        and altered_facts == 0
        and unexpected_facts == 0
    )
    organization_success = content_organization_success and replacement_accuracy == 1.0
    return Grade(
        content_organization_success=content_organization_success,
        organization_success=organization_success,
        file_tree_accuracy=file_tree_accuracy,
        fact_recall=fact_recall,
        placement_precision=placement_precision,
        replacement_accuracy=replacement_accuracy,
        duplicate_fact_count=duplicates,
        altered_fact_count=altered_facts,
        unexpected_fact_count=unexpected_facts,
        misplaced_fact_count=misplaced,
        missing_fact_count=missing,
        unexpected_file_count=unexpected_files,
    )


def _best_partition_intersection(expected: list[set[str]], actual: list[set[str]]) -> int:
    """Maximum marker overlap under a one-to-one expected/actual file assignment."""
    if not expected or not actual:
        return 0
    if len(expected) <= len(actual):
        return max(
            sum(len(expected[index] & actual[actual_index]) for index, actual_index in enumerate(p))
            for p in permutations(range(len(actual)), len(expected))
        )
    return max(
        sum(len(expected[expected_index] & actual[index]) for index, expected_index in enumerate(p))
        for p in permutations(range(len(expected)), len(actual))
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_protocol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_protocol.setdefault(str(row["protocol"]), []).append(row)
    summary: dict[str, Any] = {}
    for protocol, protocol_rows in by_protocol.items():
        graded = [row for row in protocol_rows if "grade" in row]
        summary[protocol] = {
            "runs": len(protocol_rows),
            "content_organization_success_rate": mean(
                float(row["grade"]["content_organization_success"]) for row in graded
            )
            if graded
            else 0.0,
            "organization_success_rate": mean(
                float(row["grade"]["organization_success"]) for row in graded
            )
            if graded
            else 0.0,
            "file_tree_accuracy": mean(row["grade"]["file_tree_accuracy"] for row in graded)
            if graded
            else 0.0,
            "fact_recall": mean(row["grade"]["fact_recall"] for row in graded) if graded else 0.0,
            "placement_precision": mean(row["grade"]["placement_precision"] for row in graded)
            if graded
            else 0.0,
            "mean_calls": mean(row.get("calls", 0) for row in protocol_rows),
            "mean_retries": mean(row.get("retries", 0) for row in protocol_rows),
            "mean_prompt_tokens": mean(row.get("prompt_tokens", 0) for row in protocol_rows),
            "mean_completion_tokens": mean(
                row.get("completion_tokens", 0) for row in protocol_rows
            ),
            "mean_total_tokens": mean(row.get("total_tokens", 0) for row in protocol_rows),
            "mean_duration_seconds": mean(
                row.get("duration_seconds", 0.0) for row in protocol_rows
            ),
            "terminal_error_rate": mean(bool(row.get("terminal_error")) for row in protocol_rows),
        }
    return summary


def paired_counts(
    rows: list[dict[str, Any]], *, metric: str = "organization_success"
) -> dict[str, int]:
    grouped: dict[tuple[str, int], dict[str, bool]] = {}
    for row in rows:
        key = (str(row["case_id"]), int(row["repeat_index"]))
        grouped.setdefault(key, {})[str(row["protocol"])] = bool(row.get("grade", {}).get(metric))
    counts = {"both_success": 0, "python_only": 0, "json_only": 0, "both_fail": 0}
    for pair in grouped.values():
        if set(pair) != {"json", "python"}:
            continue
        py, js = pair["python"], pair["json"]
        if py and js:
            counts["both_success"] += 1
        elif py:
            counts["python_only"] += 1
        elif js:
            counts["json_only"] += 1
        else:
            counts["both_fail"] += 1
    return counts


def mcnemar_exact_p_value(counts: dict[str, int]) -> float:
    """Two-sided exact McNemar p-value for discordant paired outcomes."""
    from math import comb

    left = counts["python_only"]
    right = counts["json_only"]
    discordant = left + right
    if discordant == 0:
        return 1.0
    tail = sum(comb(discordant, index) for index in range(min(left, right) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))
