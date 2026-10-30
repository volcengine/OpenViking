"""Offline regression coverage for fixed Linux routing and job isolation."""

import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "linux_runner", ROOT / ".github/scripts/linux_runner.py"
)
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


@pytest.mark.parametrize("pool", router.POOLS)
def test_untrusted_sources_use_hosted_for_both_lanes_even_when_forced(pool):
    assert router.select(pool, trusted=False)[:2] == (router.HOSTED, router.HOSTED)


@pytest.mark.parametrize("pool", ["balanced", "auto"])
def test_workload_routing_requires_no_inventory_or_runner_environment(pool):
    assert router.select(pool)[:2] == (router.SELF_HOSTED, router.HOSTED)


@pytest.mark.parametrize(
    "pool,labels",
    [("github-hosted", router.HOSTED), ("ov-ci-runner", router.SELF_HOSTED)],
)
def test_explicit_override_routes_both_lanes_to_one_pool(pool, labels):
    assert router.select(pool)[:2] == (labels, labels)


@pytest.mark.parametrize("pool", ["typo", "openviking-ecs"])
def test_invalid_or_retired_pool_fails_clearly(pool):
    with pytest.raises(ValueError, match="Unknown Linux test pool"):
        router.select(pool)


@pytest.mark.parametrize(
    "repository,source,approved,expected",
    [
        ("volcengine/OpenViking", "volcengine/OpenViking", [], True),
        ("volcengine/OpenViking", "zhanglistar/OpenViking", ["zhanglistar/OpenViking"], True),
        ("volcengine/OpenViking", "zhanglistar/OpenViking", [], False),
        ("volcengine/OpenViking", "zhanglistar/other", ["zhanglistar/OpenViking"], False),
        ("volcengine/OpenViking", "other/OpenViking", ["zhanglistar/OpenViking"], False),
        ("volcengine/OpenViking", None, ["zhanglistar/OpenViking"], False),
        ("other/OpenViking", "zhanglistar/OpenViking", ["zhanglistar/OpenViking"], False),
    ],
)
def test_only_exact_approved_pr_sources_are_trusted(repository, source, approved, expected):
    event = {"pull_request": {"head": {"repo": {"full_name": source} if source else None}}}
    assert router.trusted_source(repository, event, approved) is expected


def test_official_push_remains_trusted_without_fork_configuration():
    assert router.trusted_source("volcengine/OpenViking", {}) is True
    assert router.trusted_source("other/OpenViking", {}) is False


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def embedded_selector():
    config = workflow("_linux-runner.yml")
    job = config["jobs"]["select"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["container"] == "python:3.12-slim"
    assert all("uses" not in step for step in job["steps"])
    assert "secrets" not in config[True]["workflow_call"]
    assert set(config[True]["workflow_call"]["outputs"]) == {"runs_on", "hosted_runs_on"}
    step = job["steps"][0]
    assert step["working-directory"] == "/"
    assert step["env"] == {
        "TRUSTED_PR_REPOSITORIES": "${{ vars.CI_LINUX_TRUSTED_REPOSITORIES }}",
        "POOL": "${{ inputs.pool || vars.CI_LINUX_POOL || 'auto' }}",
    }
    command, script = step["run"].split("\n", 1)
    assert command == "python3 -I - --pool \"$POOL\" <<'PY'"
    assert script.endswith("PY\n")
    return script.removesuffix("PY\n")


def test_selector_embeds_the_tested_script_without_checkout():
    assert embedded_selector() == (ROOT / ".github/scripts/linux_runner.py").read_text()


@pytest.mark.parametrize("pool", router.POOLS)
@pytest.mark.parametrize(
    "source,approved,trusted",
    [
        ("volcengine/OpenViking", "", True),
        ("zhanglistar/OpenViking", '["zhanglistar/OpenViking"]', True),
        ("zhanglistar/OpenViking", "", False),
        ("zhanglistar/OpenViking", "[]", False),
        ("zhanglistar/OpenViking", '["other/OpenViking"]', False),
        ("zhanglistar/OpenViking", '"zhanglistar/OpenViking"', False),
        ("zhanglistar/OpenViking", '{"zhanglistar/OpenViking": true}', False),
        ("zhanglistar/OpenViking", '["zhanglistar/OpenViking", null]', False),
        ("zhanglistar/OpenViking", "not-json", False),
        (None, '["zhanglistar/OpenViking"]', False),
    ],
)
def test_embedded_cli_routes_both_lanes_and_ignores_workspace_imports(
    tmp_path, pool, source, approved, trusted
):
    (tmp_path / "json.py").write_text("raise RuntimeError('untrusted workspace was imported')\n")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"head": {"repo": {"full_name": source} if source else None}}})
    )
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    result = subprocess.run(
        [sys.executable, "-I", "-", "--pool", pool],
        input=embedded_selector(),
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_REPOSITORY": "volcengine/OpenViking",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "PYTHONPATH": str(tmp_path),
            "TRUSTED_PR_REPOSITORIES": approved,
        },
        capture_output=True,
        check=True,
    )
    primary, secondary, reason = router.select(pool, trusted)
    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {
        "runs_on": json.dumps(primary),
        "hosted_runs_on": json.dumps(secondary),
    }
    assert reason in result.stdout
    assert reason in summary.read_text()


