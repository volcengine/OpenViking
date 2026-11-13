"""Check half-load build/test routing, fork isolation and platform compatibility."""

import json
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
SELF_HOSTED = ["self-hosted", "Linux", "X64", "ci-linux-builds"]


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
    enabled="true",
    run_number=2,
    contexts=None,
):
    """Evaluate only the routing expressions in these checked-in workflows."""
    expression = expression.strip().removeprefix("${{").removesuffix("}}")
    for key, value in (contexts or {}).items():
        expression = expression.replace(key, repr(value))
    expression = expression.replace(
        "github.event.pull_request.head.repo.full_name", repr(source)
    ).replace("github.event.pull_request", repr(source is not None))
    expression = expression.replace("github.repository", repr(repository))
    for key, value in {
        "github.event_name": event,
        "github.ref": ref,
        "vars.CI_DISABLE_SELF_HOSTED": disabled,
        "vars.CI_LINUX_BUILDS_ENABLED": enabled,
        "github.run_number": run_number,
    }.items():
        expression = expression.replace(key, repr(value))
    expression = re.sub(r"matrix\.(\w+)", lambda match: repr(row[match[1]]), expression)
    expression = re.sub(r"!(?!=)", " not ", expression.replace("&&", " and ").replace("||", " or "))
    result = eval(
        f"({expression})",
        {
            "__builtins__": {},
            "fromJSON": json.loads,
            "startsWith": str.startswith,
            "endsWith": lambda value, suffix: str(value).endswith(suffix),
        },
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


def test_only_selected_builds_and_explicit_diagnostic_reference_self_hosted():
    references = {
        (path.name, name)
        for path in WORKFLOWS.glob("*.yml")
        for name, job in jobs(path.name).items()
        if "self-hosted" in str(job.get("runs-on", ""))
    }
    assert references == {
        ("_build.yml", "build-linux"),
        ("_build.yml", "check-linux-runner"),
        ("api_test.yml", "prepare"),
        ("api_test.yml", "native-runtime"),
    }


def test_wheels_keep_glibc_baseline_and_native_arm_runner():
    wheel = jobs("_build.yml")["build-linux"]
    assert wheel["container"]["image"] == "ubuntu:20.04"
    assert labels(wheel["runs-on"], {"arch": "aarch64"}) == ["ubuntu-24.04-arm"]


def test_studio_stays_hosted_and_native_runtime_keeps_ubuntu24_baseline():
    for file, name in [("_build.yml", "build-studio")]:
        job = jobs(file)[name]
        assert job["runs-on"] == "ubuntu-24.04"
        assert "container" not in job
        assert all(step.get("uses") != "./.github/actions/linux-test-env" for step in job["steps"])
    native = jobs("api_test.yml")["native-runtime"]
    assert native["container"]["image"] == "ubuntu:24.04"
    assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in native["steps"])


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
    assert job["container"]["image"] == "ubuntu:20.04"
    assert 0 < job["timeout-minutes"] <= 60
    assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in job["steps"])


@pytest.mark.parametrize(
    "file,name,self_hosted_parity",
    [
        ("_build.yml", "build-linux", 0),
        ("api_test.yml", "prepare", 1),
        ("api_test.yml", "native-runtime", 1),
    ],
)
def test_selected_builds_alternate_half_the_runs(file, name, self_hosted_parity):
    expression = jobs(file)[name]["runs-on"]
    actual = [labels(expression, {"arch": "x86_64"}, run_number=number) for number in range(1, 101)]
    assert actual.count(SELF_HOSTED) == 50
    for number, result in enumerate(actual, 1):
        assert result == (SELF_HOSTED if number % 2 == self_hosted_parity else ["ubuntu-24.04"])


@pytest.mark.parametrize(
    "file,name,run_number",
    [
        ("_build.yml", "build-linux", 2),
        ("api_test.yml", "prepare", 1),
        ("api_test.yml", "native-runtime", 1),
    ],
)
@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "fork/OpenViking", "event": "pull_request"},
        {"repository": "fork/OpenViking"},
        {"disabled": "true"},
        {"enabled": ""},
        {"enabled": "false"},
        {"event": "release", "ref": "refs/tags/v1.0.0"},
        {"event": "push", "ref": "refs/tags/v1.0.0"},
        {"event": "workflow_dispatch"},
        {"event": "schedule"},
    ],
)
def test_half_load_routing_keeps_untrusted_and_non_build_events_hosted(
    file, name, run_number, overrides
):
    assert labels(
        jobs(file)[name]["runs-on"], {"arch": "x86_64"}, run_number=run_number, **overrides
    ) == ["ubuntu-24.04"]


def test_runner_diagnostic_is_explicit_and_does_not_checkout_code():
    diagnostic = jobs("_build.yml")["check-linux-runner"]
    assert diagnostic["runs-on"] == SELF_HOSTED
    assert diagnostic["permissions"] == {}
    assert diagnostic["timeout-minutes"] == 5
    assert "workflow_dispatch" in diagnostic["if"]
    assert "inputs.check_linux_runner" in diagnostic["if"]
    assert all("uses" not in step for step in diagnostic["steps"])


