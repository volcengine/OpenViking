# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Benchmark glob patterns and expected-count helpers.

The default set keeps every manifest-computed result at or below 1,000 files
at every benchmark scale. This makes fs/auto latency comparisons measure glob
execution rather than response transfer or backend result ceilings. It also
keeps effectiveness checks exact: neither engine is treated as ground truth.

The opt-in stress set retains broad patterns for explicitly testing large
result sets and remote limits. It is not part of the default correctness or
latency runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from . import glob_match


@dataclass(frozen=True)
class GlobPattern:
    label: str
    pattern: str
    tier: str = "structure"
    target: str = ""
    note: str = ""


# Seed-42 manifest truth stays at or below 1,000 matches for every scale.
PATTERNS: List[GlobPattern] = [
    GlobPattern(
        "abs_0_missing",
        "**/definitely-missing/**/*.py",
        tier="absolute",
        target="0",
        note="negative case with no expected matches",
    ),
    GlobPattern(
        "abs_10_migrations",
        "**/migrations/*.sql",
        tier="absolute",
        target="0-10",
        note="fixed migration landmark in the 100w root",
    ),
    GlobPattern(
        "abs_100_gateway",
        "**/legacy-gateway/**/*.go",
        tier="absolute",
        target="0-100",
        note="fixed gateway landmark in the 100w root",
    ),
    GlobPattern(
        "index_1_any",
        "**/file_0000000.*",
        tier="window",
        target="0-7",
        note="one filename index per non-landmark bucket",
    ),
    GlobPattern(
        "index_10_any",
        "**/file_000000?.*",
        tier="window",
        target="7-70",
        note="ten-index filename window per bucket",
    ),
    GlobPattern(
        "index_100_any",
        "**/file_00000??.*",
        tier="window",
        target="73-645",
        note="hundred-index filename window per bucket",
    ),
    GlobPattern(
        "index_100_py",
        "**/file_00000??.py",
        tier="window",
        target="15-179",
        note="filename window narrowed by extension",
    ),
    GlobPattern(
        "index_100_config",
        "**/file_00000??.{json,yaml}",
        tier="window",
        target="4-50",
        note="filename window with extension alternation",
    ),
    GlobPattern(
        "tests_100_py",
        "**/tests/test_file_00000??.py",
        tier="structure",
        target="6-55",
        note="test directory and filename prefix",
    ),
    GlobPattern(
        "docs_100_json",
        "**/docs/**/file_00000??.json",
        tier="structure",
        target="1-23",
        note="recursive docs subtree",
    ),
    GlobPattern(
        "modules_1000_py",
        "**/mod_0[0-4]/**/file_0000???.py",
        tier="structure",
        target="19-268",
        note="module character class",
    ),
    GlobPattern(
        "modules_1000_ts",
        "**/mod_0?/**/file_0000???.ts",
        tier="structure",
        target="22-227",
        note="module single-char wildcard",
    ),
    GlobPattern(
        "auth_1000_py",
        "**/services/auth/**/file_0000???.py",
        tier="structure",
        target="6-87",
        note="fixed service segment",
    ),
]


# Broad-result patterns are available for explicit limit and transfer testing.
STRESS_PATTERNS: List[GlobPattern] = [
    GlobPattern(
        "stress_all_py",
        "**/*.py",
        tier="stress",
        target="~30%",
        note="high cardinality; may hit remote result ceilings",
    ),
]


def compute_expected(pattern: str, rel_paths: List[str]) -> List[str]:
    """Ground-truth matches for ``pattern`` over dataset-root-relative paths."""
    return glob_match.expected_matches(pattern, rel_paths)
