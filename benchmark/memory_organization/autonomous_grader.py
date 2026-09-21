from __future__ import annotations

import re
from collections import defaultdict

from benchmark.memory_organization.autonomous_cases import AutonomousCase
from benchmark.memory_organization.grader import Grade, _best_partition_intersection
from benchmark.memory_organization.models import fact_lines

_AS_OF_RE = re.compile(r"\s*\(as of \d{4}-\d{2}-\d{2}\)\s*$", re.IGNORECASE)
_MATCHED_SIZE_NOTE_RE = re.compile(
    r"\s*(?:"
    r"（长期稳定）|"
    r"（长期稳定且持续指导未来工作协作）|"
    r"\(a stable long-term preference that guides future work and collaboration\)|"
    r"\(a stable long-term preference that consistently guides future work, collaboration, "
    r"technical decisions, and repeated execution across projects\)"
    r")\s*$",
    re.IGNORECASE,
)


def _normalized_fact_text(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*]\s*)?(?:\[F\d{2}\]\s*)?", "", text)
    text = _AS_OF_RE.sub("", text)
    text = _MATCHED_SIZE_NOTE_RE.sub("", text)
    return " ".join(text.strip().rstrip(".。").lower().split())


def _locate_expected_facts(
    case: AutonomousCase, actual_files: dict[str, tuple[str, str]]
) -> dict[str, list[tuple[str, str]]]:
    expected = {marker: _normalized_fact_text(text) for marker, text in case.expected_facts.items()}
    occurrences: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for uri, (memory_type, content) in actual_files.items():
        normalized_lines = {_normalized_fact_text(line) for line in content.splitlines()}
        explicit_facts = {
            marker: _normalized_fact_text(text) for marker, text in fact_lines(content)
        }
        for marker, normalized_fact in expected.items():
            if explicit_facts.get(marker) == normalized_fact or normalized_fact in normalized_lines:
                occurrences[marker].append((memory_type, uri))
    return occurrences


def grade_autonomous_result(
    case: AutonomousCase,
    actual_files: dict[str, tuple[str, str]],
    actual_replacements: dict[str, str],
) -> Grade:
    expected_facts = case.expected_facts
    occurrences: dict[str, list[str]] = defaultdict(list)
    actual_groups: dict[str, list[set[str]]] = defaultdict(list)
    altered = 0
    unexpected = 0

    for uri, (memory_type, content) in actual_files.items():
        markers: set[str] = set()
        for marker, text in fact_lines(content):
            markers.add(marker)
            occurrences[marker].append(uri)
            if marker not in expected_facts:
                unexpected += 1
            elif expected_facts[marker] != text:
                altered += 1
        actual_groups[memory_type].append(markers)

    expected_markers = set(expected_facts)
    present = {
        marker
        for marker, locations in occurrences.items()
        if locations
        and marker in expected_facts
        and any(
            expected_facts[marker] == text
            for _uri, (_memory_type, content) in actual_files.items()
            for found_marker, text in fact_lines(content)
            if found_marker == marker
        )
    }
    duplicate_count = sum(max(0, len(locations) - 1) for locations in occurrences.values())

    matched = 0
    total_assignments = 0
    exact_group_matches = 0
    expected_file_count = 0
    actual_file_count = len(actual_files)
    partitions_match = set(actual_groups) == set(case.expected_groups)
    for memory_type in set(actual_groups) | set(case.expected_groups):
        expected = [set(group) for group in case.expected_groups.get(memory_type, ())]
        actual = actual_groups.get(memory_type, [])
        matched += _best_partition_intersection(expected, actual)
        total_assignments += sum(len(group) for group in actual)
        expected_file_count += len(expected)
        expected_partition = sorted(tuple(sorted(group)) for group in expected)
        actual_partition = sorted(tuple(sorted(group)) for group in actual)
        exact_group_matches += sum(group in actual_partition for group in expected_partition)
        partitions_match = partitions_match and actual_partition == expected_partition

    placement_precision = matched / total_assignments if total_assignments else 0.0
    file_tree_accuracy = exact_group_matches / max(expected_file_count, actual_file_count, 1)
    missing = len(expected_markers - present)
    misplaced = max(0, total_assignments - matched)
    unexpected_files = max(0, actual_file_count - expected_file_count)

    replacement_checks = [
        actual_replacements.get(source) == target
        for source, target in case.expected_replacements.items()
    ]
    replacement_checks.extend(
        source in case.expected_replacements
        for source in actual_replacements
        if source not in case.expected_replacements
    )
    replacement_accuracy = (
        sum(replacement_checks) / len(replacement_checks) if replacement_checks else 1.0
    )

    content_success = (
        partitions_match
        and actual_file_count == expected_file_count
        and missing == 0
        and misplaced == 0
        and duplicate_count == 0
        and altered == 0
        and unexpected == 0
    )
    return Grade(
        content_organization_success=content_success,
        organization_success=content_success and replacement_accuracy == 1.0,
        file_tree_accuracy=file_tree_accuracy,
        fact_recall=len(present) / len(expected_markers) if expected_markers else 1.0,
        placement_precision=placement_precision,
        replacement_accuracy=replacement_accuracy,
        duplicate_fact_count=duplicate_count,
        altered_fact_count=altered,
        unexpected_fact_count=unexpected,
        misplaced_fact_count=misplaced,
        missing_fact_count=missing,
        unexpected_file_count=unexpected_files,
    )