@pytest.mark.parametrize("number", [1, 2])
@pytest.mark.parametrize("source", ["volcengine/OpenViking", "fork/OpenViking"])
def test_api_consumers_inherit_the_trusted_native_producer_pool(number, source):
    workflow = jobs("api_test.yml")
    native = workflow["native-runtime"]
    destination = labels(
        native["runs-on"], {}, source=source, event="pull_request", run_number=number
    )
    environment = "self-hosted" if destination == SELF_HOSTED else "github-hosted"
    pool = next(step for step in native["steps"] if step.get("id") == "test-pool")
    published = labels(
        pool["env"]["TEST_RUNNER"], {}, contexts={"runner.environment": environment}
    )[0]
    assert json.loads(published) == destination
    assert native["outputs"] == {
        "test_runner": "${{ steps.test-pool.outputs.runner }}",
        "test_container": "${{ steps.test-pool.outputs.container }}",
    }
    container = labels(
        pool["env"]["TEST_CONTAINER"], {}, contexts={"runner.environment": environment}
    )[0]
    assert container == ("ubuntu:24.04" if environment == "self-hosted" else "")
    contexts = {
        "needs.native-runtime.outputs.test_runner": json.dumps(destination),
        "needs.native-runtime.outputs.test_container": container,
    }
    for name in ["api-tests", "database-tests"]:
        job = workflow[name]
        assert "native-runtime" in job["needs"]
        assert labels(job["runs-on"], {"os": "ubuntu-24.04"}, contexts=contexts) == destination
        assert labels(job["container"]["image"], {"os": "ubuntu-24.04"}, contexts=contexts) == [
            container
        ]
    cuvs = workflow["cuvs-tests"]
    assert cuvs["with"]["linux_runner_json"] == "${{ needs.native-runtime.outputs.test_runner }}"
    assert cuvs["with"]["linux_container"] == "${{ needs.native-runtime.outputs.test_container }}"
    lite = jobs("_test_lite.yml")["test-lite"]
    lite_contexts = {
        "inputs.linux_runner_json": json.dumps(destination),
        "inputs.linux_container": container,
    }
    assert labels(lite["runs-on"], {"os": "ubuntu-24.04"}, contexts=lite_contexts) == destination
    for os in ["macos-15", "windows-latest"]:
        assert labels(workflow["api-tests"]["runs-on"], {"os": os}, contexts=contexts) == [os]
        assert labels(lite["runs-on"], {"os": os}, contexts=lite_contexts) == [os]
        assert labels(
            workflow["api-tests"]["container"]["image"], {"os": os}, contexts=contexts
        ) == [""]
        assert labels(lite["container"]["image"], {"os": os}, contexts=lite_contexts) == [""]


@pytest.mark.parametrize(
    "file,name",
    [
        ("api_test.yml", "api-tests"),
        ("api_test.yml", "database-tests"),
        ("_test_lite.yml", "test-lite"),
    ],
)
def test_self_hosted_test_containers_bootstrap_and_cleanup(file, name):
    job = jobs(file)[name]
    steps = job["steps"]
    bootstrap = steps[0]
    assert bootstrap["if"] == "runner.environment == 'self-hosted'"
    assert "Acquire::https::CaInfo=/tmp/runner-ca-certificates.crt" in bootstrap["run"]
    assert "APT::Update::Error-Mode=any" in bootstrap["run"]
    assert "git ca-certificates curl python3 cmake build-essential" in bootstrap["run"]
    assert "ca-certificates.crt:/tmp/runner-ca-certificates.crt:ro" in job["container"]["options"]
    cleanup = next(step for step in steps if step.get("uses") == "./.github/actions/linux-test-env")
    assert cleanup["if"] == "runner.environment == 'self-hosted'"
    assert steps.index(cleanup) < next(
        index for index, step in enumerate(steps) if step.get("uses") == "actions/setup-python@v6"
    )


@pytest.mark.parametrize("container,host", [("", "127.0.0.1"), ("ubuntu:24.04", "mysql")])
def test_database_services_use_container_dns_or_host_loopback(container, host):
    job = jobs("api_test.yml")["database-tests"]
    contexts = {"needs.native-runtime.outputs.test_container": container}
    for variable in ["OV_TEST_MYSQL", "OPENVIKING_WATCH_MYSQL_PARAMS"]:
        expression = job["env"][variable].strip().removeprefix("${{").removesuffix("}}")
        # GitHub format uses escaped braces, like Python's format.
        expression = expression.replace("format(", "gh_format(")
        expression = expression.replace(
            "needs.native-runtime.outputs.test_container", repr(container)
        )
        expression = expression.replace("&&", " and ").replace("||", " or ")
        value = eval(
            expression, {"__builtins__": {}, "gh_format": lambda text, value: text.format(value)}
        )
        assert json.loads(value)["host"] == host
    redis = "redis://redis:6379" if container else "redis://127.0.0.1:6379"
    assert labels(job["env"]["OV_TEST_REDIS"], {}, contexts=contexts) == [redis]
    for service, port in [("mysql", "3306"), ("redis", "6379")]:
        assert labels(job["services"][service]["ports"][0], {}, contexts=contexts) == [
            port if container else f"{port}:{port}"
        ]
