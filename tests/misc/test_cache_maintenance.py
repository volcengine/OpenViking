"""Exercise retention, GitHub pagination, and deletion failure boundaries."""

import importlib.util
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "cache_maintenance", ROOT / ".github/scripts/cache_maintenance.py"
)
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def cache(identifier, *, platform="macOS-X64-host", age=3, ref=maintenance.MAIN_REF, **changes):
    return {
        "id": identifier,
        "key": f"cargo-v3-{platform}-{'a' * 64}-{'b' * 64}-{identifier:040x}",
        "version": "archive-version",
        "ref": ref,
        "created_at": (NOW - timedelta(days=age)).isoformat(),
        "size_in_bytes": 800_000_000,
        **changes,
    }


@pytest.mark.parametrize(
    "platform",
    [
        "Linux-X64-host",
        "Linux-ARM64-host",
        "macOS-ARM64-host",
        "macOS-X64-host",
        "macOS-X64-x86_64-apple-darwin",
        "Windows-X64-host",
    ],
)
def test_keep_two_newest_for_every_platform_and_explicit_target(platform):
    entries = [cache(index, platform=platform, age=6 - index) for index in range(1, 5)]
    assert [item["id"] for item in maintenance.deletion_plan(entries, set(), NOW)] == [1, 2]


def test_retention_separates_platform_target_and_archive_version():
    entries = [
        cache(1),
        cache(2, platform="Linux-X64-host"),
        cache(3, platform="macOS-ARM64-host"),
        cache(4, platform="macOS-X64-x86_64-apple-darwin"),
        cache(5, version="different-archive-version"),
    ]
    assert maintenance.deletion_plan(entries, set(), NOW) == []


def test_old_toolchain_and_lockfile_variants_expire_but_recent_saves_survive():
    entries = [
        cache(1, age=7),
        cache(2, age=5, key=f"cargo-v3-macOS-X64-host-{'c' * 64}-{'d' * 64}-{'e' * 40}"),
        cache(3, age=0.75),
        cache(4, age=0.5),
        cache(5, age=0.25),
    ]
    assert [item["id"] for item in maintenance.deletion_plan(entries, set(), NOW)] == [1, 2]


def test_other_main_caches_unknown_formats_and_refs_are_untouched():
    entries = [
        cache(1, key="ccache-v2-old"),
        cache(2, key="cargo-v4-new"),
        cache(3, key="cargo-v3-malformed"),
        cache(4, ref="refs/heads/release"),
        cache(5, ref="refs/pull/unrecognized/merge"),
        cache(6, version=""),
    ]
    assert maintenance.deletion_plan(entries, {1}, NOW) == []


def test_closed_pr_caches_expire_while_open_and_recent_closed_caches_survive():
    entries = [
        cache(1, age=7, ref="refs/pull/10/merge", key="uv-cache"),
        cache(2, age=20, ref="refs/pull/10/head"),
        cache(3, age=30, ref="refs/pull/11/merge"),
        cache(4, age=6.9, ref="refs/pull/10/merge"),
    ]
    assert [item["id"] for item in maintenance.deletion_plan(entries, {10}, NOW)] == [1, 2]


def test_invalid_timestamp_aborts_plan():
    with pytest.raises(ValueError):
        maintenance.deletion_plan(
            [cache(1, created_at="2026-11-01"), cache(2), cache(3)], set(), NOW
        )


def test_api_collects_all_pages_and_checks_each_pr_once(monkeypatch):
    calls = []
    entries = [
        cache(1, ref="refs/pull/10/merge"),
        cache(2, ref="refs/pull/10/head"),
        cache(3, ref="refs/pull/11/merge"),
    ]

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs == {"capture_output": True, "text": True, "timeout": 60}
        if "actions/caches?per_page=100" in command[2]:
            return SimpleNamespace(
                returncode=0,
                stdout=maintenance.json.dumps(
                    [{"actions_caches": entries[:2]}, {"actions_caches": entries[2:]}]
                ),
            )
        return SimpleNamespace(
            returncode=0,
            stdout='{"state": "closed"}' if command[2].endswith("/10") else '{"state": "open"}',
        )

    monkeypatch.setattr(subprocess, "run", run)
    github = maintenance.GitHub("volcengine/OpenViking")
    assert github.caches() == entries
    assert calls[0][-2:] == ["--paginate", "--slurp"]
    assert github.closed_prs(entries) == {10}
    assert len(calls) == 3


