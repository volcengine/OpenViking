"""Select an ordinary Linux test pool; inventory is a snapshot, not a reservation."""

import argparse
import json
import os
import urllib.error
import urllib.request

POOLS = ("openviking-ecs", "ov-ci-runner")
HOSTED = ["ubuntu-24.04"]
SHARED = ["self-hosted", "Linux", "X64", "ci-linux-tests"]
# Explicitly approved fork; keep usable when fork runs cannot read repository vars.
DEFAULT_TRUSTED_PR_REPOSITORIES = ["zhanglistar/OpenViking"]


def trusted_source(repository, event, trusted_pr_repositories=()):
    if repository != "volcengine/OpenViking":
        return False
    pr = event.get("pull_request")
    if not pr:
        return True
    source = (pr.get("head", {}).get("repo") or {}).get("full_name", "")
    return source == repository or source in trusted_pr_repositories


def select(pool, runners, trusted=True, run_id=0, selector_runner=""):
    if pool not in ("auto", "github-hosted", *POOLS):
        raise ValueError(f"Unknown Linux test pool: {pool}")
    if not trusted:
        return HOSTED, "Untrusted PRs and non-official repositories use GitHub-hosted"
    if pool == "github-hosted":
        return HOSTED, "GitHub-hosted explicitly selected"
    if pool in POOLS and runners is None:
        return ["self-hosted", "Linux", "X64", pool], f"Explicit pool {pool}; wait for capacity"
    if pool == "auto" and runners is None and selector_runner in POOLS:
        # Fork PRs receive no inventory secret. Both prepared selector hosts
        # carry this test label, so each dependent job can use either idle host.
        return (
            SHARED,
            "Inventory credentials absent; share prepared self-hosted pools",
        )
    candidates = []
    for runner in runners or []:
        labels = {label["name"].lower() for label in runner.get("labels", [])}
        for name in POOLS:
            if pool not in ("auto", name):
                continue
            if {"self-hosted", "linux", "x64", name} <= labels:
                # The selector's own runner becomes available when this job
                # finishes, before dependent test jobs can start.
                available = not runner.get("busy", True) or (
                    selector_runner and runner.get("name") == selector_runner
                )
                if runner.get("status") == "online" and available:
                    candidates.append(name)
    candidates = sorted(set(candidates))
    if candidates:
        chosen = candidates[int(run_id) % len(candidates)]
        return ["self-hosted", "Linux", "X64", chosen], f"Online idle pool: {chosen}"
    return HOSTED, "No eligible idle runner or inventory unavailable; use GitHub-hosted"


def inventory(api_url, repository, token):
    runners = []
    for page in range(1, 101):
        request = urllib.request.Request(
            f"{api_url}/repos/{repository}/actions/runners?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            batch = json.load(response)["runners"]
        runners.extend(batch)
        if len(batch) < 100:
            return runners
    raise ValueError("Runner inventory exceeded 100 pages")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", default="auto", choices=("auto", "github-hosted", *POOLS))
    args = parser.parse_args()
    with open(os.environ["GITHUB_EVENT_PATH"]) as stream:
        event = json.load(stream)
    repository = os.environ["GITHUB_REPOSITORY"]
    try:
        configured = os.environ.get("TRUSTED_PR_REPOSITORIES", "")
        trusted_pr_repositories = (
            json.loads(configured) if configured else DEFAULT_TRUSTED_PR_REPOSITORIES
        )
        if not isinstance(trusted_pr_repositories, list) or not all(
            isinstance(source, str) and source for source in trusted_pr_repositories
        ):
            raise ValueError("Expected a JSON list of repository names")
    except (ValueError, TypeError):
        print("::warning::Invalid trusted PR repository list; no fork repositories are trusted")
        trusted_pr_repositories = []
    trusted = trusted_source(repository, event, trusted_pr_repositories)
    token = os.environ.get("RUNNER_READ_TOKEN", "")
    runners = None
    if trusted and token and args.pool != "github-hosted":
        try:
            runners = inventory(os.environ["GITHUB_API_URL"], repository, token)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            # Do not print request headers, credentials or response bodies.
            print("::warning::Runner inventory unavailable; falling back to GitHub-hosted")
            runners = []
    labels, reason = select(
        args.pool,
        runners,
        trusted,
        os.environ["GITHUB_RUN_ID"],
        os.environ.get("RUNNER_NAME", ""),
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"runs_on={json.dumps(labels)}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(f"Linux test pool: `{json.dumps(labels)}`\n\n{reason}.\n")
    print(reason)


if __name__ == "__main__":
    main()
