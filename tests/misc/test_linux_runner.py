"""Check fixed build routing, fork isolation and platform compatibility."""

import json
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
SELF_HOSTED = ["self-hosted", "Linux", "X64", "ov-ci-runner"]


def jobs(file):
    return yaml.safe_load((WORKFLOWS / file).read_text())["jobs"]


def labels(
    expression,
    row,
    repository="volcengine/OpenViking",
    source=None,
    event="push",
    ref="refs/heads/main",
    disabled="",
):
    """Evaluate only the routing expressions in these checked-in workflows."""
    expression = expression.strip().removeprefix("${{").removesuffix("}}")
    expression = expression.replace(
        "github.event.pull_request.head.repo.full_name", repr(source)
    ).replace("github.event.pull_request", repr(source is not None))
    expression = expression.replace("github.repository", repr(repository))
    for key, value in {
        "github.event_name": event,
        "github.ref": ref,
        "vars.CI_DISABLE_SELF_HOSTED": disabled,
    }.items():
        expression = expression.replace(key, repr(value))
    expression = re.sub(r"matrix\.(\w+)", lambda match: repr(row[match[1]]), expression)
    expression = re.sub(r"!(?!=)", " not ", expression.replace("&&", " and ").replace("||", " or "))
    result = eval(
        f"({expression})",
        {"__builtins__": {}, "fromJSON": json.loads, "startsWith": str.startswith},
    )
    return result if isinstance(result, list) else [result]


@pytest.mark.parametrize(
    "repository,source,expected",
    [
        ("volcengine/OpenViking", None, SELF_HOSTED),
        ("volcengine/OpenViking", "volcengine/OpenViking", SELF_HOSTED),
        ("volcengine/OpenViking", "other/OpenViking", ["ubuntu-24.04"]),
        ("other/OpenViking", None, ["ubuntu-24.04"]),
    ],
)
def test_x64_wheels_use_self_hosted_only_for_official_sources(repository, source, expected):
    event = "pull_request" if source else "push"
    assert (
        labels(
            jobs("_build.yml")["build-linux"]["runs-on"],
            {"arch": "x86_64"},
            repository,
            source,
            event,
        )
        == expected
    )


@pytest.mark.parametrize(
    "event,ref,disabled,expected",
    [
        ("release", "refs/tags/v1.0.0", "", ["ubuntu-24.04"]),
        ("push", "refs/tags/v1.0.0", "", ["ubuntu-24.04"]),
        ("release", "refs/heads/main", "", ["ubuntu-24.04"]),
        ("workflow_dispatch", "refs/heads/main", "", ["ubuntu-24.04"]),
        ("schedule", "refs/heads/main", "", ["ubuntu-24.04"]),
        ("push", "refs/heads/main", "true", ["ubuntu-24.04"]),
        ("pull_request", "refs/pull/1/merge", "true", ["ubuntu-24.04"]),
        ("pull_request", "refs/pull/1/merge", "", SELF_HOSTED),
    ],
)
def test_release_routing_and_emergency_hosted_override(event, ref, disabled, expected):
    assert (
        labels(
            jobs("_build.yml")["build-linux"]["runs-on"],
            {"arch": "x86_64"},
            source="volcengine/OpenViking",
            event=event,
            ref=ref,
            disabled=disabled,
        )
        == expected
    )


def test_only_linux_wheels_reference_self_hosted():
    references = {
        (path.name, name)
        for path in WORKFLOWS.glob("*.yml")
        for name, job in jobs(path.name).items()
        if "self-hosted" in str(job.get("runs-on", ""))
    }
    assert references == {("_build.yml", "build-linux")}


def test_wheels_keep_glibc_baseline_and_native_arm_runner():
    wheel = jobs("_build.yml")["build-linux"]
    assert wheel["container"] == "ubuntu:20.04"
    assert labels(wheel["runs-on"], {"arch": "aarch64"}) == ["ubuntu-24.04-arm"]


def test_studio_and_api_native_runtime_stay_hosted():
    for file, name in [("_build.yml", "build-studio"), ("api_test.yml", "native-runtime")]:
        job = jobs(file)[name]
        assert job["runs-on"] == "ubuntu-24.04"
        assert "container" not in job
        assert all(step.get("uses") != "./.github/actions/linux-test-env" for step in job["steps"])


def test_all_musl_targets_stay_hosted():
    build = jobs("rust-cli.yml")["build"]
    targets = build["strategy"]["matrix"]["include"]
    assert {row["target"] for row in targets if row["npm_os"] == "linux"} == {
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
    }
    for row in targets:
        assert labels(build["runs-on"], row) == [row["os"]]
    assert "container" not in build


def test_wheel_container_restores_workspace_ownership():
    job = jobs("_build.yml")["build-linux"]
    assert job["container"] == "ubuntu:20.04"
    assert 0 < job["timeout-minutes"] <= 60
    assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in job["steps"])