@pytest.mark.parametrize(
    "delete,status,expected", [(True, 404, False), (True, 403, None), (False, 404, None)]
)
def test_only_delete_not_found_is_tolerated(monkeypatch, delete, status, expected):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr=f"gh: request failed (HTTP {status})"
        ),
    )
    github = maintenance.GitHub("volcengine/OpenViking")
    if expected is None:
        with pytest.raises(RuntimeError, match=str(status)):
            github.api("actions/caches/1", delete=delete)
    else:
        assert github.api("actions/caches/1", delete=delete) is expected


class FakeGitHub:
    def __init__(self, fail_lookup=False):
        self.deleted = []
        self.fail_lookup = fail_lookup

    def caches(self):
        return [cache(index, age=10 - index) for index in range(1, 5)]

    def closed_prs(self, caches):
        if self.fail_lookup:
            raise RuntimeError("PR lookup failed")
        return set()

    def api(self, path, *, delete):
        assert delete
        self.deleted.append(path)
        return not path.endswith("/2")


def test_default_is_read_only_and_apply_deletes_only_planned_ids():
    github = FakeGitHub()
    preview = maintenance.maintain(github, now=NOW)
    assert github.deleted == []
    assert preview["mode"] == "dry-run"
    assert preview["selected_count"] == 2
    assert preview["selected_bytes"] == 1_600_000_000
    applied = maintenance.maintain(github, apply=True, now=NOW)
    assert github.deleted == ["actions/caches/1", "actions/caches/2"]
    assert applied["deleted_count"] == applied["already_absent_count"] == 1


def test_lookup_failure_aborts_before_any_deletion():
    github = FakeGitHub(fail_lookup=True)
    with pytest.raises(RuntimeError, match="PR lookup"):
        maintenance.maintain(github, apply=True, now=NOW)
    assert github.deleted == []


def test_workflow_scopes_cleanup_permissions_and_waits_for_cache_save():
    workflow = yaml.safe_load((ROOT / ".github/workflows/macos-wheel-cache.yml").read_text())
    cleanup = workflow["jobs"]["cleanup"]
    assert cleanup["needs"] == "warm"
    assert cleanup["if"] == workflow["jobs"]["warm"]["if"]
    assert cleanup["permissions"] == {
        "contents": "read",
        "actions": "write",
        "pull-requests": "read",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert cleanup["timeout-minutes"] == 15
    assert ".github/scripts/cache_maintenance.py" in workflow[True]["push"]["paths"]
    step = cleanup["steps"][1]
    assert step["run"].splitlines()[0] == "set -euo pipefail"
    assert "--apply" in step["run"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    build = yaml.safe_load((ROOT / ".github/workflows/_build.yml").read_text())["jobs"]
    assert build["build-other"]["timeout-minutes"] == 90
    assert build["build-studio"]["timeout-minutes"] == 15
    pr = yaml.safe_load((ROOT / ".github/workflows/pr.yml").read_text())
    contract = pr["jobs"]["check-deps"]["steps"][-1]["run"]
    assert "tests/misc/test_cache_maintenance.py" in contract
    assert "tests/misc/test_cli_model_query_retry.py" in contract


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="The cleanup job uses bash on Ubuntu",
)
@pytest.mark.parametrize("script_status", [0, 17])
def test_cleanup_pipeline_propagates_script_failure_before_writing_summary(tmp_path, script_status):
    """Run the actual step with GitHub's default bash -e and a stub producer."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/macos-wheel-cache.yml").read_text())
    command = workflow["jobs"]["cleanup"]["steps"][1]["run"]
    commands = tmp_path / "bin"
    commands.mkdir()
    python = commands / "python3"
    python.write_text(
        "#!/bin/sh\n"
        + ('printf \'{"mode":"apply"}\\n\'\n' if script_status == 0 else "")
        + f"exit {script_status}\n"
    )
    python.chmod(0o755)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-e", "-c", command],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
            "GH_REPO": "volcengine/OpenViking",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == script_status
    if script_status:
        assert not summary.exists()
    else:
        assert '{"mode":"apply"}' in summary.read_text()
        assert (tmp_path / "cache-maintenance.json").read_text() == '{"mode":"apply"}\n'
