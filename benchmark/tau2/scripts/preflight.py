#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

from tau2_common import domains, load_config, output_dir, run_id, split_file, strategy_ids, tau2_repo, write_json


def _check_import(module: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(module)
    return {"module": module, "ok": spec is not None}


def _split_status(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for domain in domains(config):
        path = split_file(config, domain)
        rows.append(
            {
                "domain": domain,
                "path": str(path),
                "exists": path.is_file(),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight TAU-2 benchmark config.")
    parser.add_argument("--config", type=Path, default=Path(__file__).parents[1] / "config" / "baseline.yaml")
    parser.add_argument("--run-id", default=run_id())
    parser.add_argument("--strict", action="store_true", help="Require optional runtime imports and TAU-2 split files.")
    args = parser.parse_args()

    config = load_config(args.config)
    out = output_dir(config, args.run_id)

    errors: list[str] = []
    try:
        strategy_ids(config)
    except Exception as exc:
        errors.append(str(exc))

    split_rows = _split_status(config)
    if args.strict:
        for row in split_rows:
            if not row["exists"]:
                errors.append(f"missing split file for {row['domain']}: {row['path']}")

    import_rows = [_check_import("openviking"), _check_import("openviking_cli"), _check_import("tau2")]
    if args.strict:
        for row in import_rows:
            if not row["ok"]:
                errors.append(f"missing Python module: {row['module']}")

    report = {
        "status": "failed" if errors else "ok",
        "config": str(args.config.resolve()),
        "run_id": args.run_id,
        "tau2_repo": str(tau2_repo(config)),
        "domains": domains(config),
        "strategies": strategy_ids(config),
        "strict": args.strict,
        "imports": import_rows,
        "split_files": split_rows,
        "errors": errors,
    }
    write_json(out / "preflight.json", report)

    if errors:
        for error in errors:
            print(f"[preflight][ERROR] {error}", file=sys.stderr)
        print(f"[preflight] wrote {out / 'preflight.json'}", file=sys.stderr)
        return 1
    print(f"[preflight][OK] wrote {out / 'preflight.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
