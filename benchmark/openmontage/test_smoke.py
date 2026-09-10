from __future__ import annotations

import json
from pathlib import Path

from scorer import score_case


ROOT = Path(__file__).resolve().parent


def test_fixture_has_expected_stage_and_eval_counts():
    fixture = json.loads((ROOT / "data" / "fixture.json").read_text(encoding="utf-8"))
    assert len(fixture["artifacts"]) == 5
    assert len(fixture["evaluations"]) == 5
    assert {artifact["stage"] for artifact in fixture["artifacts"]} == {
        "brief",
        "script",
        "scene_plan",
        "asset_manifest",
        "render_report",
    }


def test_score_case_requires_uri_and_keywords():
    case = {
        "id": "brief-provider-lock",
        "expected_uri_suffix": "01-brief/brief.md",
        "expected_keywords": ["Remotion", "ImageGen Alpha"],
        "hits": [
            {
                "uri": "viking://resources/openmontage/launch-video/01-brief/brief.md",
                "abstract": "provider lock uses Remotion with ImageGen Alpha",
                "overview": "",
            }
        ],
    }

    result = score_case(case)

    assert result["passed"] is True
    assert result["uri_match"] is True
    assert result["keyword_match"] is True
