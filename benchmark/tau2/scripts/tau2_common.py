from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


TAU2_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TAU2_DIR.parents[1]


_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2) or ""
            return os.environ.get(name, default)

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [render_env(item) for item in value]
    if isinstance(value, dict):
        return {key: render_env(item) for key, item in value.items()}
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a mapping: {path}")

    parent_name = raw.pop("extends", None)
    if parent_name:
        parent_path = (path.parent / str(parent_name)).resolve()
        parent = load_config(parent_path)
        raw = deep_merge(parent, raw)
    return render_env(raw)


def resolve_path(path_value: str | Path, *, base: Path | None = None) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return ((base or REPO_ROOT) / path).resolve()


def output_dir(config: dict[str, Any], configured_run_id: str) -> Path:
    raw = config.get("paths", {}).get("output_dir", TAU2_DIR / "result")
    return resolve_path(raw) / configured_run_id


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def strategy_ids(config: dict[str, Any]) -> list[str]:
    strategies = config.get("strategies") or []
    if not isinstance(strategies, list):
        raise ValueError("strategies must be a list")
    ids = []
    for item in strategies:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("each strategy must be a mapping with id")
        ids.append(str(item["id"]))
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate strategy ids: {ids}")
    return ids


def domains(config: dict[str, Any]) -> list[str]:
    values = config.get("benchmark", {}).get("domains") or []
    if not isinstance(values, list) or not values:
        raise ValueError("benchmark.domains must be a non-empty list")
    return [str(item) for item in values]


def tau2_repo(config: dict[str, Any]) -> Path:
    raw = config.get("paths", {}).get("tau2_repo")
    if not raw:
        raise ValueError("paths.tau2_repo is required")
    return resolve_path(raw)


def tau2_cli(config: dict[str, Any]) -> str:
    return str(config.get("paths", {}).get("tau2_cli") or "tau2")


def _git_commit(path: Path) -> str | None:
    if not path.exists():
        return None
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def tau2_context(config: dict[str, Any]) -> dict[str, Any]:
    repo = tau2_repo(config)
    cli = tau2_cli(config)
    return {
        "tau2_repo": str(repo),
        "tau2_repo_exists": repo.exists(),
        "tau2_commit": _git_commit(repo),
        "tau2_cli": cli,
        "tau2_cli_resolved": shutil.which(cli),
    }


def user_simulator_policy(config: dict[str, Any]) -> str:
    policy = config.get("eval", {}).get("user_simulator_policy", "official")
    policy = str(policy)
    if policy not in {"official", "confirmation_aware"}:
        raise ValueError(
            "eval.user_simulator_policy must be 'official' or 'confirmation_aware'"
        )
    return policy


def simulator_policy_report(config: dict[str, Any]) -> dict[str, Any]:
    policy = user_simulator_policy(config)
    repo = tau2_repo(config)
    prompt_paths = [
        repo / "data" / "tau2" / "user_simulator" / "simulation_guidelines.md",
        repo / "data" / "tau2" / "user_simulator" / "simulation_guidelines_tools.md",
    ]
    prompt_text = "\n".join(
        path.read_text(encoding="utf-8") for path in prompt_paths if path.is_file()
    )
    confirmation_aware_prompt = (
        "do not emit" in prompt_text
        and "###STOP###" in prompt_text
        and "confirm" in prompt_text.lower()
    )
    supported = policy == "official" or confirmation_aware_prompt
    return {
        "user_simulator_policy": policy,
        "supported": supported,
        "confirmation_aware_prompt_detected": confirmation_aware_prompt,
        "prompt_files": [str(path) for path in prompt_paths],
        "claim_boundary": (
            "official_tau2_user_simulator"
            if policy == "official"
            else "requires_tau2_confirmation_aware_user_simulator_prompt"
        ),
    }


def split_file(config: dict[str, Any], domain: str) -> Path:
    return tau2_repo(config) / "data" / "tau2" / "domains" / domain / "split_tasks.json"
