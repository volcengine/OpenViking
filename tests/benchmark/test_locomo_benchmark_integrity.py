import csv
import sys

import pytest

from benchmark.locomo.openviking import judge, run_eval, stat_judge_result


def _qa_item(question_id: str) -> dict:
    return {
        "sample_id": "sample_0",
        "question_id": question_id,
        "question_index": 0,
        "question": "What happened?",
        "answer": "A benchmark answer",
        "category": "1",
        "question_time": "2026-01-01",
        "evidence": [],
        "evidence_text": [],
        "is_invalid": False,
    }


def test_run_eval_writes_explicit_failed_row_for_worker_exception(tmp_path, monkeypatch):
    output_path = tmp_path / "result.csv"
    monkeypatch.setattr(run_eval, "load_locomo_qa", lambda *_args, **_kwargs: [_qa_item("q-1")])

    def fail_worker(**_kwargs):
        raise RuntimeError("deterministic worker failure")

    monkeypatch.setattr(run_eval, "run_vikingbot_chat", fail_worker)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            "unused.json",
            "--output",
            str(output_path),
            "--errors",
            str(tmp_path / "missing-errors.json"),
            "--threads",
            "1",
        ],
    )

    with pytest.raises(
        run_eval.ResultIntegrityError,
        match=r"planned=1, recorded=1, completed=0, failed=1",
    ):
        run_eval.main()

    with output_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["question_id"] == "q-1"
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "RuntimeError: deterministic worker failure"
    assert rows[0]["result"] == ""


def test_validate_result_integrity_rejects_missing_planned_row():
    planned = [_qa_item("q-1"), _qa_item("q-2")]
    completed = [
        {
            **_qa_item("q-1"),
            "status": "completed",
            "error": "",
        }
    ]

    with pytest.raises(
        run_eval.ResultIntegrityError,
        match=r"planned=2, recorded=1.*missing=.*question_id:q-2",
    ):
        run_eval.validate_result_integrity(planned, completed)


def test_statistics_report_expected_failed_and_ungraded_denominators(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "judge-results.csv"
    fieldnames = ["question_id", "category", "is_invalid", "status", "error", "result"]
    rows = [
        {
            "question_id": "correct",
            "category": "1",
            "is_invalid": "false",
            "status": "completed",
            "error": "",
            "result": "CORRECT",
        },
        {
            "question_id": "wrong",
            "category": "1",
            "is_invalid": "false",
            "status": "completed",
            "error": "",
            "result": "WRONG",
        },
        {
            "question_id": "ungraded",
            "category": "1",
            "is_invalid": "false",
            "status": "completed",
            "error": "",
            "result": "",
        },
        {
            "question_id": "failed",
            "category": "1",
            "is_invalid": "false",
            "status": "failed",
            "error": "RuntimeError: failed",
            "result": "",
        },
        {
            "question_id": "adversarial",
            "category": "5",
            "is_invalid": "false",
            "status": "completed",
            "error": "",
            "result": "CORRECT",
        },
    ]
    with input_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(sys, "argv", ["stat_judge_result.py", "--input", str(input_path)])

    stat_judge_result.main()

    output = capsys.readouterr().out
    assert "Expected rows: 4" in output
    assert "Failed rows: 1" in output
    assert "Ungraded rows: 1" in output
    assert "Graded accuracy: 50.00%" in output
    assert "Accuracy (expected denominator): 25.00%" in output
    assert (
        (tmp_path / "summary.txt")
        .read_text(encoding="utf-8")
        .startswith("=== Judge Result Statistics")
    )


def test_judge_does_not_grade_failed_execution_rows():
    rows = [
        {"category": "1", "status": "failed", "result": ""},
        {"category": "1", "status": "completed", "result": ""},
        {"category": "1", "result": ""},
        {"category": "5", "status": "completed", "result": ""},
    ]

    assert judge.get_ungraded_rows(rows) == [1, 2]
    assert judge.get_ungraded_rows(rows, force=True) == [1, 2]
