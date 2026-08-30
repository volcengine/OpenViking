#!/usr/bin/env python3
"""Prepare an empty .repo_memory workspace; do not generate memory content."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_MEMORY_GITIGNORE_RULE = ".repo_memory/"
PREFERRED_REMOTE_NAMES = ("upstream", "origin")


def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_git_repo(repo: Path) -> bool:
    code, out, _ = run(["git", "rev-parse", "--is-inside-work-tree"], repo)
    return code == 0 and out == "true"


def git_value(repo: Path, *args: str) -> str:
    code, out, _ = run(["git", *args], repo)
    return out if code == 0 else ""


def remote_url_from_line(line: str) -> str:
    parts = line.split()
    return parts[1] if len(parts) >= 2 else ""


def remote_name_from_line(line: str) -> str:
    parts = line.split()
    return parts[0] if parts else ""


def normalize_remote_path(path: str) -> str:
    repo_path = path.strip().lstrip("/").rstrip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    return repo_path


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
        scp_match = re.match(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$", url)
        if scp_match:
            host = scp_match.group("host")
            repo_path = normalize_remote_path(scp_match.group("path"))
            return {"provider": provider_from_host(host), "repo": repo_path, "host": host, "url": url}

    parsed = urlparse(url)
    host = parsed.hostname or ""
    repo_path = normalize_remote_path(parsed.path)
    return {"provider": provider_from_host(host), "repo": repo_path, "host": host, "url": url}


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


def gitignore_has_repo_memory_rule(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        rule = stripped.split("#", 1)[0].strip().lstrip("/")
        if rule in {".repo_memory", ".repo_memory/"}:
            return True
    return False


def ensure_repo_memory_gitignore(repo: Path) -> bool:
    gitignore = repo / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if gitignore_has_repo_memory_rule(existing):
        return False
    separator = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{separator}{REPO_MEMORY_GITIGNORE_RULE}\n", encoding="utf-8")
    return True


def cli_auth_state(command: str, auth_args: list[str], login_hint: str) -> dict[str, Any]:
    if shutil.which(command) is None:
        return {
            "available": False,
            "authenticated": False,
            "auth_status": "cli_missing",
            "auth_error": "",
            "login_hint": login_hint,
        }
    try:
        proc = subprocess.run([command, *auth_args], text=True, capture_output=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "authenticated": False,
            "auth_status": "auth_check_timeout",
            "auth_error": f"{command} auth check timed out",
            "login_hint": login_hint,
        }
    if proc.returncode == 0:
        return {
            "available": True,
            "authenticated": True,
            "auth_status": "authenticated",
            "auth_error": "",
            "login_hint": login_hint,
        }
    error = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
    return {
        "available": True,
        "authenticated": False,
        "auth_status": "auth_required",
        "auth_error": error[0][:500] if error else "",
        "login_hint": login_hint,
    }


def provider_label(provider: str) -> str:
    if provider == "github":
        return "GitHub"
    if provider == "gitlab":
        return "GitLab"
    return "Git provider"


def provider_evidence_label(provider: str) -> str:
    if provider == "github":
        return "GitHub PR/issue"
    if provider == "gitlab":
        return "GitLab MR/issue"
    return "provider"


def notice_markdown(body: str, command: str, rerun_line: str) -> str:
    command_block = f"\n```bash\n{command}\n```\n" if command else ""
    return "\n".join(
        [
            "**Provider Evidence Unavailable**",
            "",
            f"> {body}",
            "> Continuing with local-only repo memory now.",
            "",
            "**Next steps for provider evidence**",
            command_block.rstrip(),
            rerun_line,
        ]
    ).strip()


def non_git_repo_notice(repo: Path) -> str:
    return "\n".join(
        [
            "**Repo Memory Cannot Be Built Yet**",
            "",
            f"> This folder is not a git repository: {repo}",
            "> The $repo-wiki repo-build operation cannot collect local commit history or provider-linked PR/MR/issue evidence.",
            "",
            "**Next steps**",
            "- If this is an existing project, open the real cloned repo directory.",
            "- If this is a new project, run `git init` first, make at least one commit, then rerun $repo-wiki repo-build.",
            "- If you only want file inspection, continue by inspecting files without repo memory.",
        ]
    )


def provider_notice(provider: str, cli: str, evidence_state: str, login_hint: str) -> dict[str, Any]:
    label = provider_label(provider)
    evidence_label = provider_evidence_label(provider)
    if evidence_state == "ready":
        return {
            "provider_notice_level": "info",
            "provider_user_notice": f"{label} provider evidence is ready; {evidence_label} evidence can be collected.",
            "provider_notice_markdown": (
                f"**Provider Evidence Ready**\n\n"
                f"> {label} provider evidence is ready.\n\n"
                f"`{evidence_label}` evidence can be collected."
            ),
            "provider_next_steps": [],
        }
    if evidence_state == "auth_required":
        body = f"{label} provider evidence is unavailable because `{cli}` is not logged in."
        rerun_line = f"Rerun $repo-wiki repo-build to collect {evidence_label} evidence."
        return {
            "provider_notice_level": "warning",
            "provider_user_notice": (
                f"{body} "
                f"I am continuing with local-only repo memory now. To include {evidence_label} evidence, "
                f"run `{login_hint}`, then rerun $repo-wiki repo-build."
            ),
            "provider_notice_markdown": notice_markdown(body, login_hint, rerun_line),
            "provider_next_steps": [
                f"Run: {login_hint}",
                rerun_line,
            ],
        }
    if evidence_state == "cli_missing":
        body = f"{label} provider evidence is unavailable because `{cli}` is not installed."
        rerun_line = f"Rerun $repo-wiki repo-build to collect {evidence_label} evidence."
        return {
            "provider_notice_level": "warning",
            "provider_user_notice": (
                f"{body} "
                f"I am continuing with local-only repo memory now. To include {evidence_label} evidence, "
                f"install `{cli}`, run `{login_hint}`, then rerun $repo-wiki repo-build."
            ),
            "provider_notice_markdown": notice_markdown(body, login_hint, rerun_line),
            "provider_next_steps": [
                f"Install: {cli}",
                f"Run: {login_hint}",
                rerun_line,
            ],
        }
    return {
        "provider_notice_level": "info",
        "provider_user_notice": "No supported GitHub/GitLab remote was detected; continuing with local-only repo memory.",
        "provider_notice_markdown": (
            "**Provider Evidence Unavailable**\n\n"
            "> No supported GitHub/GitLab remote was detected.\n"
            "> Continuing with local-only repo memory."
        ),
        "provider_next_steps": [],
    }


def provider_cli_report(provider: str, gh: dict[str, Any], glab: dict[str, Any]) -> dict[str, Any]:
    if provider == "github":
        state = gh
        cli = "gh"
    elif provider == "gitlab":
        state = glab
        cli = "glab"
    else:
        return {
            "provider_cli": "",
            "provider_cli_available": False,
            "provider_authenticated": False,
            "provider_auth_status": "no_supported_remote",
            "provider_evidence_state": "unavailable",
            "provider_login_hint": "",
            "provider_fallback": "No supported GitHub/GitLab remote was detected; build local-only repo memory.",
            **provider_notice(provider, "", "unavailable", ""),
        }

    if not state["available"]:
        evidence_state = "cli_missing"
        fallback = f"{cli} is not installed; build local-only repo memory unless the user installs the provider CLI."
    elif not state["authenticated"]:
        evidence_state = "auth_required"
        fallback = f"{cli} is not authenticated; build local-only repo memory unless the user logs in and reruns provider collection."
    else:
        evidence_state = "ready"
        fallback = "Provider evidence can be collected."

    return {
        "provider_cli": cli,
        "provider_cli_available": state["available"],
        "provider_authenticated": state["authenticated"],
        "provider_auth_status": state["auth_status"],
        "provider_evidence_state": evidence_state,
        "provider_login_hint": state["login_hint"],
        "provider_fallback": fallback,
        **provider_notice(provider, cli, evidence_state, state["login_hint"]),
    }


def skipped_provider_report(provider: str, host: str) -> dict[str, Any]:
    cli = "gh" if provider == "github" else "glab" if provider == "gitlab" else ""
    return {
        "provider_cli": cli,
        "provider_cli_available": False,
        "provider_authenticated": False,
        "provider_auth_status": "skipped_by_policy",
        "provider_evidence_state": "skipped_by_policy",
        "provider_login_hint": login_hint(cli, host) if cli else "",
        "provider_fallback": "Provider collection is disabled by the selected history policy.",
        "provider_notice_level": "info",
        "provider_user_notice": "Provider evidence collection was skipped by policy.",
        "provider_notice_markdown": "",
        "provider_next_steps": [],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check environment and create .repo_memory directories only.")
    parser.add_argument("repo", nargs="?", default=".", help="Local git repository path; defaults to current directory")
    parser.add_argument("--reuse", action="store_true", help="Allow using an existing .repo_memory directory without deleting it")
    parser.add_argument("--skip-provider-check", action="store_true", help="Do not inspect gh/glab availability or authentication")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.exists():
        raise SystemExit(f"Repository path does not exist: {repo}")
    if shutil.which("git") is None:
        raise SystemExit("git is required but was not found on PATH")
    if not is_git_repo(repo):
        raise SystemExit(non_git_repo_notice(repo))

    memory = repo / ".repo_memory"
    if memory.exists() and not args.reuse:
        raise SystemExit(f"{memory} already exists. Stop by default; rerun with --reuse to update it.")

    for subdir in [memory, memory / "raw", memory / "resources"]:
        subdir.mkdir(parents=True, exist_ok=True)

    remotes = git_value(repo, "remote", "-v")
    remote = parse_code_host_repo(remotes)
    gitignore_updated = ensure_repo_memory_gitignore(repo)
    gh_host = remote["host"] if remote["provider"] == "github" else ""
    glab_host = remote["host"] if remote["provider"] == "gitlab" else ""
    if args.skip_provider_check:
        gh_state = {"available": False, "authenticated": False, "auth_status": "skipped_by_policy", "auth_error": "", "login_hint": login_hint("gh", gh_host)}
        glab_state = {"available": False, "authenticated": False, "auth_status": "skipped_by_policy", "auth_error": "", "login_hint": login_hint("glab", glab_host)}
        provider_report = skipped_provider_report(remote["provider"], remote["host"])
    else:
        gh_state = cli_auth_state("gh", auth_args("gh", gh_host), login_hint("gh", gh_host))
        glab_state = cli_auth_state("glab", auth_args("glab", glab_host), login_hint("glab", glab_host))
        provider_report = provider_cli_report(remote["provider"], gh_state, glab_state)
    report: dict[str, Any] = {
        "prepared_at": now_iso(),
        "repo_path": str(repo),
        "memory_path": str(memory),
        "is_git_repo": True,
        "local_head": git_value(repo, "rev-parse", "HEAD"),
        "local_branch": git_value(repo, "branch", "--show-current") or "(detached HEAD)",
        "working_tree_state": "dirty" if git_value(repo, "status", "--short") else "clean",
        "git_remotes": remotes.splitlines(),
        "git_provider": remote["provider"],
        "git_remote_repo": remote["repo"],
        "git_remote_host": remote["host"],
        "git_remote_url": remote["url"],
        "git_remote_name": remote.get("remote_name", ""),
        "git_remote_selection_reason": remote.get("selection_reason", ""),
        "github_repo": remote["repo"] if remote["provider"] == "github" else "",
        "gitlab_repo": remote["repo"] if remote["provider"] == "gitlab" else "",
        "gh_available": gh_state["available"],
        "gh_authenticated": gh_state["authenticated"],
        "gh_auth_status": gh_state["auth_status"],
        "gh_auth_error": gh_state["auth_error"],
        "gh_login_hint": gh_state["login_hint"],
        "glab_available": glab_state["available"],
        "glab_authenticated": glab_state["authenticated"],
        "glab_auth_status": glab_state["auth_status"],
        "glab_auth_error": glab_state["auth_error"],
        "glab_login_hint": glab_state["login_hint"],
        **provider_report,
        "gitignore_path": str(repo / ".gitignore"),
        "gitignore_rule": REPO_MEMORY_GITIGNORE_RULE,
        "gitignore_updated": gitignore_updated,
        "created_directories": [".repo_memory", ".repo_memory/raw", ".repo_memory/resources"],
        "next_step": "Agent must inspect local code/docs and author PROFILE.md and resources. This script intentionally does not generate memory content.",
    }
    (memory / "raw" / "prepare-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
