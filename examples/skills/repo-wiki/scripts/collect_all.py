#!/usr/bin/env python3
"""Run the mechanical repo-memory collection steps."""

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULTS_PATH = SCRIPT_DIR.parent / "defaults.json"
FALLBACK_DEFAULTS = {
    "repoHistory": {
        "mode": "local-only",
        "limits": {
            "commits": 30,
            "prs": 30,
            "issues": 30,
        },
    },
    # Kept for compatibility with defaults.json v1.
    "limits": {
        "commits": 30,
        "prs": 30,
        "issues": 30,
    },
    "summaryChars": 4000,
}

HISTORY_MODES = {"none", "commits-only", "local-only", "provider", "provider-required"}


class ProgressBar:
    def __init__(self, enabled: bool, total: int) -> None:
        self.enabled = enabled
        self.total = total
        self.width = 20
        self._last_line_len = 0
        self._is_tty = sys.stderr.isatty()

    def update(self, completed: int, label: str) -> None:
        if not self.enabled:
            return
        completed = max(0, min(completed, self.total))
        filled = round((completed / self.total) * self.width) if self.total else self.width
        bar = "#" * filled + "-" * (self.width - filled)
        line = f"repo-wiki repo-build [{bar}] {completed}/{self.total} {label}"
        if self._is_tty:
            padding = " " * max(0, self._last_line_len - len(line))
            print(f"\r{line}{padding}", end="", file=sys.stderr, flush=True)
            self._last_line_len = len(line)
        else:
            print(line, file=sys.stderr, flush=True)

    def fail(self, completed: int, label: str) -> None:
        self.update(completed, label)
        self.finish()

    def finish(self) -> None:
        if self.enabled and self._is_tty:
            print(file=sys.stderr, flush=True)


