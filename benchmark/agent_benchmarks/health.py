"""Audit hidden tool/verifier failures without changing historical scores."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from benchmark.skillflow.verifier_health import check_verifier_stdout


def execution_health(execution):
    broken_paths = []
    overflow = []
    persisted_tools = []
    transport_errors = []
    for i, tool in enumerate(execution.get("tool_executions", [])):
        content = str(tool.get("content", ""))
        arguments = tool.get("arguments", {})
        if (
            tool.get("backend") == "mcp"
            and tool.get("tool_name") in {"write", "read"}
            and "required parameter `path` is missing" in content
            and (arguments.get("path") or arguments.get("file_path"))
        ):
            broken_paths.append(i)
        if content.startswith("<persisted-output>"):
            persisted_tools.append(i)
            if tool.get("tool_name") == "search":
                overflow.append(i)
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("transport_error"):
            transport_errors.append(i)
    return {
        "broken_path_calls": broken_paths,
        "persisted_search_calls": overflow,
        "persisted_tool_calls": persisted_tools,
        "transport_error_calls": transport_errors,
    }


def audit(run_dir):
    protocol = json.loads((run_dir / "protocol.json").read_text())
    rows = []
    judges = Counter()
    code_tests = Counter()
    for path in sorted(run_dir.glob("*/round-*/task-*/result.json")):
        result = json.loads(path.read_text())
        execution_path = next(path.parent.glob("**/execution.json"))
        execution = json.loads(execution_path.read_text())
        tools = execution_health(execution)
        row = {
            "benchmark": result["benchmark"],
            "round": result["round"],
            "index": result["index"],
            "task_id": result["task_id"],
            "historical_passed": result["passed"],
            "result_path": str(path.resolve()),
            "execution_sha256": hashlib.sha256(execution_path.read_bytes()).hexdigest(),
            **tools,
        }
        if result["benchmark"] == "skillflow":
            log = execution_path.parent / "verifier/test-stdout.txt"
            row["verifier_health"] = check_verifier_stdout(log)
        elif result["domain"] == "information_retrieval":
            detail = json.loads((execution_path.parent / "verifier/details.json").read_text())
            judges[detail["method"]] += 1
            assert detail["judge_result"]["parse_error"] is False
            assert detail["judge_result"]["correct"] is result["passed"]
        else:
            detail = json.loads((execution_path.parent / "verifier.json").read_text())
            code_tests["trials"] += 1
            code_tests["test_cases"] += detail["total"]
            assert detail["total"] > 0 and not detail.get("error")
            assert len(detail["results"]) == detail["total"]
        rows.append(row)
    benchmarks = {}
    for name in protocol["selected"]:
        subset = [r for r in rows if r["benchmark"] == name]
        broken = [r for r in subset if r["broken_path_calls"]]
        overflow = [r for r in subset if r["persisted_search_calls"]]
        unhealthy = [
            r for r in subset if not r.get("verifier_health", {"healthy": True})["healthy"]
        ]
        affected = [r for r in subset if r in broken or r in overflow or r in unhealthy]
        benchmarks[name] = {
            "trials": len(subset),
            "broken_path_trials": len(broken),
            "broken_path_calls": sum(len(r["broken_path_calls"]) for r in broken),
            "verifier_unhealthy_trials": len(unhealthy),
            "verifier_unhealthy_task_ids": sorted({r["task_id"] for r in unhealthy}),
            "persisted_search_trials": len(overflow),
            "persisted_search_calls": sum(len(r["persisted_search_calls"]) for r in overflow),
            "affected_trials_union": len(affected),
            "stability_conclusion_supported": not affected,
        }
    # Verify task bytes against the previously downloaded pinned HF tree.
    sf_repo = Path(protocol["selected"]["skillflow"][0]["path"]).parents[2]
    revision = "ecaadb0e25d5d5cfd87bd86d81e77b4abe3a00bc"
    tree = json.loads((sf_repo / f".cache/huggingface/trees/{revision}.json").read_text())["files"]
    receipts = []
    for task in protocol["selected"]["skillflow"]:
        folder = Path(task["path"])
        for p in sorted(folder.rglob("*")):
            if not p.is_file() or not (
                p.name in {"instruction.md", "task.toml"}
                or p.relative_to(folder).parts[0] in {"tests", "environment", "solution"}
            ):
                continue
            relative = str(p.relative_to(sf_repo))
            if relative not in tree:
                receipts.append(
                    {"path": relative, "matches": False, "reason": "not_in_pinned_tree"}
                )
                continue
            expected = tree[relative]
            data = p.read_bytes()
            if expected.get("lfs_sha256"):
                actual = hashlib.sha256(data).hexdigest()
                wanted = expected["lfs_sha256"]
            elif expected.get("lfs"):
                actual = hashlib.sha256(data).hexdigest()
                wanted = (
                    expected["lfs"]["sha256"]
                    if "sha256" in expected["lfs"]
                    else expected["lfs"]["oid"].removeprefix("sha256:")
                )
            else:
                actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
                wanted = expected["blob_id"]
            receipts.append(
                {
                    "path": relative,
                    "matches": actual == wanted,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    return {
        "audit_version": 1,
        "historical_results_preserved": True,
        "new_llm_rollouts": 0,
        "benchmarks": benchmarks,
        "ir_judge_methods": dict(judges),
        "code_verification": dict(code_tests),
        "task_dataset_revision": revision,
        "source_receipts": receipts,
        "trials": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    result = audit(args.run_dir.resolve())
    destination = args.run_dir / "health-audit"
    destination.mkdir(exist_ok=True)
    (destination / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "benchmarks": result["benchmarks"],
                "source_files": len(result["source_receipts"]),
                "source_mismatches": [r for r in result["source_receipts"] if not r["matches"]],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
