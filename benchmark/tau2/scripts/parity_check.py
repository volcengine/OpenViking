#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tau2_common import write_json


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare OpenViking TAU-2 artifacts against a harness reference.")
    parser.add_argument("--ov-run-plan", type=Path, required=True)
    parser.add_argument("--harness-run-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ov_plan = _load_json(args.ov_run_plan)
    harness_plan = _load_json(args.harness_run_plan)

    ov_cells = ov_plan.get("cells") or []
    harness_cells = harness_plan.get("cells") or harness_plan.get("treatments") or []
    report = {
        "status": "ok" if len(ov_cells) == len(harness_cells) else "mismatch",
        "ov_run_plan": str(args.ov_run_plan.resolve()),
        "harness_run_plan": str(args.harness_run_plan.resolve()),
        "ov_cell_count": len(ov_cells),
        "harness_cell_count": len(harness_cells),
        "checks": {
            "cell_count_match": len(ov_cells) == len(harness_cells),
        },
        "notes": [
            "Initial parity is intentionally structural.",
            "Train payload, retrieval trace, and scoreboard parity should be added as each migration layer lands.",
        ],
    }
    write_json(args.output, report)
    if report["status"] != "ok":
        print(f"[parity][WARN] wrote mismatch report: {args.output}")
        return 1
    print(f"[parity][OK] wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
