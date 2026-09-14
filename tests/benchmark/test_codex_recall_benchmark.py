# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from benchmark.codex_recall import compare, run


def test_runner_omits_private_queries_and_gold_uris(tmp_path, monkeypatch, capsys):
    private_query = "private deployment incident query"
    private_uri = "viking://user/private/memories/experiences/incident.md"
    fixture = tmp_path / "private.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "id": "positive-1",
                "query": private_query,
                "expected": "accept",
                "gold_uri": private_uri,
            }
        ),
        encoding="utf-8",
    )
    hook = tmp_path / "auto-recall.mjs"
    hook.write_text("// fixture", encoding="utf-8")
    report_path = tmp_path / "report.json"

    def fake_subprocess_run(*_args, **_kwargs):
        payload = {
            "hookSpecificOutput": {
                "additionalContext": f"matched memory ({private_uri})",
            }
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(run.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run.py",
            "--input",
            str(fixture),
            "--output",
            str(report_path),
            "--hook",
            str(hook),
        ],
    )

    assert run.main() == 0
    output = capsys.readouterr().out
    report = report_path.read_text(encoding="utf-8")
    assert private_query not in output + report
    assert private_uri not in output + report
    assert json.loads(report)["summary"]["positive_recall_rate"] == 1.0


def test_load_cases_rejects_duplicate_ids(tmp_path):
    fixture = tmp_path / "duplicates.jsonl"
    fixture.write_text(
        "\n".join(
            [
                '{"id":"same","query":"one","expected":"accept"}',
                '{"id":"same","query":"two","expected":"abstain"}',
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate id"):
        run.load_cases(fixture)


def _report(label, *, false_rate, recall_rate, p50, p95, tokens):
    return {
        "schema_version": 1,
        "label": label,
        "fixture_sha256": "fixture",
        "protocol": {"repeat": 5, "timeout_ms": 10000},
        "variant": {"max_tokens": 800},
        "summary": {
            "false_injection_rate": false_rate,
            "positive_recall_rate": recall_rate,
            "latency_ms_p50": p50,
            "latency_ms_p95": p95,
            "injection_tokens_p95": tokens,
            "tokenizer": "o200k_base",
        },
    }


def test_compare_accepts_improvement_and_rejects_reverse_optimization():
    baseline = _report("main", false_rate=0.1, recall_rate=0.9, p50=600, p95=1000, tokens=800)
    improved = _report("candidate", false_rate=0.0, recall_rate=0.95, p50=500, p95=900, tokens=700)
    regressed = _report("candidate", false_rate=0.0, recall_rate=0.85, p50=500, p95=900, tokens=700)

    accepted = compare.compare_reports(baseline, improved, latency_ratio=0.05, latency_jitter_ms=25)
    rejected = compare.compare_reports(
        baseline, regressed, latency_ratio=0.05, latency_jitter_ms=25
    )

    assert accepted["non_regression_passed"] is True
    assert rejected["non_regression_passed"] is False
    assert rejected["failures"] == ["positive_recall_rate"]


def test_compare_requires_identical_fixture_and_protocol():
    baseline = _report("main", false_rate=0, recall_rate=1, p50=1, p95=2, tokens=3)
    different = dict(baseline, fixture_sha256="other")
    with pytest.raises(ValueError, match="different fixtures"):
        compare.compare_reports(baseline, different, latency_ratio=0.05, latency_jitter_ms=25)

    different_protocol = dict(baseline, protocol={"repeat": 1, "timeout_ms": 10000})
    with pytest.raises(ValueError, match="different benchmark protocols"):
        compare.compare_reports(
            baseline, different_protocol, latency_ratio=0.05, latency_jitter_ms=25
        )


def test_variant_env_accepts_only_recall_tuning():
    assert run.parse_variant_env("OPENVIKING_RECALL_STRATEGY=fast") == (
        "OPENVIKING_RECALL_STRATEGY",
        "fast",
    )
    with pytest.raises(argparse.ArgumentTypeError, match="recall tuning"):
        run.parse_variant_env("OPENVIKING_API_KEY=private")
