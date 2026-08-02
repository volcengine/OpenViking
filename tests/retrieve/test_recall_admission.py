# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.retrieve.recall_admission import (
    REASON_BELOW_OTHER_PEER_MIN_SCORE,
    REASON_BELOW_TYPE_MIN_SCORE,
    RecallAdmissionConfig,
    RecallAdmissionTracker,
    decide_recall_admission,
)


def test_type_threshold_uses_stricter_value_and_includes_boundary():
    config = RecallAdmissionConfig.from_value(
        {"mode": "enforce", "type_min_scores": {"events": 0.5}}
    )

    below = decide_recall_admission(
        score=0.499,
        memory_type="events",
        origin="self",
        min_score=0.35,
        config=config,
    )
    boundary = decide_recall_admission(
        score=0.5,
        memory_type="events",
        origin="self",
        min_score=0.35,
        config=config,
    )

    assert below.admitted is False
    assert below.reason == REASON_BELOW_TYPE_MIN_SCORE
    assert below.required_score == 0.5
    assert boundary.admitted is True


def test_other_peer_delta_only_raises_other_peer_requirement():
    config = RecallAdmissionConfig.from_value(
        {
            "mode": "enforce",
            "type_min_scores": {"preferences": 0.4},
            "other_peer_score_delta": 0.1,
        }
    )

    self_decision = decide_recall_admission(
        score=0.45,
        memory_type="preferences",
        origin="self",
        min_score=0.35,
        config=config,
    )
    peer_decision = decide_recall_admission(
        score=0.45,
        memory_type="preferences",
        origin="other_peer",
        min_score=0.35,
        config=config,
    )

    assert self_decision.admitted is True
    assert peer_decision.admitted is False
    assert peer_decision.reason == REASON_BELOW_OTHER_PEER_MIN_SCORE
    assert peer_decision.required_score == 0.5


def test_shadow_observes_rejection_without_enforcing_it():
    tracker = RecallAdmissionTracker(
        config=RecallAdmissionConfig.from_value(
            {"mode": "shadow", "type_min_scores": {"entities": 0.6}}
        ),
        min_score=0.35,
    )

    assert tracker.evaluate(score=0.4, memory_type="entities", origin="self") is True
    assert tracker.to_stats() == {
        "mode": "shadow",
        "evaluated": 1,
        "accepted": 0,
        "rejected": 1,
        "reason_counts": {REASON_BELOW_TYPE_MIN_SCORE: 1},
        "type_min_scores": {"entities": 0.6},
        "other_peer_score_delta": 0.0,
        "would_abstain": True,
        "abstained": False,
    }


def test_nonfinite_score_is_rejected_in_enforce_mode():
    config = RecallAdmissionConfig.from_value(
        {"mode": "enforce", "type_min_scores": {"events": 0.4}}
    )

    decision = decide_recall_admission(
        score=float("inf"),
        memory_type="events",
        origin="self",
        min_score=0.35,
        config=config,
    )

    assert decision.admitted is False
    assert decision.reason == REASON_BELOW_TYPE_MIN_SCORE


def test_invalid_type_threshold_is_ignored_instead_of_becoming_zero():
    config = RecallAdmissionConfig.from_value(
        {
            "mode": "enforce",
            "type_min_scores": {"events": "invalid", "entities": 2, "unknown": 0.9},
        }
    )

    assert config.type_min_scores == {"entities": 1.0}
