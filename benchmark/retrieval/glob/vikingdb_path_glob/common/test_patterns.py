"""Tests for the default glob benchmark pattern set."""

from vikingdb_path_glob.common import dataset
from vikingdb_path_glob.common.patterns import PATTERNS, STRESS_PATTERNS, compute_expected


def test_default_patterns_are_bounded_at_every_scale():
    assert len(PATTERNS) == 13
    for scale, _bucket, _count in dataset.SCALES:
        relpaths = list(dataset.iter_scale_relpaths(scale, seed=42))
        for spec in PATTERNS:
            assert len(compute_expected(spec.pattern, relpaths)) <= 1_000


def test_high_cardinality_patterns_are_opt_in():
    assert all(spec not in PATTERNS for spec in STRESS_PATTERNS)
    assert any(spec.pattern == "**/*.py" for spec in STRESS_PATTERNS)
