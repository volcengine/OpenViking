#!/usr/bin/env python3
"""Extract GitHub PR/issue resource facets with only Python stdlib + gh CLI."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable


PR_LIST_FIELDS = "number,title,state,url,updatedAt,baseRefName,headRefName,headRepository,headRepositoryOwner,isDraft"
PR_VIEW_FIELDS = "number,title,body,state,url,updatedAt,createdAt,closedAt,mergedAt,author,comments,reviews,latestReviews,reviewDecision,files,commits,closingIssuesReferences,mergeCommit,baseRefName,headRefName,headRepository,headRepositoryOwner,isDraft,additions,deletions,changedFiles"
ISSUE_LIST_FIELDS = "number,title,state,url,updatedAt,labels"
ISSUE_VIEW_FIELDS = "number,title,body,state,url,updatedAt,labels,comments"
DEFAULT_GH_RETRIES = 3
DEFAULT_GH_RETRY_DELAY_MS = 1000
TRANSIENT_GH_ERROR = re.compile(
    r"EOF|timeout|timed out|connection reset|connection refused|temporarily unavailable|"
    r"TLS handshake timeout|502|503|504|rate limit|secondary rate limit|abuse detection",
    re.IGNORECASE,
)


def is_transient_gh_error(stderr: str, stdout: str = "") -> bool:
    return TRANSIENT_GH_ERROR.search(f"{stderr}\n{stdout}") is not None


def run_gh(args: list[str], retries: int = DEFAULT_GH_RETRIES, retry_delay_ms: int = DEFAULT_GH_RETRY_DELAY_MS) -> Any:
    if shutil.which("gh") is None:
        raise SystemExit("GitHub CLI 'gh' is required. Install it and run: gh auth login")
    attempts = max(1, retries + 1)
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(["gh", *args], text=True, capture_output=True)
        last_result = result
        if result.returncode == 0:
            text = result.stdout.strip()
            return json.loads(text) if text else None
        if attempt < attempts and is_transient_gh_error(result.stderr, result.stdout):
            delay = max(0, retry_delay_ms) / 1000 * (2 ** (attempt - 1))
            print(
                f"gh {' '.join(args)} failed with transient error; retrying "
                f"{attempt}/{retries} after {delay:.1f}s: {result.stderr.strip()}",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        break
    assert last_result is not None
    raise SystemExit(f"gh {' '.join(args)} failed:\n{last_result.stderr.strip()}")


def auth_status_failure(result: subprocess.CompletedProcess[str], login_hint: str) -> str:
    details: list[str] = []
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    if stderr:
        details.append(f"stderr: {stderr}")
    if stdout:
        details.append(f"stdout: {stdout}")
    detail = "\n".join(details) if details else "gh auth status returned no diagnostic output"
    return f"gh auth check failed with exit code {result.returncode}. Run: {login_hint}\n{detail}"


def run_git(repo_path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise SystemExit("git is required for PR snapshot filtering")
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


def login_hint(hostname: str) -> str:
    return f"gh auth login --hostname {hostname}" if hostname and hostname != "github.com" else "gh auth login"


def repo_selector(repo: str, hostname: str) -> str:
    if hostname and hostname != "github.com" and not repo.startswith(f"{hostname}/"):
        return f"{hostname}/{repo}"
    return repo


def assert_gh_ready(hostname: str) -> None:
    if shutil.which("gh") is None:
        raise SystemExit("GitHub CLI 'gh' is required. Install it and run: gh auth login")
    args = ["auth", "status"]
    if hostname:
        args.extend(["--hostname", hostname])
    attempts = max(1, DEFAULT_GH_RETRIES + 1)
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(["gh", *args], text=True, capture_output=True)
        last_result = result
        if result.returncode == 0:
            return
        if attempt < attempts and is_transient_gh_error(result.stderr, result.stdout):
            delay = max(0, DEFAULT_GH_RETRY_DELAY_MS) / 1000 * (2 ** (attempt - 1))
            print(
                f"gh auth status failed with transient error; retrying "
                f"{attempt}/{DEFAULT_GH_RETRIES} after {delay:.1f}s: {result.stderr.strip()}",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        break
    assert last_result is not None
    raise SystemExit(auth_status_failure(last_result, login_hint(hostname)))


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


def int_field(value: Any, key: str = "number") -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, dict) and isinstance(value.get(key), int):
        return value[key]
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


def commit_sha(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("commit"), dict):
        return text_field(value["commit"], "oid", "sha", "abbreviatedOid")
    return text_field(value, "oid", "sha", "abbreviatedOid")


def commit_headline(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("commit"), dict):
        return text_field(value["commit"], "messageHeadline", "message", "title")
    return text_field(value, "messageHeadline", "message", "title")


def author_login(value: Any) -> str:
    return text_field(value, "login", "name")


def review_state(value: Any) -> str:
    return text_field(value, "state")


def file_path(value: Any) -> str:
    return text_field(value, "path", "filename")


def label_name(value: Any) -> str:
    return text_field(value, "name")


def repo_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    owner = value.get("owner")
    owner_login = text_field(owner, "login") if isinstance(owner, dict) else ""
    name = text_field(value, "name", "nameWithOwner")
    name_with_owner = text_field(value, "nameWithOwner")
    if name_with_owner and "/" in name_with_owner:
        return name_with_owner
    return f"{owner_login}/{name}" if owner_login and name else name


def body_text(value: Any) -> str:
    return text_field(value, "body", "title", "name")


def pr_detail_to_facet(repo: str, detail: dict[str, Any], summary_chars: int) -> dict[str, Any]:
    number = int_field(detail) or 0
    title = text_field(detail, "title") or f"PR #{number}"
    body = text_field(detail, "body")
    comments = [body_text(item) for item in detail.get("comments", []) if body_text(item)]
    reviews = [body_text(item) for item in detail.get("reviews", []) if body_text(item)]
    commits = unique_strings(commit_sha(item) for item in detail.get("commits", []) if commit_sha(item))
    commit_headlines = unique_strings(commit_headline(item) for item in detail.get("commits", []) if commit_headline(item))
    files = unique_strings(file_path(item) for item in detail.get("files", []) if file_path(item))
    review_states = unique_strings(review_state(item) for item in detail.get("latestReviews", []) if review_state(item))
    issues = unique_numbers(
        number for number in (int_field(item) for item in detail.get("closingIssuesReferences", [])) if number is not None
    )
    summary = bounded_summary([body, *comments, *reviews], summary_chars) or title
    base_ref = text_field(detail, "baseRefName")
    head_ref = text_field(detail, "headRefName")
    head_repo = repo_name(detail.get("headRepository")) or repo
    branch_label = f"{base_ref} <- {head_ref}" if base_ref or head_ref else ""
    evidence = unique_strings(
        [
            f"PR #{number}: {title}",
            f"PR #{number} changed {', '.join(files)}" if files else "",
            f"PR #{number} closes issue {', '.join(f'#{issue}' for issue in issues)}" if issues else "",
        ]
    )
    return {
        "facetId": f"pr.{number}",
        "sourceType": "pr",
        "repo": repo,
        "title": title,
        "summary": summary,
        "url": text_field(detail, "url"),
        "state": text_field(detail, "state"),
        "createdAt": text_field(detail, "createdAt"),
        "updatedAt": text_field(detail, "updatedAt"),
        "closedAt": text_field(detail, "closedAt"),
        "mergedAt": text_field(detail, "mergedAt"),
        "author": author_login(detail.get("author")),
        "isDraft": bool(detail.get("isDraft")),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "head_repo": head_repo,
        "branch_label": branch_label,
        "changed_files": detail.get("changedFiles") or len(files),
        "additions": detail.get("additions") or 0,
        "deletions": detail.get("deletions") or 0,
        "commit_headlines": commit_headlines,
        "review_decision": text_field(detail, "reviewDecision"),
        "review_states": review_states,
        "commits": commits,
        "prs": [number] if number else [],
        "issues": issues,
        "files": files,
        "symbols": extract_symbols("\n".join([title, summary, *files])),
        "evidence": evidence,
    }


def issue_detail_to_facet(repo: str, detail: dict[str, Any], summary_chars: int) -> dict[str, Any]:
    number = int_field(detail) or 0
    title = text_field(detail, "title") or f"Issue #{number}"
    body = text_field(detail, "body")
    comments = [body_text(item) for item in detail.get("comments", []) if body_text(item)]
    labels = [label_name(item) for item in detail.get("labels", []) if label_name(item)]
    labels_text = f"Labels: {', '.join(labels)}" if labels else ""
    summary = bounded_summary([body, *comments, labels_text], summary_chars) or title
    evidence = unique_strings(
        [
            f"issue #{number}: {title}",
            f"issue #{number} labels: {', '.join(labels)}" if labels else "",
        ]
    )
    return {
        "facetId": f"issue.{number}",
        "sourceType": "issue",
        "repo": repo,
        "title": title,
        "summary": summary,
        "url": text_field(detail, "url"),
        "state": text_field(detail, "state"),
        "updatedAt": text_field(detail, "updatedAt"),
        "labels": labels,
        "commits": [],
        "prs": [],
        "issues": [number] if number else [],
        "files": [],
        "symbols": extract_symbols("\n".join([title, summary, *labels])),
        "evidence": evidence,
    }


def gh_list(kind: str, repo: str, limit: int, state: str, retries: int, retry_delay_ms: int) -> list[dict[str, Any]]:
    fields = PR_LIST_FIELDS if kind == "pr" else ISSUE_LIST_FIELDS
    return run_gh([kind, "list", "--repo", repo, "--limit", str(limit), "--state", state, "--json", fields], retries, retry_delay_ms)


def gh_view(kind: str, repo: str, number: int, retries: int, retry_delay_ms: int) -> dict[str, Any]:
    fields = PR_VIEW_FIELDS if kind == "pr" else ISSUE_VIEW_FIELDS
    return run_gh([kind, "view", str(number), "--repo", repo, "--json", fields], retries, retry_delay_ms)


def pr_is_in_snapshot(repo_path: Path, detail: dict[str, Any], snapshot_sha: str) -> bool:
    if not snapshot_sha:
        return True
    merge_sha = commit_sha(detail.get("mergeCommit"))
    return bool(merge_sha) and is_git_ancestor(repo_path, merge_sha, snapshot_sha)


def fetch_pr_facets(
    query_repo: str,
    facet_repo: str,
    repo_path: Path,
    snapshot_sha: str,
    limit: int,
    state: str,
    concurrency: int,
    summary_chars: int,
    retries: int,
    retry_delay_ms: int,
) -> list[dict[str, Any]]:
    if snapshot_sha and state == "open":
        return []

    facets: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    candidate_limit = max(1, limit)
    previous_candidate_count = -1

    while len(facets) < limit:
        candidates = gh_list("pr", query_repo, candidate_limit, state, retries, retry_delay_ms)
        if not isinstance(candidates, list):
            raise SystemExit("gh PR list response was not a JSON list")

        candidate_count = len(candidates)
        numbers: list[int] = []
        for item in candidates:
            number = int_field(item)
            if number is not None and number not in seen_numbers:
                seen_numbers.add(number)
                numbers.append(number)

        def one(number: int) -> dict[str, Any] | None:
            detail = gh_view("pr", query_repo, number, retries, retry_delay_ms)
            if snapshot_sha and not pr_is_in_snapshot(repo_path, detail, snapshot_sha):
                return None
            return pr_detail_to_facet(facet_repo, detail, summary_chars)

        if numbers:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
                for facet in pool.map(one, numbers):
                    if facet:
                        facets.append(facet)
                        if len(facets) >= limit:
                            break

        if len(facets) >= limit or candidate_count < candidate_limit or candidate_count == previous_candidate_count:
            break
        previous_candidate_count = candidate_count
        candidate_limit *= 2

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
    concurrency: int,
    summary_chars: int,
    retries: int,
    retry_delay_ms: int,
) -> list[dict[str, Any]]:
    assert_gh_ready(hostname)
    selected_repo = repo_selector(repo, hostname)
    snapshot_sha = resolve_snapshot_sha(repo_path, snapshot_ref) if snapshot_ref and "prs" in include else ""
    facets: list[dict[str, Any]] = []
    jobs: list[tuple[str, int]] = []
    if "prs" in include:
        facets.extend(fetch_pr_facets(selected_repo, repo, repo_path, snapshot_sha, pr_limit, state, concurrency, summary_chars, retries, retry_delay_ms))
    if "issues" in include:
        jobs.extend(("issue", item["number"]) for item in gh_list("issue", selected_repo, issue_limit, state, retries, retry_delay_ms))

    def one(job: tuple[str, int]) -> dict[str, Any] | None:
        kind, number = job
        detail = gh_view(kind, selected_repo, number, retries, retry_delay_ms)
        return issue_detail_to_facet(repo, detail, summary_chars)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        facets.extend(facet for facet in pool.map(one, jobs) if facet)
    return facets


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract GitHub PR/issue SourceFacet JSON using gh CLI.")
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form")
    parser.add_argument("--hostname", default="", help="GitHub hostname for auth/API commands; defaults to gh's current host")
    parser.add_argument("--repo-path", default=".", help="Local git repository path used for PR snapshot filtering")
    parser.add_argument(
        "--snapshot-ref",
        default="",
        help="Optional local git ref; PRs are kept only when their merge commit is an ancestor of this ref",
    )
    parser.add_argument("--pr-limit", type=int, default=None, help="Maximum retained PRs after snapshot filtering")
    parser.add_argument("--issue-limit", type=int, default=None, help="Maximum retained provider issues")
    parser.add_argument("--state", choices=["all", "open", "closed"], default="all")
    parser.add_argument("--include", default="prs,issues", help="Comma-separated: prs,issues")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--gh-retries", type=int, default=DEFAULT_GH_RETRIES, help="Retries for transient gh CLI failures")
    parser.add_argument("--gh-retry-delay-ms", type=int, default=DEFAULT_GH_RETRY_DELAY_MS, help="Initial retry delay for transient gh CLI failures")
    parser.add_argument("--summary-chars", type=int, default=4000)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--out", help="Write JSON to this file instead of stdout")
    args = parser.parse_args(argv)
    if not re.match(r"^[^/]+/[^/]+$", args.repo):
        parser.error("--repo must be owner/name")
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
    if args.concurrency < 1 or args.concurrency > 10:
        parser.error("--concurrency must be from 1 to 10")
    if args.gh_retries < 0 or args.gh_retries > 10:
        parser.error("--gh-retries must be from 0 to 10")
    if args.gh_retry_delay_ms < 0:
        parser.error("--gh-retry-delay-ms must be non-negative")
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
        args.concurrency,
        args.summary_chars,
        args.gh_retries,
        args.gh_retry_delay_ms,
    )
    text = json.dumps(facets, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
