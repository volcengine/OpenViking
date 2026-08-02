# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import sys

from benchmark.recall_admission import run


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "result": {
                "stats": {
                    "returned": 2,
                    "admission": {
                        "mode": "shadow",
                        "evaluated": 2,
                        "rejected": 2,
                        "would_abstain": True,
                        "reason_counts": {"below_type_min_score": 2},
                    },
                }
            }
        }


class _FakeClient:
    requests = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, endpoint, json):
        self.requests.append((endpoint, json))
        return _FakeResponse()


def test_runner_scores_shadow_decision_without_printing_query(tmp_path, monkeypatch, capsys):
    private_query = "private regression query must stay out of output"
    input_path = tmp_path / "cases.jsonl"
    input_path.write_text(
        json.dumps({"id": "negative-1", "query": private_query, "expected": "abstain"}),
        encoding="utf-8",
    )
    _FakeClient.requests = []
    monkeypatch.setattr(run.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--input",
            str(input_path),
            "--type-min-score",
            "events=0.5",
        ],
    )

    assert run.main() == 0

    output = capsys.readouterr().out
    assert private_query not in output
    records = [json.loads(line) for line in output.splitlines()]
    assert records[0]["actual"] == "abstain"
    assert records[0]["passed"] is True
    assert records[-1]["summary"]["false_injection_rate"] == 0.0
    assert _FakeClient.requests[0][1]["admission"] == {
        "mode": "shadow",
        "type_min_scores": {"events": 0.5},
        "other_peer_score_delta": 0.0,
    }