def run_step(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def json_step(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_source_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    data = load_json(path)
    if not isinstance(data, list):
        return {}
    counts = Counter(str(item.get("sourceType") or "unknown") for item in data if isinstance(item, dict))
    return dict(sorted(counts.items()))


def emit_process_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.stdout:
        print(result.stdout, file=sys.stderr)


def write_report(report: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2 if pretty else None))


def write_failure(
    failed_step: str,
    result: subprocess.CompletedProcess[str],
    pretty: bool,
    *,
    repo: Optional[Path] = None,
    memory: Optional[Path] = None,
    provider: Optional[dict[str, Any]] = None,
    steps: Optional[dict[str, Any]] = None,
    outputs: Optional[dict[str, str]] = None,
    notices: Optional[list[dict[str, Any]]] = None,
    effective_settings: Optional[dict[str, Any]] = None,
) -> int:
    emit_process_output(result)
    step_reports = dict(steps or {})
    step_reports[failed_step] = json_step(result)
    report = {
        "ok": False,
        "failed_step": failed_step,
        "steps": step_reports,
    }
    if repo is not None:
        report["repo_path"] = str(repo)
    if memory is not None:
        report["memory_path"] = str(memory)
    if provider is not None:
        report["provider"] = provider
    if outputs:
        report["outputs"] = outputs
    if notices:
        report["notices"] = notices
    if effective_settings is not None:
        report["effective_settings"] = effective_settings
    write_report(report, pretty)
    return result.returncode or 1


def provider_report(prepare_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": prepare_report.get("git_provider", ""),
        "repo": prepare_report.get("git_remote_repo", ""),
        "host": prepare_report.get("git_remote_host", ""),
        "remote_name": prepare_report.get("git_remote_name", ""),
        "selection_reason": prepare_report.get("git_remote_selection_reason", ""),
        "cli": prepare_report.get("provider_cli", ""),
        "cli_available": bool(prepare_report.get("provider_cli_available")),
        "authenticated": bool(prepare_report.get("provider_authenticated")),
        "auth_status": prepare_report.get("provider_auth_status", ""),
        "evidence_state": prepare_report.get("provider_evidence_state", ""),
        "login_hint": prepare_report.get("provider_login_hint", ""),
        "notice_level": prepare_report.get("provider_notice_level", ""),
        "user_notice": prepare_report.get("provider_user_notice", ""),
        "notice_markdown": prepare_report.get("provider_notice_markdown", ""),
        "next_steps": prepare_report.get("provider_next_steps", []),
    }


def provider_notices(prepare_report: dict[str, Any]) -> list[dict[str, Any]]:
    if prepare_report.get("provider_notice_level") != "warning":
        return []
    message = str(prepare_report.get("provider_user_notice", "")).replace("`", "")
    return [
        {
            "level": "warning",
            "title": "Provider Evidence Unavailable",
            "message": message,
            "command": prepare_report.get("provider_login_hint", ""),
            "next_steps": prepare_report.get("provider_next_steps", []),
            "render_as": "assistant_message",
        }
    ]


def provider_failure_notice(provider: dict[str, Any], result: subprocess.CompletedProcess[str], *, continuing: bool) -> dict[str, Any]:
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    detail = (stderr or stdout or "provider facet collection failed").replace("`", "")
    if len(detail) > 800:
        detail = f"{detail[:800]}..."
    label = str(provider.get("name") or "provider")
    evidence = "GitHub PR/issue" if label == "github" else "GitLab MR/issue" if label == "gitlab" else "provider"
    continuation = (
        "Continuing with local-only repo memory now."
        if continuing
        else "Provider evidence was required, so the run stopped before authoring provider resources."
    )
    return {
        "level": "warning",
        "title": "Provider Evidence Unavailable",
        "message": (
            f"{evidence} evidence could not be collected even though the provider CLI appeared ready. "
            f"{continuation} Provider error: {detail}"
        ),
        "command": "",
        "next_steps": [
            "Fix provider repository access or remote URL.",
            f"Rerun $repo-wiki repo-build to collect {evidence} evidence.",
        ],
        "render_as": "assistant_message",
    }


def provider_script(provider: str) -> str:
    if provider == "github":
        return "github_resource_facets.py"
    if provider == "gitlab":
        return "gitlab_resource_facets.py"
    return ""


def provider_raw_name(provider: str) -> str:
    if provider == "github":
        return "github-facets.json"
    if provider == "gitlab":
        return "gitlab-facets.json"
    return "provider-facets.json"


def load_default_settings() -> tuple[dict[str, Any], str]:
    if not DEFAULTS_PATH.exists():
        return FALLBACK_DEFAULTS, "hardcoded_fallback"
    try:
        data = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{DEFAULTS_PATH}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{DEFAULTS_PATH}: expected a JSON object")
    return data, str(DEFAULTS_PATH)


def default_int(settings: dict[str, Any], path: list[str], fallback: int) -> int:
    value: Any = settings
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{DEFAULTS_PATH}: {'.'.join(path)} must be an integer")
    return value


def validate_range(parser: argparse.ArgumentParser, name: str, value: int, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        parser.error(f"{name} must be from {minimum} to {maximum}")


def default_history_mode(settings: dict[str, Any]) -> str:
    history = settings.get("repoHistory")
    if not isinstance(history, dict):
        return "local-only"
    mode = history.get("mode", "local-only")
    if not isinstance(mode, str) or mode not in HISTORY_MODES:
        raise ValueError(f"{DEFAULTS_PATH}: repoHistory.mode must be one of {', '.join(sorted(HISTORY_MODES))}")
    return mode


def default_limit(settings: dict[str, Any], key: str) -> int:
    history = settings.get("repoHistory")
    if isinstance(history, dict) and isinstance(history.get("limits"), dict) and history["limits"].get(key) is not None:
        return default_int(settings, ["repoHistory", "limits", key], FALLBACK_DEFAULTS["repoHistory"]["limits"][key])
    return default_int(settings, ["limits", key], FALLBACK_DEFAULTS["limits"][key])


def history_collect(mode: str) -> dict[str, bool]:
    return {
        "commits": mode != "none",
        "provider": mode in {"provider", "provider-required"},
    }


def resolve_history_mode(args: argparse.Namespace, default_mode: str) -> str:
    mode = args.history_mode or default_mode
    if args.skip_provider:
        mode = "local-only"
    if args.require_provider:
        mode = "provider-required"
    return mode


def apply_effective_settings(args: argparse.Namespace, parser: argparse.ArgumentParser) -> argparse.Namespace:
    try:
        defaults, source = load_default_settings()
        default_mode = default_history_mode(defaults)
        default_commit_limit = default_limit(defaults, "commits")
        default_pr_limit = default_limit(defaults, "prs")
        default_issue_limit = default_limit(defaults, "issues")
        default_summary_chars = default_int(defaults, ["summaryChars"], FALLBACK_DEFAULTS["summaryChars"])
    except ValueError as exc:
        parser.error(str(exc))

    history_mode = resolve_history_mode(args, default_mode)
    collect = history_collect(history_mode)

    commit_limit = args.commit_limit if args.commit_limit is not None else default_commit_limit
    pr_limit = args.pr_limit if args.pr_limit is not None else default_pr_limit
    issue_limit = args.issue_limit if args.issue_limit is not None else default_issue_limit
    summary_chars = args.summary_chars if args.summary_chars is not None else default_summary_chars

    validate_range(parser, "--commit-limit", commit_limit, 1, 500)
    validate_range(parser, "--pr-limit", pr_limit, 1, 100)
    validate_range(parser, "--issue-limit", issue_limit, 1, 100)
    if summary_chars < 100:
        parser.error("--summary-chars must be at least 100")

    overrides: dict[str, Any] = {}
    if args.history_mode is not None:
        overrides["history_mode"] = args.history_mode
    if args.skip_provider:
        overrides["skip_provider"] = True
    if args.require_provider:
        overrides["require_provider"] = True
    if args.commit_limit is not None:
        overrides["commit_limit"] = args.commit_limit
    if args.pr_limit is not None:
        overrides["pr_limit"] = args.pr_limit
    if args.issue_limit is not None:
        overrides["issue_limit"] = args.issue_limit
    if args.summary_chars is not None:
        overrides["summary_chars"] = args.summary_chars

    args.commit_limit = commit_limit
    args.pr_limit = pr_limit
    args.issue_limit = issue_limit
    args.summary_chars = summary_chars
    args.history_mode = history_mode
    args.history_collect = collect
    args.effective_settings = {
        "history": {
            "mode": history_mode,
            "collect": collect,
        },
        "limits": {
            "commits": commit_limit,
            "prs": pr_limit,
            "issues": issue_limit,
        },
        "summary_chars": summary_chars,
        "source": source,
        "overrides": overrides,
    }
    return args


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare repo memory and collect raw facets for agent-authored resources.")
    parser.add_argument("--repo-path", default=".", help="Local git repository path")
    parser.add_argument("--reuse", action="store_true", help="Reuse/update an existing .repo_memory directory")
    parser.add_argument("--snapshot-ref", default="HEAD", help="Local git ref whose history should be collected")
    parser.add_argument("--commit-limit", type=int, default=None, help="Maximum local commits retained from the snapshot history")
    parser.add_argument("--pr-limit", type=int, default=None, help="Maximum snapshot-filtered PRs/MRs retained")
    parser.add_argument("--issue-limit", type=int, default=None, help="Maximum provider issues retained")
    parser.add_argument("--summary-chars", type=int, default=None)
    parser.add_argument("--history-mode", choices=sorted(HISTORY_MODES), default=None, help="History evidence policy for this build")
    parser.add_argument("--skip-provider", action="store_true", help="Skip GitHub/GitLab PR/MR/issue collection and build local-only memory")
    parser.add_argument("--require-provider", action="store_true", help="Fail if provider PR/MR/issue evidence cannot be collected")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Render a stderr progress bar while keeping stdout as the final JSON report",
    )
    args = parser.parse_args(argv)
    if args.skip_provider and args.require_provider:
        parser.error("--skip-provider and --require-provider cannot be used together")
    if args.history_mode and args.skip_provider and args.history_mode != "local-only":
        parser.error(f"--skip-provider cannot be combined with --history-mode {args.history_mode}")
    if args.history_mode and args.require_provider and args.history_mode != "provider-required":
        parser.error("--require-provider cannot be combined with a non-required history mode")
    args.repo_path = Path(args.repo_path).expanduser().resolve()
    return apply_effective_settings(args, parser)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo = args.repo_path
    progress = ProgressBar(args.progress, 3)
    command_cwd = repo if repo.exists() else SCRIPT_DIR
    prepare_cmd = [sys.executable, str(SCRIPT_DIR / "prepare_repo_memory.py"), str(repo)]
    if args.reuse:
        prepare_cmd.append("--reuse")
    if not args.history_collect["provider"]:
        prepare_cmd.append("--skip-provider-check")
    progress.update(0, "prepare")
    prepare = run_step(prepare_cmd, command_cwd)
    if prepare.returncode != 0:
        progress.fail(0, "prepare failed")
        return write_failure("prepare", prepare, args.pretty, repo=repo)
    if prepare.stderr:
        print(prepare.stderr, file=sys.stderr)
    progress.update(1, "prepare")

    memory = repo / ".repo_memory"
    raw_dir = memory / "raw"
    prepare_report_path = raw_dir / "prepare-report.json"
    prepare_data = load_json(prepare_report_path)
    notices = provider_notices(prepare_data)

    git_commits_path = raw_dir / "git-commits.json"
    outputs = {
        "prepare_report": str(prepare_report_path),
        "git_commits": str(git_commits_path),
    }
    if args.history_collect["commits"]:
        git_commits = run_step(
            [
                sys.executable,
                str(SCRIPT_DIR / "git_commit_facets.py"),
                "--repo-path",
                str(repo),
                "--snapshot-ref",
                args.snapshot_ref,
                "--limit",
                str(args.commit_limit),
                "--summary-chars",
                str(args.summary_chars),
                "--out",
                str(git_commits_path),
            ],
            repo,
        )
        git_commits_step = json_step(git_commits)
        if git_commits.returncode != 0:
            progress.fail(1, "git commits failed")
            return write_failure(
                "git_commits",
                git_commits,
                args.pretty,
                repo=repo,
                memory=memory,
                steps={"prepare": json_step(prepare)},
                outputs=outputs,
            )
    else:
        git_commits_path.write_text("[]\n", encoding="utf-8")
        git_commits_step = {
            "ok": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "skipped": True,
            "reason": "history_disabled_by_policy",
        }
    progress.update(2, "git commits")

    provider = provider_report(prepare_data)
    provider_name = str(provider["name"])
    provider_facets: dict[str, Any]
    provider_output = raw_dir / provider_raw_name(provider_name)
    provider_script_name = provider_script(provider_name)
    if not args.history_collect["provider"]:
        reason = "provider_skipped_by_user" if args.skip_provider else "history_disabled_by_policy" if args.history_mode == "none" else "history_provider_disabled_by_policy"
        provider_facets = {
            "ok": True,
            "skipped": True,
            "reason": reason,
            "output": "",
        }
    elif provider["evidence_state"] == "ready" and provider_script_name and provider["repo"]:
        provider_cmd = [
            sys.executable,
            str(SCRIPT_DIR / provider_script_name),
            "--repo",
            str(provider["repo"]),
            "--repo-path",
            str(repo),
            "--snapshot-ref",
            args.snapshot_ref,
            "--include",
            "prs,issues",
            "--pr-limit",
            str(args.pr_limit),
            "--issue-limit",
            str(args.issue_limit),
            "--state",
            "all",
            "--summary-chars",
            str(args.summary_chars),
            "--out",
            str(provider_output),
        ]
        if provider.get("host"):
            provider_cmd.extend(["--hostname", str(provider["host"])])
        provider_result = run_step(provider_cmd, repo)
        provider_facets = {
            **json_step(provider_result),
            "skipped": False,
            "output": str(provider_output),
        }
        if provider_result.returncode != 0:
            provider_facets["degraded_to_local_only"] = args.history_mode != "provider-required"
            provider_facets["reason"] = "provider_facets_failed"
            provider_facets["output"] = ""
            if provider_output.exists():
                provider_output.unlink()
            if args.history_mode == "provider-required":
                progress.fail(2, "provider facets failed")
                return write_failure(
                    "provider_facets",
                    provider_result,
                    args.pretty,
                    repo=repo,
                    memory=memory,
                    provider=provider,
                    steps={
                        "prepare": json_step(prepare),
                        "git_commits": git_commits_step,
                    },
                    outputs=outputs,
                    notices=[provider_failure_notice(provider, provider_result, continuing=False)],
                    effective_settings=args.effective_settings,
                )
            notices.append(provider_failure_notice(provider, provider_result, continuing=True))
        else:
            outputs["provider_facets"] = provider_facets["output"]
    else:
        if args.history_mode == "provider-required":
            provider_result = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=f"provider evidence is required but provider_evidence_state={provider['evidence_state']}",
            )
            progress.fail(2, "provider facets unavailable")
            return write_failure(
                "provider_facets",
                provider_result,
                args.pretty,
                repo=repo,
                memory=memory,
                provider=provider,
                steps={
                    "prepare": json_step(prepare),
                    "git_commits": git_commits_step,
                },
                outputs=outputs,
                notices=notices,
                effective_settings=args.effective_settings,
            )
        provider_facets = {
            "ok": True,
            "skipped": True,
            "reason": f"provider_evidence_state={provider['evidence_state']}",
            "output": "",
        }
    progress.update(3, "provider facets")
    progress.finish()

    counts = {
        "raw": {
            "git_commits": raw_source_counts(git_commits_path),
        },
    }
    if provider_facets.get("output"):
        counts["raw"]["provider_facets"] = raw_source_counts(Path(str(provider_facets["output"])))

    report = {
        "ok": True,
        "repo_path": str(repo),
        "memory_path": str(memory),
        "provider": provider,
        "notices": notices,
        "effective_settings": args.effective_settings,
        "steps": {
            "prepare": json_step(prepare),
            "git_commits": git_commits_step,
            "provider_facets": provider_facets,
        },
        "outputs": outputs,
        "counts": counts,
        "next_step": "Inspect raw evidence, then author PROFILE.md and resources/*.md.",
    }
    write_report(report, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