def default_matrix(config, job):
    """Expand default matrix slots, including entries added with include."""
    matrix = job.get("strategy", {}).get("matrix", {})
    axes = {key: value for key, value in matrix.items() if key not in ("include", "exclude")}
    if not axes:
        return matrix.get("include") or [{}]
    defaults = (config[True].get("workflow_call") or {}).get("inputs", {})
    for key, value in axes.items():
        if isinstance(value, str):
            if key == "os":
                axes[key] = json.loads(
                    defaults.get("os_json", {}).get("default", '["ubuntu-24.04"]')
                )
            elif key == "python-version":
                axes[key] = json.loads(defaults["python_json"]["default"])
            elif key == "arch":
                # Only x64 wheels participate; native ARM remains on its hosted runner.
                axes[key] = ["x86_64"]
            else:
                raise AssertionError(f"Unhandled dynamic matrix axis: {key}")
    rows = (
        [dict(zip(axes, values, strict=True)) for values in itertools.product(*axes.values())]
        if axes
        else []
    )
    for extra in matrix.get("include", []):
        matched = False
        for row in rows:
            if all(key not in axes or row[key] == value for key, value in extra.items()):
                row.update(extra)
                matched = True
        if not matched:
            rows.append(extra)
    excludes = matrix.get("exclude", [])
    return [
        row
        for row in (rows or [{}])
        if not any(all(row.get(key) == value for key, value in rule.items()) for rule in excludes)
    ]


def labels_for_slot(expression, matrix, pool="auto", trusted=True):
    """Evaluate the limited routing expressions against concrete matrix entries."""
    primary, secondary, _ = router.select(pool, trusted)
    expression = expression.removeprefix("${{ ").removesuffix(" }}")
    expression = expression.replace(
        "needs.linux-runner.outputs.hosted_runs_on", repr(json.dumps(secondary))
    ).replace("needs.linux-runner.outputs.runs_on", repr(json.dumps(primary)))
    expression = re.sub(
        r"matrix\.([\w-]+)", lambda match: repr(matrix.get(match[1], "")), expression
    )
    expression = expression.replace("&&", " and ").replace("||", " or ")
    # Only repository routing expressions are evaluated, with no Python builtins.
    return eval(expression, {"__builtins__": {}, "fromJSON": json.loads})


def linux_slots(pool="auto", trusted=True):
    slots = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        config = workflow(path.name)
        for name, job in config.get("jobs", {}).items():
            expression = job.get("runs-on", "")
            if "needs.linux-runner.outputs." not in expression:
                continue
            for row in default_matrix(config, job):
                labels = labels_for_slot(expression, row, pool, trusted)
                if isinstance(labels, list) and labels in (router.SELF_HOSTED, router.HOSTED):
                    slots.append((path.name, name, row, labels))
    return slots


