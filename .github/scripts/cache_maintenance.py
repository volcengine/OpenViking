"""Retain recent compiler caches; default to a read-only deletion plan."""

import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

MAIN_REF = "refs/heads/main"
CARGO_KEY = re.compile(
    r"^cargo-v3-(Linux|macOS|Windows)-(X64|ARM64)-(.+)-"
    r"[0-9a-f]{64}-[0-9a-f]{64}-[0-9a-f]{40}$"
)
PR_REF = re.compile(r"^refs/pull/([1-9][0-9]*)/(merge|head)$")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Cache timestamps must include a timezone")
    return parsed


def deletion_plan(caches, closed_prs, now):
    """Keep two entries per Cargo platform/target/version, regardless of toolchain.

    Unknown keys/refs are untouched. Age grace periods protect recently saved
    entries, including saves from concurrent main runs and recently closed PRs.
    GitHub cache versions distinguish incompatible archive paths/compression.
    """
    groups = defaultdict(list)
    selected = []
    for cache in caches:
        match = CARGO_KEY.fullmatch(cache["key"])
        if cache["ref"] == MAIN_REF and match and cache.get("version"):
            groups[(*match.groups(), cache["version"])].append(cache)
        pr = PR_REF.fullmatch(cache["ref"])
        if (
            pr
            and int(pr[1]) in closed_prs
            and now - timestamp(cache["created_at"]) >= timedelta(days=7)
        ):
            selected.append((cache, "closed PR, at least 7 days old"))
    for entries in groups.values():
        entries.sort(key=lambda cache: (timestamp(cache["created_at"]), cache["id"]), reverse=True)
        for cache in entries[2:]:
            if now - timestamp(cache["created_at"]) >= timedelta(days=1):
                selected.append((cache, "superseded main Cargo cache"))
    return [
        {
            "id": cache["id"],
            "key": cache["key"],
            "ref": cache["ref"],
            "bytes": cache["size_in_bytes"],
            "reason": reason,
        }
        for cache, reason in sorted(selected, key=lambda item: item[0]["id"])
    ]


class GitHub:
    def __init__(self, repo):
        self.repo = repo

    def api(self, path, *, paginate=False, delete=False):
        command = ["gh", "api", f"repos/{self.repo}/{path}"]
        if paginate:
            command.extend(["--paginate", "--slurp"])
        if delete:
            command.extend(["--method", "DELETE"])
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            # A concurrent eviction/deletion already achieved the requested removal.
            if delete and "(HTTP 404)" in result.stderr:
                return False
            raise RuntimeError(result.stderr.strip() or "GitHub API request failed")
        return True if delete else json.loads(result.stdout)

    def caches(self):
        pages = self.api("actions/caches?per_page=100", paginate=True)
        return [cache for page in pages for cache in page["actions_caches"]]

    def closed_prs(self, caches):
        numbers = {int(match[1]) for cache in caches if (match := PR_REF.fullmatch(cache["ref"]))}
        # Resolve every referenced PR before deleting anything. API failures abort
        # rather than treating an inaccessible/unknown PR as closed.
        return {
            number for number in sorted(numbers) if self.api(f"pulls/{number}")["state"] == "closed"
        }


def maintain(github, *, apply=False, now=None):
    caches = github.caches()
    plan = deletion_plan(caches, github.closed_prs(caches), now or datetime.now(timezone.utc))
    deleted = 0
    already_absent = 0
    if apply:
        for cache in plan:
            if github.api(f"actions/caches/{cache['id']}", delete=True):
                deleted += 1
            else:
                already_absent += 1
    return {
        "mode": "apply" if apply else "dry-run",
        "cache_count": len(caches),
        "cache_bytes": sum(cache["size_in_bytes"] for cache in caches),
        "selected_count": len(plan),
        "selected_bytes": sum(cache["bytes"] for cache in plan),
        "deleted_count": deleted,
        "already_absent_count": already_absent,
        "plan": plan,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub owner/repository")
    parser.add_argument("--apply", action="store_true", help="Delete the selected cache IDs")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be owner/repository")
    print(json.dumps(maintain(GitHub(args.repo), apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
