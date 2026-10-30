"""Check fixed build routing, fork isolation and platform compatibility."""

import json
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
SELF_HOSTED = ["self-hosted", "Linux", "X64", "ov-ci-runner"]
HEAVY_BUILDS = [
    ("_build.yml", "build-studio", {}),
    ("_build.yml", "build-linux", {"arch": "x86_64"}),
    ("api_test.yml", "native-runtime", {}),
    ("rust-cli.yml", "build", {"npm_os": "linux", "os": "ubuntu-24.04"}),
]


def jobs(file):
    return yaml.safe_load((WORKFLOWS / file).read_text())["jobs"]


def labels(expression, row, repository="volcengine/OpenViking", source=None):
    """Evaluate only the routing expressions in these checked-in workflows."""
    expression = expression.strip().removeprefix("${{").removesuffix("}}")
    expression = expression.replace(
        "github.event.pull_request.head.repo.full_name", repr(source)
    ).replace("github.event.pull_request", repr(source is not None))
    expression = expression.replace("github.repository", repr(repository))
    expression = re.sub(r"matrix\.(\w+)", lambda match: repr(row[match[1]]), expression)
    expression = expression.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    result = eval(f"({expression})", {"__builtins__": {}, "fromJSON": json.loads})
    return result if isinstance(result, list) else [result]


@pytest.mark.parametrize("file,name,row", HEAVY_BUILDS)
@pytest.mark.parametrize(
    "repository,source,expected",
    [
        ("volcengine/OpenViking", None, SELF_HOSTED),
        ("volcengine/OpenViking", "volcengine/OpenViking", SELF_HOSTED),
        ("volcengine/OpenViking", "other/OpenViking", ["ubuntu-24.04"]),
        ("other/OpenViking", None, ["ubuntu-24.04"]),
    ],
)
def test_heavy_builds_use_self_hosted_only_for_official_sources(
    file, name, row, repository, source, expected
):
    assert labels(jobs(file)[name]["runs-on"], row, repository, source) == expected


def test_only_heavy_build_jobs_reference_self_hosted():
    references = {
        (path.name, name)
        for path in WORKFLOWS.glob("*.yml")
        for name, job in jobs(path.name).items()
        if "self-hosted" in str(job.get("runs-on", ""))
    }
    assert references == {(file, name) for file, name, _ in HEAVY_BUILDS}


def test_wheels_keep_glibc_baseline_and_native_arm_runner():
    wheel = jobs("_build.yml")["build-linux"]
    assert wheel["container"] == "ubuntu:20.04"
    assert labels(wheel["runs-on"], {"arch": "aarch64"}) == ["ubuntu-24.04-arm"]


def test_musl_targets_use_self_hosted_and_other_platforms_keep_their_runners():
    build = jobs("rust-cli.yml")["build"]
    targets = build["strategy"]["matrix"]["include"]
    assert {row["target"] for row in targets if row["npm_os"] == "linux"} == {
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
    }
    for row in targets:
        expected = SELF_HOSTED if row["npm_os"] == "linux" else [row["os"]]
        assert labels(build["runs-on"], row) == expected


def test_heavy_build_containers_restore_workspace_ownership():
    for file, name, _ in HEAVY_BUILDS:
        job = jobs(file)[name]
        assert "ubuntu:" in job["container"]
        assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in job["steps"])