@pytest.mark.parametrize("pool", ["auto", "balanced"])
def test_only_heavy_build_slots_use_self_hosted(pool):
    slots = linux_slots(pool)
    heavy_slots = [
        (file, name, row) for file, name, row, labels in slots if labels == router.SELF_HOSTED
    ]
    assert len(heavy_slots) == 5
    assert {(file, name) for file, name, _ in heavy_slots} == {
        ("_build.yml", "build-studio"),
        ("_build.yml", "build-linux"),
        ("api_test.yml", "native-runtime"),
        ("rust-cli.yml", "build"),
    }
    assert {row["target"] for file, _, row in heavy_slots if file == "rust-cli.yml"} == {
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
    }
    assert all(row["arch"] == "x86_64" for file, name, row in heavy_slots if name == "build-linux")


def test_tests_checks_and_source_distribution_all_use_hosted():
    heavy_jobs = {
        ("_build.yml", "build-studio"),
        ("_build.yml", "build-linux"),
        ("api_test.yml", "native-runtime"),
        ("rust-cli.yml", "build"),
    }
    light_slots = [slot for slot in linux_slots() if (slot[0], slot[1]) not in heavy_jobs]
    assert light_slots
    assert all(labels == router.HOSTED for *_, labels in light_slots)
    assert {
        ("api_test.yml", "api-tests"),
        ("gateway.yml", "test"),
        ("gateway-e2e.yml", "kind"),
        ("pr.yml", "sdk-python-tests"),
        ("hermes-plugin-tests.yml", "quick-local-platforms"),
        ("_test_full.yml", "test"),
        ("_build.yml", "build-sdist"),
    } <= {(file, name) for file, name, *_ in light_slots}


@pytest.mark.parametrize("pool", router.POOLS)
def test_no_untrusted_matrix_slot_can_select_self_hosted(pool):
    slots = linux_slots(pool, trusted=False)
    assert slots
    assert all(labels == router.HOSTED for *_, labels in slots)


@pytest.mark.parametrize(
    "pool,expected",
    [("github-hosted", router.HOSTED), ("ov-ci-runner", router.SELF_HOSTED)],
)
def test_override_reaches_every_linux_matrix_slot(pool, expected):
    slots = linux_slots(pool)
    assert slots
    assert all(labels == expected for *_, labels in slots)


@pytest.mark.parametrize(
    "file,jobs",
    [
        ("pr.yml", ["plugin-tests", "openclaw-tests", "sdk-python-tests", "langchain-tests"]),
        ("api_test.yml", ["native-runtime", "api-tests"]),
        ("_test_lite.yml", ["test-lite"]),
        ("_test_full.yml", ["test"]),
        ("hermes-plugin-tests.yml", ["hermes-plugin-tests", "quick-local-platforms"]),
        ("rust-cli.yml", ["test"]),
        ("framework-layout.yml", ["layout"]),
        ("python-agent-framework.yml", ["contracts"]),
        ("host-compat.yml", ["check"]),
        ("gateway-chart.yml", ["chart"]),
        ("gateway.yml", ["test"]),
        ("api_test_effect.yml", ["effect-tests"]),
    ],
)
def test_linux_jobs_route_and_bootstrap_git_before_checkout(file, jobs):
    config = workflow(file)["jobs"]
    assert config["linux-runner"]["uses"] == "./.github/workflows/_linux-runner.yml"
    for name in jobs:
        job = config[name]
        assert "linux-runner" in job["needs"]
        assert "needs.linux-runner.outputs." in job["runs-on"]
        assert "ubuntu:24.04" in str(job["container"])
        steps = job["steps"]
        checkout = next(
            i for i, step in enumerate(steps) if "actions/checkout@" in step.get("uses", "")
        )
        assert any(
            "apt-get" in step.get("run", "") and "git" in step["run"] for step in steps[:checkout]
        )
        assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in steps)


def test_no_workflow_depends_on_inventory_secret_or_selector_override():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        text = path.read_text()
        assert "CI_RUNNER_READ_TOKEN" not in text
        assert "CI_LINUX_SELECTOR_RUNS_ON" not in text


