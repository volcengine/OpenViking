#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from tau2_common import write_json


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize TAU-2 cell result JSON files.")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    rows = []
    for path in sorted((run_dir / "cell_results").glob("*.json")):
        row = _load_json(path)
        rows.append(row)

    returncodes = [row.get("returncode") for row in rows]
    summary = {
        "run_dir": str(run_dir),
        "cell_count": len(rows),
        "succeeded_cell_count": sum(1 for code in returncodes if code == 0),
        "failed_cell_count": sum(1 for code in returncodes if code != 0),
        "returncodes": returncodes,
        "average_reward": None,
        "notes": [
            "This summarizer only aggregates wrapper cell status in the initial PR.",
            "TAU-2 reward parsing is added once the execution artifact shape is fixed.",
        ],
    }
    rewards = [row.get("reward") for row in rows if isinstance(row.get("reward"), (int, float))]
    if rewards:
        summary["average_reward"] = mean(rewards)
    write_json(run_dir / "summary.json", summary)
    print(f"[tau2] wrote {run_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
