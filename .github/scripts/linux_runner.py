"""Route Linux builds and tests by workload without runner inventory access."""

import argparse
import json
import os

POOLS = ("balanced", "auto", "github-hosted", "ov-ci-runner")
HOSTED = ["ubuntu-24.04"]
SELF_HOSTED = ["self-hosted", "Linux", "X64", "ov-ci-runner"]


def trusted_source(repository, event, trusted_pr_repositories=()):
    if repository != "volcengine/OpenViking":
        return False
    pr = event.get("pull_request")
    if not pr:
        return True
    source = (pr.get("head", {}).get("repo") or {}).get("full_name", "")
    return source == repository or source in trusted_pr_repositories


def select(pool="auto", trusted=True):
    if pool not in POOLS:
        raise ValueError(f"Unknown Linux test pool: {pool}")
    if not trusted:
        return HOSTED, HOSTED, "Untrusted PRs and non-official repositories use GitHub-hosted"
    if pool == "github-hosted":
        return HOSTED, HOSTED, "GitHub-hosted explicitly selected for all Linux jobs"
    if pool == "ov-ci-runner":
        return SELF_HOSTED, SELF_HOSTED, "ov-ci-runner explicitly selected for all Linux jobs"
    # Heavy builds use the primary lane; tests and checks use the hosted lane.
    # balanced remains a compatibility alias; neither mode queries availability.
    return (
        SELF_HOSTED,
        HOSTED,
        "Linux workload routing: heavy builds on ov-ci-runner, tests and checks on GitHub-hosted",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", default="auto", choices=POOLS)
    args = parser.parse_args()
    with open(os.environ["GITHUB_EVENT_PATH"]) as stream:
        event = json.load(stream)
    repository = os.environ["GITHUB_REPOSITORY"]
    try:
        trusted_pr_repositories = json.loads(os.environ.get("TRUSTED_PR_REPOSITORIES") or "[]")
        if not isinstance(trusted_pr_repositories, list) or not all(
            isinstance(source, str) and source for source in trusted_pr_repositories
        ):
            raise ValueError("Expected a JSON list of repository names")
    except (ValueError, TypeError):
        print("::warning::Invalid trusted PR repository list; no fork repositories are trusted")
        trusted_pr_repositories = []
    primary, secondary, reason = select(
        args.pool, trusted_source(repository, event, trusted_pr_repositories)
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"runs_on={json.dumps(primary)}\n")
        stream.write(f"hosted_runs_on={json.dumps(secondary)}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
        stream.write(
            f"{reason}.\n\n"
            f"Heavy builds: `{json.dumps(primary)}`\n\n"
            f"Tests and checks: `{json.dumps(secondary)}`\n"
        )
    print(reason)


if __name__ == "__main__":
    main()
