"""Select an ordinary Linux test pool; inventory is a snapshot, not a reservation."""

import argparse
import json
import os
import urllib.error
import urllib.request

POOLS = ("openviking-ecs", "ov-ci-runner")
HOSTED = ["ubuntu-24.04"]


def select(pool, runners, trusted=True, run_id=0, selector_runner=""):
    if pool not in ("auto", "github-hosted", *POOLS):
        raise ValueError(f"Unknown Linux test pool: {pool}")
    if not trusted:
        return HOSTED, "Fork PRs and non-official repositories use GitHub-hosted"
    if pool == "github-hosted":
        return HOSTED, "GitHub-hosted explicitly selected"
    if pool in POOLS and runners is None:
        return ["self-hosted", "Linux", "X64", pool], f"Explicit pool {pool}; wait for capacity"
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
    pr = event.get("pull_request")
    trusted = repository == "volcengine/OpenViking" and (
        not pr or (pr.get("head", {}).get("repo") or {}).get("full_name") == repository
    )
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
