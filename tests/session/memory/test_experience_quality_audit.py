# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.experience_quality_audit import (
    ExperienceAuditConfig,
    ExperienceAuditItem,
    audit_experience_items,
)


def _item(name, content="", sources=()):
    return ExperienceAuditItem(
        name=name,
        path=f"/tmp/{name}.md",
        chars=len(content),
        source_trajectories=tuple(sources),
        content=content,
    )


def test_experience_quality_audit_flags_duplicates_and_broad_sources():
    report = audit_experience_items(
        [
            _item("reservation_baggage_addition", "Add bags", ["t1"]),
            _item("baggage_addition_processing", "Add baggage", ["t2"]),
            _item("multi_reservation_modification", "A broad workflow", ["t1", "t2", "t3"]),
        ],
        ExperienceAuditConfig(broad_source_threshold=3),
    )

    assert report["experience_count"] == 3
    assert report["avg_sources"] == 1.67
    assert report["duplicate_name_pairs"] == [
        {
            "left": "reservation_baggage_addition",
            "right": "baggage_addition_processing",
            "name_jaccard": 0.6667,
        }
    ]
    assert report["broad_source_items"][0]["name"] == "multi_reservation_modification"


def test_experience_quality_audit_detects_swallowed_watch_terms():
    report = audit_experience_items(
        [
            _item(
                "flight_multiple_modification_process",
                "Travel insurance does not cover flight change fees.",
                ["t1", "t2"],
            ),
            _item("passenger_details_modification", "Update passenger details.", ["t3"]),
        ],
        ExperienceAuditConfig(watch_terms=("insurance", "passenger details")),
    )

    by_term = {item["term"]: item for item in report["watch_terms"]}
    assert by_term["insurance"]["swallowed_in_content"] is True
    assert by_term["insurance"]["name_matches"] == []
    assert by_term["insurance"]["content_matches"] == ["flight_multiple_modification_process"]
    assert by_term["passenger details"]["swallowed_in_content"] is False
    assert by_term["passenger details"]["name_matches"] == ["passenger_details_modification"]
