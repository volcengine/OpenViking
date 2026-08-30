#!/usr/bin/env python3
"""Detect incremental repo-memory updates from git and optional provider evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
BUILDER_SCRIPTS_DIR = SCRIPT_DIR
BUILDER_DEFAULTS_PATH = SKILL_DIR / "defaults.json"
BUILDER_SKILL_DIR = SKILL_DIR
BUILDER_HELPER_FILES = {
    "defaults": BUILDER_DEFAULTS_PATH,
    "validate_memory": BUILDER_SCRIPTS_DIR / "validate_memory.py",
    "github_resource_facets": BUILDER_SCRIPTS_DIR / "github_resource_facets.py",
    "gitlab_resource_facets": BUILDER_SCRIPTS_DIR / "gitlab_resource_facets.py",
}
PREFERRED_REMOTE_NAMES = ("upstream", "origin")
HISTORY_MODES = {"none", "commits-only", "local-only", "provider", "provider-required"}
FALLBACK_DEFAULTS = {
    "repoHistory": {
        "mode": "local-only",
        "limits": {
            "prs": 30,
            "issues": 30,
        },
    },
    # Kept for compatibility with defaults.json v1.
    "limits": {
        "prs": 30,
        "issues": 30,
    },
    "summaryChars": 4000,
}


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def git(repo: Path, args: list[str], message: str) -> str:
    if shutil.which("git") is None:
        raise SystemExit("git is required for repo-memory incremental update detection")
    result = run(["git", "-C", str(repo), *args], repo)
    if result.returncode != 0:
        raise SystemExit(f"{message}:\n{result.stderr.strip()}")
    return result.stdout.strip()


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def default_int(settings: dict[str, Any], path: list[str], fallback: int) -> int:
    value: Any = settings
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{BUILDER_DEFAULTS_PATH}: {'.'.join(path)} must be an integer")
    return value


def default_limit(settings: dict[str, Any], key: str) -> int:
    history = settings.get("repoHistory")
    if isinstance(history, dict) and isinstance(history.get("limits"), dict) and history["limits"].get(key) is not None:
        return default_int(settings, ["repoHistory", "limits", key], FALLBACK_DEFAULTS["repoHistory"]["limits"][key])
    return default_int(settings, ["limits", key], FALLBACK_DEFAULTS["limits"][key])


def default_history_mode(settings: dict[str, Any]) -> str:
    history = settings.get("repoHistory")
    if not isinstance(history, dict):
        return "local-only"
    mode = history.get("mode", "local-only")
    if not isinstance(mode, str) or mode not in HISTORY_MODES:
        raise ValueError(f"{BUILDER_DEFAULTS_PATH}: repoHistory.mode must be one of {', '.join(sorted(HISTORY_MODES))}")
    return mode


def history_collect(mode: str) -> dict[str, bool]:
    return {
        "commits": mode in {"commits-only", "local-only", "provider", "provider-required"},
        "provider": mode in {"provider", "provider-required"},
    }


def load_default_settings() -> tuple[dict[str, Any], str]:
    if not BUILDER_DEFAULTS_PATH.exists():
        return FALLBACK_DEFAULTS, "hardcoded_fallback"
    data = load_json(BUILDER_DEFAULTS_PATH, FALLBACK_DEFAULTS)
    if not isinstance(data, dict):
        raise ValueError(f"{BUILDER_DEFAULTS_PATH}: expected a JSON object")
    return data, str(BUILDER_DEFAULTS_PATH)


def validate_range(parser: argparse.ArgumentParser, name: str, value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        parser.error(f"{name} must be from {minimum} to {maximum}")


def apply_effective_settings(args: argparse.Namespace, parser: argparse.ArgumentParser) -> argparse.Namespace:
    try:
        defaults, source = load_default_settings()
        default_mode = default_history_mode(defaults)
        default_pr_limit = default_limit(defaults, "prs")
        default_issue_limit = default_limit(defaults, "issues")
        default_summary_chars = default_int(defaults, ["summaryChars"], FALLBACK_DEFAULTS["summaryChars"])
    except ValueError as exc:
        parser.error(str(exc))

    history_mode = args.history_mode or default_mode
    collect = history_collect(history_mode)
    if args.provider_mode == "off":
        if history_mode == "provider-required":
            parser.error("--provider-mode off cannot be combined with --history-mode provider-required")
        collect = {**collect, "provider": False}
    pr_limit = args.pr_limit if args.pr_limit is not None else default_pr_limit
    issue_limit = args.issue_limit if args.issue_limit is not None else default_issue_limit
    summary_chars = args.summary_chars if args.summary_chars is not None else default_summary_chars

    validate_range(parser, "--pr-limit", pr_limit, 1, 100)
    validate_range(parser, "--issue-limit", issue_limit, 1, 100)
    if summary_chars < 100:
        parser.error("--summary-chars must be at least 100")

    overrides: dict[str, Any] = {}
    if args.history_mode is not None:
        overrides["history_mode"] = args.history_mode
    if args.pr_limit is not None:
        overrides["pr_limit"] = args.pr_limit
    if args.issue_limit is not None:
        overrides["issue_limit"] = args.issue_limit
    if args.summary_chars is not None:
        overrides["summary_chars"] = args.summary_chars

    args.history_mode = history_mode
    args.history_collect = collect
    args.pr_limit = pr_limit
    args.issue_limit = issue_limit
    args.summary_chars = summary_chars
    args.effective_settings = {
        "history": {
            "mode": history_mode,
            "collect": collect,
        },
        "limits": {
            "prs": pr_limit,
            "issues": issue_limit,
        },
        "summary_chars": summary_chars,
        "source": source,
        "overrides": overrides,
    }
    return args


def file_fingerprint(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if path.exists():
        stat = path.stat()
        item["mtime_ns"] = stat.st_mtime_ns
        item["size_bytes"] = stat.st_size
    return item


def builder_helper_report() -> dict[str, Any]:
    return {
        "skill_dir": str(BUILDER_SKILL_DIR),
        "scripts_dir": str(BUILDER_SCRIPTS_DIR),
        "files": {
            name: file_fingerprint(path)
            for name, path in BUILDER_HELPER_FILES.items()
        },
    }


def parse_frontmatter(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            fields[key] = value
    return fields


def resolve_memory(repo: Path, memory_arg: str | None) -> Path:
    return Path(memory_arg).expanduser().resolve() if memory_arg else repo / ".repo_memory"


def body_after_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def commit_baseline(memory: Path, repo: Path | None = None, head_sha: str = "") -> str:
    profile_head = parse_frontmatter(memory / "PROFILE.md").get("local_head", "")
    if profile_head:
        return profile_head
    text_path = memory / "resources" / "commits.md"
    if text_path.exists():
        text = body_after_frontmatter(text_path.read_text(encoding="utf-8"))
        shas = re.findall(r"(?im)^-\s*SHA:\s*`?([0-9a-f]{7,40})`?", text)
        if repo is not None and head_sha and shas:
            nearest = nearest_ancestor(repo, shas, head_sha)
            if nearest:
                return nearest
        if shas:
            return shas[0]
    return ""


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor:
        return False
    result = run(["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant], repo)
    return result.returncode == 0


def nearest_ancestor(repo: Path, candidates: list[str], descendant: str) -> str:
    unique_candidates = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    if not unique_candidates:
        return ""
    result = run(["git", "-C", str(repo), "rev-list", descendant], repo)
    if result.returncode != 0:
        return ""
    for sha in [line.strip() for line in result.stdout.splitlines() if line.strip()]:
        for candidate in unique_candidates:
            if sha.startswith(candidate):
                return candidate
    return ""


def commit_delta(repo: Path, baseline_sha: str, head_sha: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not baseline_sha:
        return [], {
            "status": "skipped",
            "reason": "missing_baseline_commit",
            "message": "No stored local commit baseline was found in .repo_memory.",
        }
    if baseline_sha == head_sha:
        return [], {"status": "current", "reason": ""}
    if not is_ancestor(repo, baseline_sha, head_sha):
        return [], {
            "status": "skipped",
            "reason": "baseline_not_ancestor_of_head",
            "message": "Stored repo-memory commit baseline is not an ancestor of the current HEAD; history was probably rebased or force-pushed.",
        }
    shas = git(repo, ["rev-list", "--reverse", f"{baseline_sha}..{head_sha}"], "git could not list new commits")
    out: list[dict[str, Any]] = []
    for sha in [line.strip() for line in shas.splitlines() if line.strip()]:
        fields = git(
            repo,
            ["show", "-s", "--date=iso-strict", "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s", sha],
            f"git could not read commit {sha}",
        ).split("\x1f")
        full_sha, short_sha, author, authored_at, title = (fields + [""] * 5)[:5]
        files = git(repo, ["show", "--format=", "--name-only", "--no-renames", sha], f"git could not list files for {sha}")
        out.append({
            "sha": full_sha,
            "short_sha": short_sha,
            "title": title,
            "author": author,
            "authored_at": authored_at,
            "files": [line for line in files.splitlines() if line],
        })
    return out, {"status": "ok", "reason": "", "count": len(out)}


def markdown_sections(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    text = body_after_frontmatter(path.read_text(encoding="utf-8"))
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).strip(), text[start:end]))
    return sections


def markdown_field(body: str, label: str) -> str:
    pattern = rf"(?im)^-\s*{re.escape(label)}:\s*(.+?)\s*$"
    match = re.search(pattern, body)
    if not match:
        return ""
    value = match.group(1).strip()
    return value.strip().strip("`").strip()


def first_code_value(text: str) -> str:
    match = re.search(r"`([^`]+)`", text)
    return match.group(1).strip() if match else text.strip()


def section_number(title: str) -> int | None:
    match = re.search(r"[#!#]\s*(\d+)", title)
    return int(match.group(1)) if match else None


def section_title(title: str) -> str:
    return title.split(":", 1)[1].strip() if ":" in title else title.strip()


def branch_parts(value: str) -> tuple[str, str]:
    if "<-" not in value:
        return "", ""
    base, head = value.split("<-", 1)
    return base.strip(), head.strip()


def resource_items(memory: Path, resource_name: str) -> list[dict[str, Any]]:
    path = memory / "resources" / resource_name
    kind = "issue" if resource_name == "issues.md" else "pr"
    out: list[dict[str, Any]] = []
    for title, body in markdown_sections(path):
        number = section_number(title)
        if number is None:
            continue
        item: dict[str, Any] = {
            "number": number,
            "title": section_title(title),
            "source": "resource",
        }
        state = first_code_value(markdown_field(body, "State"))
        if state:
            item["state"] = state
        url = markdown_field(body, "URL")
        if url:
            item["url"] = url
        if kind == "pr":
            base_ref, head_ref = branch_parts(first_code_value(markdown_field(body, "Branch")))
            if base_ref:
                item["base_ref"] = base_ref
            if head_ref:
                item["head_ref"] = head_ref
        out.append(item)
    return out


def first_int(values: Any) -> int | None:
    if isinstance(values, int):
        return values
    if isinstance(values, list):
        for value in values:
            if isinstance(value, int):
                return value
    return None


def facet_number(facet: dict[str, Any], source_type: str) -> int | None:
    values = facet.get("issues") if source_type == "issue" else facet.get("prs")
    number = first_int(values)
    if number is not None:
        return number
    match = re.search(r"\.(\d+)$", str(facet.get("facetId", "")))
    return int(match.group(1)) if match else None


AUTHORING_FACET_KEYS = [
    "summary",
    "labels",
    "commits",
    "commit_headlines",
    "review_decision",
    "review_states",
    "files",
    "symbols",
    "evidence",
    "changed_files",
    "additions",
    "deletions",
]


def keep_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def facet_items(facets: Any, source_type: str) -> list[dict[str, Any]]:
    if not isinstance(facets, list):
        return []
    out: list[dict[str, Any]] = []
    for facet in facets:
        if not isinstance(facet, dict) or facet.get("sourceType") != source_type:
            continue
        number = facet_number(facet, source_type)
        if number is None:
            continue
        item: dict[str, Any] = {
            "number": number,
            "title": str(facet.get("title") or ""),
            "state": str(facet.get("state") or ""),
            "updated_at": str(facet.get("updatedAt") or facet.get("updated_at") or ""),
            "url": str(facet.get("url") or ""),
            "source": "provider_facet",
        }
        facet_id = str(facet.get("facetId") or "")
        if facet_id:
            item["raw_lookup"] = f"facetId={facet_id}"
        for key in AUTHORING_FACET_KEYS:
            if key in facet:
                item[key] = facet[key]
        if source_type == "pr":
            item["base_ref"] = str(facet.get("base_ref") or "")
            item["head_ref"] = str(facet.get("head_ref") or "")
            item["merged_at"] = str(facet.get("mergedAt") or facet.get("merged_at") or "")
            item["closed_at"] = str(facet.get("closedAt") or facet.get("closed_at") or "")
            linked_issues = facet.get("issues")
            if keep_value(linked_issues):
                item["linked_issues"] = linked_issues
        out.append({key: value for key, value in item.items() if keep_value(value)})
    return out


def raw_provider_items(memory: Path, source_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw_name in ["github-facets.json", "gitlab-facets.json"]:
        raw_path = memory / "raw" / raw_name
        if raw_path.exists():
            out.extend(facet_items(load_json(raw_path, []), source_type))
    return out


def baseline_items(memory: Path, resource_name: str, source_type: str) -> list[dict[str, Any]]:
    resource = resource_items(memory, resource_name)
    raw_by_no = by_number(raw_provider_items(memory, source_type))
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in resource:
        number = item_number(item)
        if number is None:
            continue
        merged = dict(item)
        merged.update(raw_by_no.get(number, {}))
        if number in raw_by_no:
            merged["source"] = "resource_with_raw_provider_facet"
        out.append(merged)
        seen.add(number)
    for number, item in sorted(raw_by_no.items()):
        if number not in seen:
            out.append(item)
    return out


def item_number(item: dict[str, Any]) -> int | None:
    number = item.get("number")
    return number if isinstance(number, int) else None


def by_number(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for item in items:
        number = item_number(item)
        if number is not None:
            out[number] = item
    return out


def comparable_item(item: dict[str, Any]) -> str:
    ignored_keys = {"raw_lookup", "source", "updated_at", "merged_at", "closed_at"}
    normalized = {key: value for key, value in item.items() if key not in ignored_keys}
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def changed_numbers(existing: dict[int, dict[str, Any]], current: dict[int, dict[str, Any]]) -> list[int]:
    changed: list[int] = []
    for number, item in current.items():
        old = existing.get(number)
        if old is None:
            continue
        for key in ["updated_at", "state", "title", "head_ref", "base_ref", "merged_at", "closed_at"]:
            if old.get(key) not in (None, "") and old.get(key) != item.get(key):
                changed.append(number)
                break
        else:
            common_keys = {
                key
                for key, value in old.items()
                if key not in {"number", "raw_lookup", "source"} and value not in (None, "")
            }
            old_common = {key: old.get(key) for key in common_keys}
            current_common = {key: item.get(key) for key in common_keys}
            if old_common != current_common and comparable_item(old) != comparable_item(item):
                changed.append(number)
    return sorted(changed)


def remote_url_from_line(line: str) -> str:
    parts = line.split()
    return parts[1] if len(parts) >= 2 else ""


def remote_name_from_line(line: str) -> str:
    parts = line.split()
    return parts[0] if parts else ""


def normalize_remote_path(path: str) -> str:
    repo_path = path.strip().lstrip("/").rstrip("/")
    return repo_path[:-4] if repo_path.endswith(".git") else repo_path


def provider_from_host(host: str) -> str:
    lower = host.lower()
    if lower == "github.com" or "github" in lower:
        return "github"
    if lower == "gitlab.com" or "gitlab" in lower:
        return "gitlab"
    return ""


def parse_remote_url(url: str) -> dict[str, str]:
    if not url:
        return {"provider": "", "repo": "", "host": "", "url": ""}
    if "://" not in url:
        match = re.match(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$", url)
        if match:
            host = match.group("host")
            return {
                "provider": provider_from_host(host),
                "repo": normalize_remote_path(match.group("path")),
                "host": host,
                "url": url,
            }
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return {
        "provider": provider_from_host(host),
        "repo": normalize_remote_path(parsed.path),
        "host": host,
        "url": url,
    }


def parse_code_host_repo(remotes: str) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    for line in remotes.splitlines():
        if "(push)" in line:
            continue
        parsed = parse_remote_url(remote_url_from_line(line))
        if parsed["provider"] and parsed["repo"]:
            parsed["remote_name"] = remote_name_from_line(line)
            candidates.append(parsed)
    for preferred in PREFERRED_REMOTE_NAMES:
        for candidate in candidates:
            if candidate.get("remote_name") == preferred:
                candidate["selection_reason"] = f"preferred_{preferred}_fetch_remote"
                return candidate
    if candidates:
        candidates[0]["selection_reason"] = "first_supported_fetch_remote"
        return candidates[0]
    return {"provider": "", "repo": "", "host": "", "url": "", "remote_name": "", "selection_reason": "none"}


def provider_host(provider: str) -> str:
    if provider == "github":
        return "github.com"
    if provider == "gitlab":
        return "gitlab.com"
    return ""


def provider_from_profile(memory: Path) -> dict[str, str]:
    profile = parse_frontmatter(memory / "PROFILE.md")
    provider = profile.get("code_host_provider", "")
    repo = profile.get("repo_full_name", "")
    if provider in {"github", "gitlab"} and repo and "/" in repo:
        parsed_profile_url = parse_remote_url(str(profile.get("repo_url", "")))
        host = parsed_profile_url["host"] if parsed_profile_url["provider"] == provider else provider_host(provider)
        return {
            "provider": provider,
            "repo": repo,
            "host": host,
            "url": profile.get("repo_url", ""),
            "remote_name": "",
            "selection_reason": "profile_frontmatter",
        }
    return {}


def auth_args(command: str, host: str) -> list[str]:
    args = ["auth", "status"]
    if host and command in {"gh", "glab"}:
        args.extend(["--hostname", host])
    return args


def login_hint(command: str, host: str) -> str:
    default_host = "github.com" if command == "gh" else "gitlab.com" if command == "glab" else ""
    if host and host != default_host:
        return f"{command} auth login --hostname {host}"
    return f"{command} auth login"


def cli_evidence_state(command: str, host: str) -> tuple[str, bool]:
    if shutil.which(command) is None:
        return "cli_missing", False
    try:
        result = subprocess.run([command, *auth_args(command, host)], text=True, capture_output=True, timeout=8)
    except subprocess.TimeoutExpired:
        return "auth_check_timeout", True
    return ("ready", True) if result.returncode == 0 else ("auth_required", True)


def provider_report(repo: Path, memory: Path, *, inspect_cli: bool = True) -> dict[str, Any]:
    remote = provider_from_profile(memory) or parse_code_host_repo(git(repo, ["remote", "-v"], "git could not list remotes"))
    provider = remote["provider"]
    cli = "gh" if provider == "github" else "glab" if provider == "gitlab" else ""
    evidence_state = "unavailable" if inspect_cli else "skipped_by_policy"
    cli_available = False
    if cli and inspect_cli:
        evidence_state, cli_available = cli_evidence_state(cli, remote["host"])
    return {
        "name": provider,
        "repo": remote["repo"],
        "host": remote["host"],
        "remote_url": remote["url"],
        "remote_name": remote.get("remote_name", ""),
        "selection_reason": remote.get("selection_reason", ""),
        "evidence_state": evidence_state,
        "cli": cli,
        "cli_available": cli_available,
        "login_hint": login_hint(cli, remote["host"]) if cli else "",
    }


def provider_script(provider: str) -> str:
    if provider == "github":
        return "github_resource_facets.py"
    if provider == "gitlab":
        return "gitlab_resource_facets.py"
    return ""


def fetch_provider_items(repo: Path, provider: dict[str, Any], snapshot_ref: str, pr_limit: int, issue_limit: int, summary_chars: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    provider_name = str(provider.get("name") or "")
    provider_repo = str(provider.get("repo") or "")
    script_name = provider_script(provider_name)
    if not script_name or not provider_repo or provider.get("evidence_state") != "ready":
        return {
            "attempted": False,
            "reason": f"provider_evidence_state={provider.get('evidence_state', '') or 'unavailable'}",
        }, [], []

    script_path = BUILDER_SCRIPTS_DIR / script_name
    if not script_path.exists():
        return {"attempted": False, "reason": "repo-wiki repo-build helpers are missing"}, [], []

    with tempfile.TemporaryDirectory(prefix="repo-wiki-repo-update.") as tmp:
        tmp_root = Path(tmp)
        raw = tmp_root / f"{provider_name}-facets.json"
        facets = run(
            [
                sys.executable,
                str(script_path),
                "--repo",
                provider_repo,
                "--repo-path",
                str(repo),
                "--snapshot-ref",
                snapshot_ref,
                "--include",
                "prs,issues",
                "--pr-limit",
                str(pr_limit),
                "--issue-limit",
                str(issue_limit),
                "--state",
                "all",
                "--summary-chars",
                str(summary_chars),
                "--out",
                str(raw),
                *(["--hostname", str(provider.get("host"))] if provider.get("host") else []),
            ],
            repo,
        )
        if facets.returncode != 0:
            return {
                "attempted": True,
                "ok": False,
                "exit_code": facets.returncode,
                "stderr": facets.stderr.strip(),
            }, [], []
        raw_facets = load_json(raw, [])
        prs = facet_items(raw_facets, "pr")
        issues = facet_items(raw_facets, "issue")
        return {
            "attempted": True,
            "ok": True,
            "provider": provider_name,
            "repo": provider_repo,
            "pr_count": len(prs) if isinstance(prs, list) else 0,
            "issue_count": len(issues) if isinstance(issues, list) else 0,
        }, prs, issues


def delta_summary(existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, Any]:
    existing_by_no = by_number(existing)
    current_by_no = by_number(current)
    added = sorted(number for number in current_by_no if number not in existing_by_no)
    updated = changed_numbers(existing_by_no, current_by_no)
    baseline_only = sorted(number for number in existing_by_no if number not in current_by_no)
    return {
        "baseline_numbers": sorted(existing_by_no),
        "current_numbers": sorted(current_by_no),
        "added_numbers": added,
        "updated_numbers": updated,
        "upsert_numbers": sorted([*added, *updated]),
        "baseline_only_numbers": baseline_only,
        "delete_numbers": [],
    }


def actions_for(deltas: dict[str, Any], provider_fetch: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    local_commit_status = deltas.get("local_commit_status", {})
    if local_commit_status.get("reason") == "missing_baseline_commit":
        actions.append("Local commit delta was skipped because no stored commit baseline was found. Use $repo-wiki repo-build for a full rebuild before doing incremental commit updates.")
    if local_commit_status.get("reason") == "baseline_not_ancestor_of_head":
        actions.append("Local commit delta was skipped because the stored baseline is not an ancestor of HEAD. Use $repo-wiki repo-build for a full rebuild, or explicitly choose a new baseline before doing incremental commit updates.")
    if deltas["local_commits"]:
        actions.append("Update .repo_memory/resources/commits.md with sections for the new local commits; update PROFILE.md local_head and commit snapshot notes only where needed.")
    if deltas["pull_requests"]["added_numbers"] or deltas["pull_requests"]["updated_numbers"]:
        actions.append("Upsert .repo_memory/resources/prs.md sections by PR/MR number for only the added or changed numbers. Preserve baseline-only and unaffected PR/MR sections; do not delete missing numbers by default.")
    if deltas["issues"]["added_numbers"] or deltas["issues"]["updated_numbers"]:
        actions.append("Upsert .repo_memory/resources/issues.md sections by issue number for only the added or changed numbers. Preserve baseline-only and unaffected issue sections; do not delete missing numbers by default.")
    if provider_fetch.get("attempted") is True and provider_fetch.get("ok") is False:
        actions.append("Provider delta fetch failed; keeping existing PR/issue resources unchanged. Fix provider access and rerun $repo-wiki repo-update, or use repo-build for a full provider refresh if needed.")
    if provider_fetch.get("attempted") is False:
        actions.append("Provider delta fetch was skipped; keep PR/issue resources unchanged unless the user asks to login/install provider CLI or force provider refresh.")
    if not actions:
        actions.append("No repo-memory content update is needed.")
    return actions


def notices_for(deltas: dict[str, Any], provider_fetch: dict[str, Any]) -> list[dict[str, Any]]:
    notices: list[dict[str, Any]] = []
    local_commit_status = deltas.get("local_commit_status", {})
    if local_commit_status.get("reason") == "missing_baseline_commit":
        notices.append({
            "level": "warning",
            "title": "Repo Memory Commit Baseline Missing",
            "message": "No stored local commit baseline was found in .repo_memory, so incremental commit detection was skipped.",
            "next_steps": [
                "Run $repo-wiki repo-build for a full rebuild before incremental commit updates.",
            ],
            "render_as": "assistant_message",
        })
    if local_commit_status.get("reason") == "baseline_not_ancestor_of_head":
        notices.append({
            "level": "warning",
            "title": "Repo Memory Baseline Rewritten",
            "message": "Stored repo-memory commit baseline is not an ancestor of the current HEAD. History was probably rebased or force-pushed, so incremental commit detection was skipped.",
            "next_steps": [
                "Run $repo-wiki repo-build for a full rebuild, or explicitly choose a new baseline before incremental commit updates.",
            ],
            "render_as": "assistant_message",
        })
    if provider_fetch.get("attempted") is True and provider_fetch.get("ok") is False:
        notices.append({
            "level": "warning",
            "title": "Provider Delta Fetch Failed",
            "message": "Provider delta fetch failed; keeping existing PR/issue resources unchanged. Fix provider access before rerunning provider deltas.",
            "next_steps": [
                "Fix GitHub/GitLab CLI access and rerun $repo-wiki repo-update.",
                "Use $repo-wiki repo-build for a full provider refresh if a broader rebuild is needed.",
            ],
            "render_as": "assistant_message",
        })
    return notices


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect incremental updates for an existing .repo_memory bundle.")
    parser.add_argument("--repo-path", default=".", help="Local git repository path")
    parser.add_argument("--memory-path", help="Existing .repo_memory path; defaults to <repo>/.repo_memory")
    parser.add_argument("--snapshot-ref", default="HEAD", help="Local git ref used for current detection")
    parser.add_argument("--history-mode", choices=sorted(HISTORY_MODES), default=None, help="History evidence policy for this update")
    parser.add_argument("--provider-mode", choices=["auto", "off"], default="auto", help="Whether to fetch latest provider PR/issue facets when provider evidence is ready")
    parser.add_argument("--pr-limit", type=int, default=None, help="Maximum latest PR/MR candidates to compare")
    parser.add_argument("--issue-limit", type=int, default=None, help="Maximum latest issue candidates to compare")
    parser.add_argument("--summary-chars", type=int, default=None, help="Maximum raw provider summary characters when provider facets are fetched")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)
    args.repo_path = Path(args.repo_path).expanduser().resolve()
    return apply_effective_settings(args, parser)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo = args.repo_path
    memory = resolve_memory(repo, args.memory_path)
    if not memory.exists() or not (memory / "PROFILE.md").exists():
        report = {
            "ok": False,
            "repo_path": str(repo),
            "memory_path": str(memory),
            "builder_helpers": builder_helper_report(),
            "error": "Existing .repo_memory/PROFILE.md is required; run $repo-wiki repo-build before repo-update.",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1

    head = git(repo, ["rev-parse", "--verify", f"{args.snapshot_ref}^{{commit}}"], f"git could not resolve {args.snapshot_ref!r}")
    baseline_sha = commit_baseline(memory, repo, head)
    if args.history_collect["commits"]:
        local_commits, local_commit_status = commit_delta(repo, baseline_sha, head)
    else:
        local_commits, local_commit_status = [], {
            "status": "skipped",
            "reason": "history_disabled_by_policy",
            "message": "Local commit delta detection is disabled by repoHistory.mode.",
        }

    existing_prs = baseline_items(memory, "prs.md", "pr")
    existing_issues = baseline_items(memory, "issues.md", "issue")
    provider = provider_report(repo, memory, inspect_cli=args.history_collect["provider"])
    if args.history_collect["provider"]:
        provider_fetch, current_prs, current_issues = fetch_provider_items(
            repo,
            provider,
            args.snapshot_ref,
            args.pr_limit,
            args.issue_limit,
            args.summary_chars,
        )
    else:
        reason = (
            "provider-mode=off"
            if args.provider_mode == "off"
            else "history_disabled_by_policy"
            if args.history_mode == "none"
            else "history_provider_disabled_by_policy"
        )
        provider_fetch, current_prs, current_issues = {"attempted": False, "reason": reason}, [], []
    fetched_prs = current_prs if provider_fetch.get("ok") is True else []
    fetched_issues = current_issues if provider_fetch.get("ok") is True else []
    if not current_prs and provider_fetch.get("ok") is not True:
        current_prs = existing_prs
    if not current_issues and provider_fetch.get("ok") is not True:
        current_issues = existing_issues

    deltas = {
        "local_commits": local_commits,
        "local_commit_status": local_commit_status,
        "pull_requests": delta_summary(existing_prs, current_prs),
        "issues": delta_summary(existing_issues, current_issues),
    }
    if local_commit_status.get("status") == "skipped" and local_commit_status.get("reason"):
        deltas["commit_delta_skipped"] = local_commit_status["reason"]
    report = {
        "ok": True,
        "repo_path": str(repo),
        "memory_path": str(memory),
        "effective_settings": args.effective_settings,
        "builder_helpers": builder_helper_report(),
        "baseline": {
            "local_commit_sha": baseline_sha,
            "pull_request_numbers": deltas["pull_requests"]["baseline_numbers"],
            "issue_numbers": deltas["issues"]["baseline_numbers"],
        },
        "current": {
            "local_head": head,
            "provider": provider,
            "provider_fetch": provider_fetch,
            "provider_items": {
                "pull_requests": fetched_prs,
                "issues": fetched_issues,
            },
        },
        "deltas": deltas,
        "notices": notices_for(deltas, provider_fetch),
        "actions": actions_for(deltas, provider_fetch),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
