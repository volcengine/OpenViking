"""Check wheel cache producer trust and cache preservation through packaging."""

import fnmatch
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    return yaml.safe_load((ROOT / relative).read_text())


def allowed(expression, repository, ref, event):
    for context, value in {
        "github.repository": repository,
        "github.ref": ref,
        "github.event_name": event,
    }.items():
        expression = expression.replace(context, repr(value))
    return eval(expression.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}})


@pytest.mark.parametrize(
    "repository,ref,event,expected",
    [
        ("volcengine/OpenViking", "refs/heads/main", "push", True),
        ("volcengine/OpenViking", "refs/heads/main", "schedule", True),
        ("volcengine/OpenViking", "refs/heads/main", "workflow_dispatch", True),
        ("fork/OpenViking", "refs/heads/main", "push", False),
        ("volcengine/OpenViking", "refs/heads/feature", "workflow_dispatch", False),
        ("volcengine/OpenViking", "refs/tags/v1.0.0", "workflow_dispatch", False),
        ("volcengine/OpenViking", "refs/pull/123/merge", "pull_request", False),
    ],
)
def test_only_official_main_can_warm_shared_wheel_caches(repository, ref, event, expected):
    workflow = load(".github/workflows/macos-wheel-cache.yml")
    assert allowed(workflow["jobs"]["warm"]["if"], repository, ref, event) == expected
    cache = load(".github/actions/compiler-cache/action.yml")
    for step in cache["runs"]["steps"]:
        if step.get("uses") == "actions/cache@v5":
            assert allowed(step["if"], repository, ref, event) == expected
        elif step.get("uses") == "actions/cache/restore@v5":
            assert allowed(step["if"], repository, ref, event) != expected


def test_producer_reuses_the_pr_wheel_build_with_only_intel_selected():
    workflow = load(".github/workflows/macos-wheel-cache.yml")
    warm = workflow["jobs"]["warm"]
    pr = load(".github/workflows/pr.yml")
    assert warm["uses"] == pr["jobs"]["build"]["uses"] == "./.github/workflows/_build.yml"
    assert json.loads(warm["with"]["os_json"]) == ["macos-15-intel"]
    assert json.loads(warm["with"]["python_json"]) == ["3.10"]
    assert warm["with"]["build_wheels"] is True
    assert warm["with"]["build_sdist"] is False
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    # PyYAML treats the YAML 1.1 word "on" as True.
    triggers = workflow[True]
    assert set(triggers) == {"push", "schedule", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    patterns = triggers["push"]["paths"]
    for path in [
        "src/abi3_engine_backend.cpp",
        "crates/ov_cli/src/main.rs",
        "crates/ragfs/Cargo.toml",
        "third_party/agfs/Cargo.lock",
        "Cargo.lock",
        "setup.py",
        "scripts/build_support/versioning.py",
        ".github/actions/compiler-cache/action.yml",
        ".github/workflows/_build.yml",
    ]:
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    assert not any(fnmatch.fnmatchcase("docs/cache.md", pattern) for pattern in patterns)


def test_packaging_clean_preserves_restored_compiler_outputs(tmp_path):
    """Exercise the real ignore rules with the same git clean used by wheels."""
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    cached = [tmp_path / "target/release/deps/object", tmp_path / ".cache/ccache/object"]
    for path in cached:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"restored compiler output")
    scratch = tmp_path / "untracked-scratch"
    scratch.write_text("remove me")
    steps = load(".github/workflows/_build.yml")["jobs"]["build-other"]["steps"]
    clean = next(step["run"] for step in steps if step.get("name", "").startswith("Clean"))
    assert "git clean -fd\n" in clean
    subprocess.run(["git", "clean", "-fd"], cwd=tmp_path, check=True, capture_output=True)
    assert all(path.read_bytes() == b"restored compiler output" for path in cached)
    assert not scratch.exists()
