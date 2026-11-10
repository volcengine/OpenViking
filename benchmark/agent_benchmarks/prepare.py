"""Create explicit, disjoint train/test manifests from locally prepared tasks."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from benchmark.agent_benchmarks.catalog import Catalog, checked_evo_splits


def skillflow_splits(roots: list[Path], *, seed: int, fraction: float):
    if not 0 < fraction < 1:
        raise ValueError("train-fraction must be between 0 and 1")
    families = defaultdict(dict)
    # Earlier roots win: pass repaired/validated task variants before original data.
    for root in roots:
        for config in sorted(root.glob("*/*/task.toml")):
            path = config.parent.resolve()
            families[path.parent.name].setdefault(
                path.name, {"domain": path.parent.name, "task_id": path.name, "path": str(path)}
            )
    result = {"train": [], "test": []}
    for family, tasks in sorted(families.items()):
        items = list(tasks.values())
        random.Random(f"{seed}:{family}").shuffle(items)
        count = min(len(items) - 1, max(1, int(len(items) * fraction))) if len(items) > 1 else 0
        result["train"].extend(items[:count])
        result["test"].extend(items[count:])
    if not any(result.values()):
        raise ValueError("No complete local SkillFlow tasks found")
    return result


def evo_splits(selection: Path, repo: Path):
    data = json.loads(selection.read_text())
    rows = data.get("evoagentbench", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(
            "Evo selection must be a list, or eligible-pools.json containing evoagentbench"
        )
    result = {"train": [], "test": []}
    splits = {}
    for row in rows:
        domain, tid, split = row["domain"], row["task_id"], row["official_split"]
        if domain not in splits:
            splits[domain] = checked_evo_splits(repo, domain)
        if split not in result or tid not in splits[domain][split]:
            raise ValueError("Evo selection violates the official split")
        result[split].append({"domain": domain, "task_id": tid})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=("skillflow", "evoagentbench"))
    parser.add_argument(
        "--runtime-config",
        required=True,
        type=Path,
        help="JSON with Python/runtime/data paths and model settings; credentials use environment variables",
    )
    parser.add_argument(
        "--tasks-root",
        action="append",
        type=Path,
        default=[],
        help="SkillFlow test_tasks root; repeat with repaired variants first",
    )
    parser.add_argument(
        "--selection", type=Path, help="Evo locally prepared pool with official_split per task"
    )
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--train-fraction", type=float, default=0.5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--image-pins", type=Path, help="Optional validated SkillFlow parent image pin list"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an existing frozen manifest")
    runtime = json.loads(args.runtime_config.read_text())
    if args.dataset == "skillflow":
        splits = skillflow_splits(args.tasks_root, seed=args.seed, fraction=args.train_fraction)
        note = "Explicit local held-out split within each family, not an official SkillFlow split or lifelong task order. Singleton families are eval-only."
    else:
        if args.selection is None:
            parser.error("Evo requires --selection of locally prepared official tasks")
        splits = evo_splits(args.selection, Path(runtime["repo"]))
        note = "Official train/test IDs preserved; no resplitting across the boundary."
    if args.image_pins:
        pins = {x["tag"]: x["image_id"] for x in json.loads(args.image_pins.read_text())}
        for rows in splits.values():
            for item in rows:
                if args.dataset != "skillflow":
                    raise ValueError("image-pins applies only to SkillFlow")
                text = (Path(item["path"]) / "environment/Dockerfile").read_text().strip()
                if text.startswith("FROM ") and "\n" not in text and text[5:].strip() in pins:
                    item["parent_image"] = text[5:].strip()
                    item["parent_image_id"] = pins[item["parent_image"]]
    data = {
        "version": 1,
        "dataset": args.dataset,
        "runtime": runtime,
        "splits": splits,
        "seed": args.seed,
        "split_note": note,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    catalog = Catalog(args.output)
    print(
        json.dumps(
            {
                "manifest": str(args.output),
                "counts": {k: len(v) for k, v in splits.items()},
                "runtime_fingerprint": catalog.fingerprint,
            }
        )
    )


if __name__ == "__main__":
    main()