def test_rust_linux_builds_use_primary_lane_and_isolated_container():
    build = workflow("rust-cli.yml")["jobs"]["build"]
    assert build["needs"] == "linux-runner"
    assert build["runs-on"] == (
        "${{ matrix.npm_os == 'linux' && "
        "fromJSON(needs.linux-runner.outputs.runs_on) || matrix.os }}"
    )
    assert build["container"] == "${{ matrix.npm_os == 'linux' && 'ubuntu:24.04' || null }}"
    targets = build["strategy"]["matrix"]["include"]
    assert {row["target"] for row in targets if row["npm_os"] == "linux"} == {
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
    }
    for row in targets:
        if row["npm_os"] != "linux":
            assert labels_for_slot(build["runs-on"], row) == row["os"]
    steps = build["steps"]
    checkout = next(
        i for i, step in enumerate(steps) if "actions/checkout@" in step.get("uses", "")
    )
    bootstrap = next(
        i for i, step in enumerate(steps) if step.get("uses") == "./.github/actions/linux-test-env"
    )
    node = next(i for i, step in enumerate(steps) if "actions/setup-node@" in step.get("uses", ""))
    rust = next(
        i for i, step in enumerate(steps) if "dtolnay/rust-toolchain@" in step.get("uses", "")
    )
    assert checkout < bootstrap < node < rust
    for step in [*steps[:checkout], steps[bootstrap], steps[node]]:
        assert step["if"] == "matrix.npm_os == 'linux'"


def test_distribution_routing_preserves_wheel_libc_and_native_arm_host():
    jobs = workflow("_build.yml")["jobs"]
    assert jobs["linux-runner"]["uses"] == "./.github/workflows/_linux-runner.yml"
    wheel = jobs["build-linux"]
    assert wheel["needs"] == ["build-studio", "linux-runner"]
    assert wheel["container"] == "ubuntu:20.04"
    assert "(glibc 2.31)" in wheel["name"]
    assert labels_for_slot(wheel["runs-on"], {"arch": "x86_64"}) == router.SELF_HOSTED
    assert labels_for_slot(wheel["runs-on"], {"arch": "aarch64"}) == ["ubuntu-24.04-arm"]
    steps = wheel["steps"]
    checkout = next(
        i for i, step in enumerate(steps) if "actions/checkout@" in step.get("uses", "")
    )
    bootstrap = next(
        i for i, step in enumerate(steps) if step.get("uses") == "./.github/actions/linux-test-env"
    )
    rust = next(
        i for i, step in enumerate(steps) if "dtolnay/rust-toolchain@" in step.get("uses", "")
    )
    assert checkout < bootstrap < rust
    assert steps[bootstrap]["with"]["install-dependencies"] == "false"
    for name in ["build-studio", "build-sdist"]:
        job = jobs[name]
        assert job["needs"] == "linux-runner"
        assert job["container"] == "ubuntu:24.04"
        assert "git" in job["steps"][0]["run"]
        assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in job["steps"])


@pytest.mark.parametrize("caller", ["pr.yml", "release.yml"])
def test_distribution_callers_forward_manual_pool_override(caller):
    build = workflow(caller)["jobs"]["build"]
    assert build["with"]["linux_pool"] == "${{ inputs.linux_pool || '' }}"


def test_gateway_resources_are_isolated():
    gateway = workflow("gateway.yml")["jobs"]["test"]
    assert "ports" not in gateway["services"]["redis"]
    assert gateway["steps"][-1]["if"] == "always()"
    kind = workflow("gateway-e2e.yml")["jobs"]["kind"]
    assert "github.run_id" in kind["env"]["KIND_CLUSTER"]
    assert "github.run_attempt" in kind["env"]["KIND_CLUSTER"]
    assert kind["steps"][-1]["if"] == "always()"


def test_pool_helpers_retain_native_ci_coverage():
    spec = importlib.util.spec_from_file_location("ci_plan", ROOT / ".github/scripts/ci_plan.py")
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    for path in [
        ".github/scripts/linux_runner.py",
        ".github/workflows/_linux-runner.yml",
        ".github/actions/linux-test-env/action.yml",
    ]:
        selected = planner.plan([path])
        assert selected["wheel_os"] == planner.FULL_OS
        assert selected["api_changed"] and selected["cuvs_changed"]
