"""Frozen, disjoint benchmark selections shared by HTTP serving and workers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

EVO_DOMAINS = {"code_implementation", "information_retrieval", "knowledge_work"}


def checked_evo_splits(repo: Path, domain: str) -> dict[str, list[str]]:
    directory = repo / "benchmark/data/splits"
    path = directory / f"{domain}.json"
    manifest = json.loads((directory / "manifest.json").read_text())
    entries = [x for x in manifest["splits"].values() if x["file"] == path.name]
    if len(entries) != 1 or hashlib.sha256(path.read_bytes()).hexdigest() != entries[0]["sha256"]:
        raise ValueError(f"Official EvoAgentBench split checksum mismatch: {path}")
    splits = json.loads(path.read_text())
    for split in ("train", "test"):
        if len(splits[split]) != entries[0][split] or len(set(splits[split])) != len(splits[split]):
            raise ValueError(f"Invalid official {split} split: {domain}")
    if set(splits["train"]) & set(splits["test"]):
        raise ValueError("Official EvoAgentBench train/test overlap")
    return splits


class Catalog:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.data = json.loads(self.path.read_text())
        self.dataset = self.data["dataset"]
        if self.dataset not in {"skillflow", "evoagentbench"}:
            raise ValueError("dataset must be skillflow or evoagentbench")
        if self.data.get("version") != 1:
            raise ValueError("Unsupported adapter manifest version")
        self.runtime = self.data["runtime"]
        self.entries: dict[tuple[str, str, str], dict[str, Any]] = {}
        identities: set[tuple[str, str]] = set()
        self.splits = {name: list(self.data["splits"][name]) for name in ("train", "test")}
        evo = {}
        for split, items in self.splits.items():
            for item in items:
                domain, task_id = item["domain"], item["task_id"]
                identity = (domain, task_id)
                if identity in identities:
                    raise ValueError(f"Duplicate task or train/test overlap: {identity}")
                identities.add(identity)
                if self.dataset == "skillflow":
                    task = Path(item["path"])
                    if task.name != task_id or task.parent.name != domain:
                        raise ValueError(f"SkillFlow path/identity mismatch: {task}")
                    for part in (
                        "instruction.md",
                        "task.toml",
                        "environment/Dockerfile",
                        "tests/test.sh",
                    ):
                        if not (task / part).is_file():
                            raise ValueError(f"Incomplete SkillFlow task: {task / part}")
                else:
                    if domain not in EVO_DOMAINS:
                        raise ValueError(f"Evo domain not prepared by this adapter: {domain}")
                    if domain not in evo:
                        evo[domain] = checked_evo_splits(Path(self.runtime["repo"]), domain)
                    if task_id not in evo[domain][split]:
                        raise ValueError(f"Evo task is outside official {split}: {identity}")
                self.entries[(split, domain, task_id)] = item
        # Include immutable task/checker content and the runtime implementation in cache identity.
        digest = hashlib.sha256(self.path.read_bytes())
        for key, item in sorted(self.entries.items()):
            if self.dataset == "skillflow":
                task = Path(item["path"])
                files = [
                    task / "instruction.md",
                    task / "task.toml",
                    *sorted((task / "tests").rglob("*")),
                    *sorted((task / "environment").rglob("*")),
                ]
                for file in files:
                    if file.is_file() and "__pycache__" not in file.parts:
                        digest.update(str((key, str(file.relative_to(task)))).encode())
                        with file.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
            else:
                digest.update(json.dumps(evo[key[1]], sort_keys=True).encode())
        for directory in (
            Path(__file__).parent,
            Path(__file__).parents[1] / "skillflow",
            Path(__file__).parents[1] / "evoagentbench",
        ):
            for file in sorted(directory.glob("*.py")):
                digest.update(file.name.encode())
                digest.update(file.read_bytes())
        for name in ("benchmark_config", "corpus_manifest"):
            if self.runtime.get(name):
                digest.update(Path(self.runtime[name]).read_bytes())
        for key in (
            "retrieval_queries",
            "knowledge_dataset",
            "reference_dir",
            "meta_prompts_dir",
            "code_cache",
            "cache",
            "openspace_repo",
        ):
            value = self.runtime.get(key)
            if not value:
                continue
            path = Path(value)
            if path.is_file():
                files = [path]
            elif key == "openspace_repo":
                files = sorted((path / "openspace/openspace").rglob("*.py")) + sorted(
                    (path / "openspace/openspace/config").glob("*.json")
                )
            elif key == "cache":
                files = [path / "corpus.sqlite"] if (path / "corpus.sqlite").exists() else []
            else:
                files = sorted(
                    x for x in path.rglob("*") if x.is_file() and "__pycache__" not in x.parts
                )
            for file in files:
                stat = file.stat()
                digest.update(json.dumps([str(file), stat.st_size, stat.st_mtime_ns]).encode())
        self.fingerprint = digest.hexdigest()

    def rows(self, domain: str, split: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        if split not in self.splits:
            raise ValueError("split must be train or test")
        domains = {x["domain"] for items in self.splits.values() for x in items}
        if domain != "all" and domain not in domains:
            raise ValueError(f"Unknown benchmark domain/family: {domain}")
        rows = [x for x in self.splits[split] if domain == "all" or x["domain"] == domain]
        indices = filters.get("task_indices")
        if indices is not None:
            if any(
                isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(rows)
                for i in indices
            ):
                raise ValueError("task_indices outside the selected split/domain")
            rows = [rows[i] for i in dict.fromkeys(indices)]
        return rows

    def case(self, item: dict[str, Any], split: str):
        from openviking.session.train.domain import Case, Rubric, RubricCriterion

        return Case(
            name=f"{item['domain']}/{item['task_id']}",
            task_signature=f"{self.dataset}: {item['domain']}",
            input={
                "dataset": self.dataset,
                "domain": item["domain"],
                "task_id": item["task_id"],
                "split": split,
            },
            rubric=Rubric(
                name="official_verifier",
                description="Native benchmark verifier",
                criteria=[
                    RubricCriterion(
                        name="official_verifier",
                        description="Pass the native benchmark verifier",
                        required=True,
                        weight=1.0,
                    )
                ],
            ),
            metadata={
                "dataset": self.dataset,
                "domain": item["domain"],
                "split": split,
                "benchmark_revision": self.fingerprint,
            },
        )

    def resolve(self, case):
        if (
            case.input.get("dataset") != self.dataset
            or case.metadata.get("benchmark_revision") != self.fingerprint
        ):
            raise ValueError("Case belongs to another dataset/runtime revision")
        key = (case.input.get("split"), case.input.get("domain"), case.input.get("task_id"))
        if key not in self.entries:
            raise ValueError("Case is outside the frozen selection")
        return self.entries[key]
