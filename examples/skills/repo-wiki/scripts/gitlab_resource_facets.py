#!/usr/bin/env python3
"""Extract GitLab merge request/issue resource facets with Python stdlib + glab CLI."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlencode


DEFAULT_GLAB_RETRIES = 3
DEFAULT_GLAB_RETRY_DELAY_MS = 1000
TRANSIENT_GLAB_ERROR = re.compile(
    r"EOF|timeout|timed out|connection reset|connection refused|temporarily unavailable|"
    r"TLS handshake timeout|502|503|504|rate limit|too many requests",
    re.IGNORECASE,
)


def is_transient_glab_error(stderr: str, stdout: str = "") -> bool:
    return TRANSIENT_GLAB_ERROR.search(f"{stderr}\n{stdout}") is not None


def run_glab_api(path: str, hostname: str = "", retries: int = DEFAULT_GLAB_RETRIES, retry_delay_ms: int = DEFAULT_GLAB_RETRY_DELAY_MS) -> Any:
    if shutil.which("glab") is None:
        raise SystemExit("GitLab CLI 'glab' is required. Install it and run: glab auth login")
    args = ["api"]
    if hostname:
        args.extend(["--hostname", hostname])
    args.append(path)
    attempts = max(1, retries + 1)
    last_result: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(["glab", *args], text=True, capture_output=True)
        last_result = result
        if result.returncode == 0:
            text = result.stdout.strip()
            return json.loads(text) if text else None
        if attempt < attempts and is_transient_glab_error(result.stderr, result.stdout):
            delay = max(0, retry_delay_ms) / 1000 * (2 ** (attempt - 1))
            print(
                f"glab api {path} failed with transient error; retrying "
                f"{attempt}/{retries} after {delay:.1f}s: {result.stderr.strip()}",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        break
    assert last_result is not None
    raise SystemExit(f"glab api {path} failed:\n{last_result.stderr.strip()}")


def auth_status_failure(result: subprocess.CompletedProcess[str], login_hint: str) -> str:
    details: list[str] = []
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    if stderr:
        details.append(f"stderr: {stderr}")
    if stdout:
        details.append(f"stdout: {stdout}")
    detail = "\n".join(details) if details else "glab auth status returned no diagnostic output"
    return f"glab auth check failed with exit code {result.returncode}. Run: {login_hint}\n{detail}"


def login_hint(hostname: str) -> str:
    return f"glab auth login --hostname {hostname}" if hostname and hostname != "gitlab.com" else "glab auth login"


def assert_glab_ready(hostname: str) -> None:
    if shutil.which("glab") is None:
        raise SystemExit("GitLab CLI 'glab' is required. Install it and run: glab auth login")
    args = ["auth", "status"]
    if hostname:
        args.extend(["--hostname", hostname])
    attempts = max(1, DEFAULT_GLAB_RETRIES + 1)
    last_result: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(["glab", *args], text=True, capture_output=True)
        last_result = result
        if result.returncode == 0:
            return
        if attempt < attempts and is_transient_glab_error(result.stderr, result.stdout):
            delay = max(0, DEFAULT_GLAB_RETRY_DELAY_MS) / 1000 * (2 ** (attempt - 1))
            print(
                f"glab auth status failed with transient error; retrying "
                f"{attempt}/{DEFAULT_GLAB_RETRIES} after {delay:.1f}s: {result.stderr.strip()}",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        break
    assert last_result is not None
    raise SystemExit(auth_status_failure(last_result, login_hint(hostname)))


def run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise SystemExit("git is required for MR snapshot filtering")
    return subprocess.run(["git", "-C", str(repo_path), *args], text=True, capture_output=True)


def resolve_snapshot_sha(repo_path: Path, snapshot_ref: str) -> str:
    result = run_git(repo_path, ["rev-parse", "--verify", f"{snapshot_ref}^{{commit}}"])
    if result.returncode != 0:
        raise SystemExit(f"git could not resolve snapshot ref {snapshot_ref!r} in {repo_path}:\n{result.stderr.strip()}")
    return result.stdout.strip()


def is_git_ancestor(repo_path: Path, ancestor_sha: str, descendant_sha: str) -> bool:
    result = run_git(repo_path, ["merge-base", "--is-ancestor", ancestor_sha, descendant_sha])
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    stderr = result.stderr.strip()
    normalized_stderr = stderr.lower()
    if (
        "not a valid commit" in normalized_stderr
        or "no such commit" in normalized_stderr
        or "not a valid object name" in normalized_stderr
    ):
        return False
    raise SystemExit(
        f"git could not compare {ancestor_sha} with snapshot {descendant_sha} in {repo_path}:\n{stderr}"
    )


def api_path(repo: str, endpoint: str, params: Optional[dict[str, Any]] = None) -> str:
    encoded_repo = quote(repo, safe="")
    query = urlencode(params or {})
    path = f"projects/{encoded_repo}/{endpoint}"
    return f"{path}?{query}" if query else path


def state_param(state: str) -> str:
    return {"open": "opened", "closed": "closed"}.get(state, state)


def normalize_state(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "opened":
        return "OPEN"
    if raw == "merged":
        return "MERGED"
    if raw == "closed":
        return "CLOSED"
    return raw.upper() if raw else ""


def unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def unique_numbers(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        if isinstance(value, int) and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def text_field(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def int_field(value: Any, key: str) -> Optional[int]:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    if isinstance(item, int):
        return item
    if isinstance(item, str) and item.isdigit():
        return int(item)
    return None


def bounded_summary(parts: Iterable[str], max_chars: int) -> str:
    text = "\n\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def extract_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in re.finditer(r"\b[$A-Z_a-z][$\w]*\b", text):
        value = match.group(0)
        looks_symbolic = (
            re.search(r"[a-z][A-Z]", value) is not None
            or re.match(r"^[A-Z0-9_]{2,}$", value) is not None
            or "_" in value
            or "$" in value
        )
        if looks_symbolic:
            symbols.append(value)
    return unique_strings(symbols)


def linked_issue_numbers(text: str) -> list[int]:
    numbers = [int(match.group(1)) for match in re.finditer(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", text, re.IGNORECASE)]
    return unique_numbers(numbers)


def author_login(value: Any) -> str:
    return text_field(value, "username", "login", "name")


def label_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return unique_strings(text_field(item, "name") if isinstance(item, dict) else str(item).strip() for item in value)


def change_paths(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    changes = value.get("changes")
    if not isinstance(changes, list):
        return []
    paths: list[str] = []
    for item in changes:
        if isinstance(item, dict):
            path = text_field(item, "new_path", "old_path")
            if path:
                paths.append(path)
    return unique_strings(paths)


def mr_to_facet(repo: str, detail: dict[str, Any], changes: dict[str, Any], summary_chars: int) -> dict[str, Any]:
    number = int_field(detail, "iid") or 0
    title = text_field(detail, "title") or f"MR !{number}"
    description = text_field(detail, "description")
    files = change_paths(changes)
    summary = bounded_summary([description], summary_chars) or title
    base_ref = text_field(detail, "target_branch")
    head_ref = text_field(detail, "source_branch")
    branch_label = f"{base_ref} <- {head_ref}" if base_ref or head_ref else ""
    issues = linked_issue_numbers(description)
    merge_sha = text_field(detail, "merge_commit_sha", "squash_commit_sha")
    evidence = unique_strings(
        [
            f"MR !{number}: {title}",
            f"MR !{number} changed {', '.join(files)}" if files else "",
            f"MR !{number} closes issue {', '.join(f'#{issue}' for issue in issues)}" if issues else "",
        ]
    )
    return {
        "facetId": f"mr.{number}",
        "sourceType": "pr",
        "provider": "gitlab",
        "repo": repo,
        "title": title,
        "summary": summary,
        "url": text_field(detail, "web_url", "url"),
        "state": normalize_state(detail.get("state")),
        "createdAt": text_field(detail, "created_at"),
        "updatedAt": text_field(detail, "updated_at"),
        "closedAt": text_field(detail, "closed_at"),
        "mergedAt": text_field(detail, "merged_at"),
        "author": author_login(detail.get("author")),
        "isDraft": bool(detail.get("draft") or detail.get("work_in_progress")),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "head_repo": repo,
        "branch_label": branch_label,
        "changed_files": int_field(detail, "changes_count") or len(files),
        "additions": 0,
        "deletions": 0,
        "commit_headlines": [],
        "review_decision": "",
        "review_states": [],
        "commits": [merge_sha] if merge_sha else [],
        "merge_commit": merge_sha,
        "prs": [number] if number else [],
        "issues": issues,
        "files": files,
        "symbols": extract_symbols("\n".join([title, summary, *files])),
        "evidence": evidence,
    }


def issue_to_facet(repo: str, detail: dict[str, Any], summary_chars: int) -> dict[str, Any]:
    number = int_field(detail, "iid") or 0
    title = text_field(detail, "title") or f"Issue #{number}"
    description = text_field(detail, "description")
    labels = label_names(detail.get("labels"))
    labels_text = f"Labels: {', '.join(labels)}" if labels else ""
    summary = bounded_summary([description, labels_text], summary_chars) or title
    evidence = unique_strings(
        [
            f"issue #{number}: {title}",
            f"issue #{number} labels: {', '.join(labels)}" if labels else "",
        ]
    )
    return {
        "facetId": f"issue.{number}",
        "sourceType": "issue",
        "provider": "gitlab",
        "repo": repo,
        "title": title,
        "summary": summary,
        "url": text_field(detail, "web_url", "url"),
        "state": normalize_state(detail.get("state")),
        "updatedAt": text_field(detail, "updated_at"),
        "labels": labels,
        "commits": [],
        "prs": [],
        "issues": [number] if number else [],
        "files": [],
        "symbols": extract_symbols("\n".join([title, summary, *labels])),
        "evidence": evidence,
    }


def mr_is_in_snapshot(repo_path: Path, detail: dict[str, Any], snapshot_sha: str) -> bool:
    if not snapshot_sha:
        return True
    merge_sha = text_field(detail, "merge_commit_sha", "squash_commit_sha")
    return bool(merge_sha) and is_git_ancestor(repo_path, merge_sha, snapshot_sha)


def append_mr_facet(
    facets: list[dict[str, Any]],
    repo: str,
    item: dict[str, Any],
    hostname: str,
    summary_chars: int,
    retries: int,
    retry_delay_ms: int,
) -> None:
    number = int_field(item, "iid")
    if not number:
        return
    changes = run_glab_api(api_path(repo, f"merge_requests/{number}/changes"), hostname, retries, retry_delay_ms)
    facets.append(mr_to_facet(repo, item, changes if isinstance(changes, dict) else {}, summary_chars))


def fetch_mr_facets(
    repo: str,
    repo_path: Path,
    hostname: str,
    api_state: str,
    snapshot_sha: str,
    limit: int,
    summary_chars: int,
    retries: int,
    retry_delay_ms: int,
) -> list[dict[str, Any]]:
    if snapshot_sha and api_state == "opened":
        return []

    facets: list[dict[str, Any]] = []

    if not snapshot_sha:
        merge_requests = run_glab_api(
            api_path(repo, "merge_requests", {"state": api_state, "per_page": limit, "order_by": "updated_at", "sort": "desc"}),
            hostname,
            retries,
            retry_delay_ms,
        )
        if not isinstance(merge_requests, list):
            raise SystemExit("glab merge request list response was not a JSON list")
        for item in merge_requests:
            if isinstance(item, dict):
                append_mr_facet(facets, repo, item, hostname, summary_chars, retries, retry_delay_ms)
        return facets[:limit]

    page = 1
    while len(facets) < limit:
        merge_requests = run_glab_api(
            api_path(
                repo,
                "merge_requests",
                {"state": api_state, "per_page": limit, "order_by": "updated_at", "sort": "desc", "page": page},
            ),
            hostname,
            retries,
            retry_delay_ms,
        )
        if not isinstance(merge_requests, list):
            raise SystemExit("glab merge request list response was not a JSON list")
        if not merge_requests:
            break

        for item in merge_requests:
            if not isinstance(item, dict) or not mr_is_in_snapshot(repo_path, item, snapshot_sha):
                continue
            append_mr_facet(facets, repo, item, hostname, summary_chars, retries, retry_delay_ms)
            if len(facets) >= limit:
                break

        if len(facets) >= limit or len(merge_requests) < limit:
            break
        page += 1

    return facets[:limit]


def fetch_facets(
    repo: str,
    repo_path: Path,
    hostname: str,
    snapshot_ref: str,
    pr_limit: int,
    issue_limit: int,
    state: str,
    include: set[str],
    summary_chars: int,
    retries: int,
    retry_delay_ms: int,
) -> list[dict[str, Any]]:
    assert_glab_ready(hostname)
    api_state = state_param(state)
    snapshot_sha = resolve_snapshot_sha(repo_path, snapshot_ref) if snapshot_ref and "prs" in include else ""
    facets: list[dict[str, Any]] = []

    if "prs" in include:
        facets.extend(fetch_mr_facets(repo, repo_path, hostname, api_state, snapshot_sha, pr_limit, summary_chars, retries, retry_delay_ms))

    if "issues" in include:
        issues = run_glab_api(
            api_path(repo, "issues", {"state": api_state, "per_page": issue_limit, "order_by": "updated_at", "sort": "desc"}),
            hostname,
            retries,
            retry_delay_ms,
        )
        if not isinstance(issues, list):
            raise SystemExit("glab issue list response was not a JSON list")
        facets.extend(issue_to_facet(repo, item, summary_chars) for item in issues if isinstance(item, dict))

    return facets


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract GitLab MR/issue SourceFacet JSON using glab CLI.")
    parser.add_argument("--repo", required=True, help="GitLab repository in group/project or group/subgroup/project form")
    parser.add_argument("--hostname", default="", help="GitLab hostname for auth/API commands; defaults to glab's current host")
    parser.add_argument("--repo-path", default=".", help="Local git repository path used for MR snapshot filtering")
    parser.add_argument(
        "--snapshot-ref",
        default="",
        help="Optional local git ref; MRs are kept only when their merge commit is an ancestor of this ref",
    )
    parser.add_argument("--pr-limit", type=int, default=None, help="Maximum retained MRs after snapshot filtering")
    parser.add_argument("--issue-limit", type=int, default=None, help="Maximum retained provider issues")
    parser.add_argument("--state", choices=["all", "open", "closed"], default="all")
    parser.add_argument("--include", default="prs,issues", help="Comma-separated: prs,issues")
    parser.add_argument("--glab-retries", type=int, default=DEFAULT_GLAB_RETRIES, help="Retries for transient glab CLI failures")
    parser.add_argument("--glab-retry-delay-ms", type=int, default=DEFAULT_GLAB_RETRY_DELAY_MS, help="Initial retry delay for transient glab CLI failures")
    parser.add_argument("--summary-chars", type=int, default=4000)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--out", help="Write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if "/" not in args.repo:
        parser.error("--repo must be group/project or group/subgroup/project")
    include = {part.strip() for part in args.include.split(",") if part.strip()}
    if not include or include - {"prs", "issues"}:
        parser.error("--include must contain prs, issues, or prs,issues")
    args.include = include
    args.pr_limit = args.pr_limit if args.pr_limit is not None else 30
    args.issue_limit = args.issue_limit if args.issue_limit is not None else 30
    if args.pr_limit < 1 or args.pr_limit > 100:
        parser.error("--pr-limit must be from 1 to 100")
    if args.issue_limit < 1 or args.issue_limit > 100:
        parser.error("--issue-limit must be from 1 to 100")
    if args.glab_retries < 0 or args.glab_retries > 10:
        parser.error("--glab-retries must be from 0 to 10")
    if args.glab_retry_delay_ms < 0:
        parser.error("--glab-retry-delay-ms must be non-negative")
    args.repo_path = Path(args.repo_path).expanduser().resolve()
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    facets = fetch_facets(
        args.repo,
        args.repo_path,
        args.hostname,
        args.snapshot_ref,
        args.pr_limit,
        args.issue_limit,
        args.state,
        args.include,
        args.summary_chars,
        args.glab_retries,
        args.glab_retry_delay_ms,
    )
    text = json.dumps(facets, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
