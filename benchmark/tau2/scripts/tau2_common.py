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
CONFIRMATION_AWARE_UPSTREAM_PR = "https://github.com/sierra-research/tau2-bench/pull/297"
CONFIRMATION_AWARE_APPENDIX = """

- If the agent asks you to confirm, authorize, or approve a backend action,
  reply with the requested confirmation but do not emit `###STOP###` in the
  same turn.
- Emit `###STOP###` only after the agent clearly reports that the requested
  backend action has been completed, or when the official transfer /
  out-of-scope rules apply.
"""


_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_litellm_env() -> dict[str, Any]:
    aliases = []
    if not os.environ.get("OPENAI_API_KEY") and os.environ.get("ARK_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["ARK_API_KEY"]
        aliases.append("OPENAI_API_KEY<-ARK_API_KEY")
    ark_base = os.environ.get("ARK_BASE_URL")
    openai_base = os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL")
    if not openai_base and ark_base:
        os.environ["OPENAI_API_BASE"] = ark_base
        os.environ["OPENAI_BASE_URL"] = ark_base
        aliases.append("OPENAI_API_BASE<-ARK_BASE_URL")
    elif os.environ.get("OPENAI_API_BASE") and not os.environ.get("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = os.environ["OPENAI_API_BASE"]
        aliases.append("OPENAI_BASE_URL<-OPENAI_API_BASE")
    elif os.environ.get("OPENAI_BASE_URL") and not os.environ.get("OPENAI_API_BASE"):
        os.environ["OPENAI_API_BASE"] = os.environ["OPENAI_BASE_URL"]
        aliases.append("OPENAI_API_BASE<-OPENAI_BASE_URL")
    return {
        "aliases": aliases,
        "has_api_key": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ARK_API_KEY")),
        "has_base_url": bool(
            os.environ.get("OPENAI_API_BASE")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("ARK_BASE_URL")
        ),
    }


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


def _prompt_paths(repo: Path) -> list[Path]:
    return [
        repo / "data" / "tau2" / "user_simulator" / "simulation_guidelines.md",
        repo / "data" / "tau2" / "user_simulator" / "simulation_guidelines_tools.md",
    ]


def _has_confirmation_aware_prompt(prompt_text: str) -> bool:
    normalized = " ".join(prompt_text.split())
    return (
        "reply with the requested confirmation" in normalized
        and "do not emit `###STOP###` in the same turn" in normalized
    )


def _ensure_confirmation_aware_prompt(repo: Path) -> bool:
    patched = False
    for path in _prompt_paths(repo):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if _has_confirmation_aware_prompt(text):
            continue
        backup = path.with_suffix(path.suffix + ".openviking.bak")
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        path.write_text(text.rstrip() + CONFIRMATION_AWARE_APPENDIX + "\n", encoding="utf-8")
        patched = True
    return patched


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
    patch_applied = policy == "confirmation_aware" and _ensure_confirmation_aware_prompt(repo)
    patch_mode = "direct_prompt_append" if patch_applied else "none"
    if policy == "confirmation_aware":
        if not patch_applied:
            patch_mode = "upstream_or_existing_prompt"

    prompt_paths = _prompt_paths(repo)
    prompt_text = "\n".join(
        path.read_text(encoding="utf-8") for path in prompt_paths if path.is_file()
    )
    confirmation_aware_prompt = _has_confirmation_aware_prompt(prompt_text)
    supported = policy == "official" or confirmation_aware_prompt
    claim_boundary = "confirmation_aware_user_simulator_prompt"
    if policy == "official":
        claim_boundary = (
            "official_policy_with_confirmation_aware_checkout"
            if confirmation_aware_prompt
            else "official_tau2_user_simulator"
        )
    return {
        "user_simulator_policy": policy,
        "supported": supported,
        "confirmation_aware_prompt_detected": confirmation_aware_prompt,
        "confirmation_aware_upstream_pr": CONFIRMATION_AWARE_UPSTREAM_PR,
        "patch_applied": patch_applied,
        "patch_mode": patch_mode,
        "prompt_files": [str(path) for path in prompt_paths],
        "backup_files": [
            str(path.with_suffix(path.suffix + ".openviking.bak"))
            for path in prompt_paths
            if path.with_suffix(path.suffix + ".openviking.bak").exists()
        ],
        "claim_boundary": claim_boundary,
    }


def split_file(config: dict[str, Any], domain: str) -> Path:
    return tau2_repo(config) / "data" / "tau2" / "domains" / domain / "split_tasks.json"