def autonomous_metrics(
    case: AutonomousCase,
    actual_files: dict[str, tuple[str, str]],
    actual_replacements: dict[str, str],
    grade: Grade,
) -> dict[str, float | bool]:
    expected_type = {
        marker: memory_type
        for memory_type, groups in case.expected_groups.items()
        for group in groups
        for marker in group
    }
    occurrences = _locate_expected_facts(case, actual_files)
    correctly_routed = sum(
        len(occurrences.get(marker, [])) == 1 and occurrences[marker][0][0] == memory_type
        for marker, memory_type in expected_type.items()
    )
    routing_accuracy = correctly_routed / len(expected_type) if expected_type else 1.0
    information_integrity = all(len(occurrences.get(marker, [])) == 1 for marker in expected_type)

    if case.category == "keep_separate":
        path_normalization_success = True
        expected_partitions = [
            group for groups in case.expected_groups.values() for group in groups
        ]
        partition_uris: list[str] = []
        for group in expected_partitions:
            locations = {
                uri for marker in group for _memory_type, uri in occurrences.get(marker, ())
            }
            if len(locations) == 1:
                partition_uris.append(next(iter(locations)))
        expected_file_count = len(expected_partitions)
        organization_action_success = (
            routing_accuracy == 1.0
            and len(partition_uris) == expected_file_count
            and len(set(partition_uris)) == expected_file_count
            and len(actual_files) == expected_file_count
            and not actual_replacements
        )
    elif case.category == "directory_merge":
        path_normalization_success = bool(actual_files) and all(
            uri == uri.lower() for uri in actual_files
        )
        organization_action_success = (
            len(actual_files) == 1
            and path_normalization_success
            and grade.replacement_accuracy == 1.0
        )
    elif case.category == "same_type_split":
        path_normalization_success = True
        source_uris = set(case.initial_files)
        preference_files = [item for item in actual_files.values() if item[0] == "preferences"]
        organization_action_success = (
            len(preference_files) >= 2
            and len(preference_files) == len(actual_files)
            and source_uris.isdisjoint(actual_files)
        )
    else:
        path_normalization_success = True
        profile_uris = {uri for uri, item in actual_files.items() if item[0] == "profile"}
        preference_uris = {uri for uri, item in actual_files.items() if item[0] == "preferences"}
        allowed_types = {"profile", "preferences"}
        organization_action_success = (
            all(item[0] in allowed_types for item in actual_files.values())
            and len(profile_uris) == 1
            and len(preference_uris) >= 1
        )

    return {
        "organization_action_success": organization_action_success,
        "information_integrity": information_integrity,
    }
