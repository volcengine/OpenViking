"""Offline regression coverage for runner inventory, routing and isolation."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "linux_runner", ROOT / ".github/scripts/linux_runner.py"
)
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


def runner(pool="ov-ci-runner", status="online", busy=False, labels=None):
    return {
        "name": pool,
        "status": status,
        "busy": busy,
        "labels": [{"name": name} for name in (labels or ["self-hosted", "Linux", "X64", pool])],
    }


@pytest.mark.parametrize("pool", ["auto", "github-hosted", *router.POOLS])
def test_untrusted_forks_use_hosted_even_when_pool_forced(pool):
    assert (
        router.select(pool, [runner()], trusted=False, selector_runner="ov-ci-runner")[0]
        == router.HOSTED
    )


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


@pytest.mark.parametrize(
    "inventory",
    [
        None,
        [],
        [runner(status="offline")],
        [runner(busy=True)],
        [runner(labels=["self-hosted", "Linux", "ARM64", "ov-ci-runner"])],
        [runner(labels=["self-hosted", "Linux", "X64"])],
        [runner(labels=["self-hosted", "Windows", "X64", "ov-ci-runner"])],
        [runner("retired-host")],
    ],
)
def test_auto_falls_back_when_no_eligible_idle_runner(inventory):
    assert router.select("auto", inventory)[0] == router.HOSTED


def test_auto_selects_remaining_pool_and_ignores_inventory_order():
    inventory = [runner("retired-host"), runner(), runner()]
    assert router.select("auto", inventory)[0][-1] == "ov-ci-runner"
    assert router.select("auto", inventory[::-1])[0][-1] == "ov-ci-runner"


@pytest.mark.parametrize("pool", router.POOLS)
def test_selector_can_choose_its_own_busy_host_after_finishing(pool):
    inventory = [runner("retired-host", busy=True), runner("ov-ci-runner", busy=True)]
    assert router.select("auto", inventory, selector_runner=pool)[0][-1] == pool
    assert router.select("auto", inventory, selector_runner="unrelated-host")[0] == router.HOSTED


def test_selector_does_not_choose_its_own_offline_host():
    inventory = [runner(status="offline", busy=True)]
    assert router.select("auto", inventory, selector_runner="ov-ci-runner")[0] == router.HOSTED


@pytest.mark.parametrize("pool", router.POOLS)
def test_auto_uses_prepared_selector_host_without_inventory_credentials(pool):
    labels, reason = router.select("auto", None, selector_runner=pool)
    assert labels == ["self-hosted", "Linux", "X64", pool]
    assert "Inventory credentials absent" in reason
    assert router.select("auto", [], selector_runner=pool)[0] == router.HOSTED


def test_auto_without_inventory_does_not_guess_unknown_selector_pool():
    assert router.select("auto", None, selector_runner="unrelated-host")[0] == router.HOSTED


def test_label_matching_is_case_insensitive():
    inventory = [runner(labels=["SELF-HOSTED", "linux", "x64", "OV-CI-RUNNER"])]
    assert router.select("auto", inventory)[0][-1] == "ov-ci-runner"


def test_forced_pool_does_not_choose_another_self_hosted_pool():
    assert router.select("ov-ci-runner", [runner("retired-host")])[0] == router.HOSTED
    assert router.select("github-hosted", [runner()])[0] == router.HOSTED


def test_explicit_pool_can_be_used_without_inventory_token():
    labels, reason = router.select("ov-ci-runner", None)
    assert labels == ["self-hosted", "Linux", "X64", "ov-ci-runner"]
    assert "wait for capacity" in reason


def test_invalid_configuration_fails_clearly():
    with pytest.raises(ValueError, match="Unknown Linux test pool"):
        router.select("typo", [])


def test_inventory_paginates_and_sets_scoped_headers(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        assert timeout == 10
        batch = [runner()] * 100 if len(requests) == 1 else [runner("ov-ci-runner")]
        return io.BytesIO(json.dumps({"runners": batch}).encode())

    monkeypatch.setattr(router.urllib.request, "urlopen", urlopen)
    result = router.inventory("https://api.github.com", "volcengine/OpenViking", "test-token")
    assert len(result) == 101
    assert requests[1].full_url.endswith("per_page=100&page=2")
    assert requests[0].get_header("Authorization") == "Bearer test-token"


@pytest.mark.parametrize("fork", [False, True, None])
def test_cli_inventory_failure_falls_back_without_leaking_token(
    tmp_path, monkeypatch, capsys, fork
):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "head": {
                        "repo": (
                            None
                            if fork is None
                            else {"full_name": "other/fork" if fork else "volcengine/OpenViking"}
                        )
                    }
                }
            }
        )
    )
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    for key, value in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "volcengine/OpenViking",
        "GITHUB_API_URL": "https://api.github.com",
        "GITHUB_RUN_ID": "42",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "RUNNER_READ_TOKEN": "must-not-be-printed",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []

    def inventory(*args):
        calls.append(args)
        raise urllib.error.HTTPError("url", 403, "must-not-be-printed", {}, None)

    monkeypatch.setattr(router, "inventory", inventory)
    monkeypatch.setattr("sys.argv", ["linux_runner.py", "--pool", "auto"])
    router.main()
    assert len(calls) == (1 if fork is False else 0)
    assert json.loads(output.read_text().split("=", 1)[1]) == router.HOSTED
    assert "must-not-be-printed" not in capsys.readouterr().out + summary.read_text()


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def embedded_selector():
    job = workflow("_linux-runner.yml")["jobs"]["select"]
    assert job["runs-on"] == (
        "${{ fromJSON(github.repository == 'volcengine/OpenViking' && "
        "vars.CI_LINUX_SELECTOR_RUNS_ON || '[\"ubuntu-24.04\"]') }}"
    )
    assert job["container"] == "python:3.12-slim"
    assert all("uses" not in step for step in job["steps"])
    step = job["steps"][0]
    assert step["working-directory"] == "/"
    command, script = step["run"].split("\n", 1)
    assert command == "python3 -I - --pool \"$POOL\" <<'PY'"
    assert script.endswith("PY\n")
    return script.removesuffix("PY\n")


def test_selector_embeds_the_tested_script_without_checkout():
    assert embedded_selector() == (ROOT / ".github/scripts/linux_runner.py").read_text()


def test_embedded_fork_selector_ignores_workspace_imports_and_pythonpath(tmp_path):
    (tmp_path / "json.py").write_text("raise RuntimeError('untrusted workspace was imported')\n")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": "other/fork"}}}}))
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    env = {
        **os.environ,
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "volcengine/OpenViking",
        "GITHUB_RUN_ID": "42",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
        "PYTHONPATH": str(tmp_path),
        "RUNNER_READ_TOKEN": "",
        "TRUSTED_PR_REPOSITORIES": "[]",
        "RUNNER_NAME": "ov-ci-runner",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-", "--pool", "ov-ci-runner"],
        input=embedded_selector(),
        text=True,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=True,
    )
    assert json.loads(output.read_text().split("=", 1)[1]) == router.HOSTED
    assert "Untrusted PRs" in result.stdout


@pytest.mark.parametrize("selector_host", [*router.POOLS, "GitHub Actions 1"])
@pytest.mark.parametrize("source", ["volcengine/OpenViking", "zhanglistar/OpenViking"])
@pytest.mark.parametrize(
    "approved,expected_trusted",
    [
        ("", False),
        ('["zhanglistar/OpenViking"]', True),
        ("[]", False),
        ('["other/OpenViking"]', False),
        ('"zhanglistar/OpenViking"', False),
        ('{"zhanglistar/OpenViking": true}', False),
        ('["zhanglistar/OpenViking", null]', False),
        ("not-json", False),
    ],
)
def test_embedded_pr_shares_hosts_only_for_trusted_sources(
    tmp_path, selector_host, source, approved, expected_trusted
):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"head": {"repo": {"full_name": source}}}}))
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    result = subprocess.run(
        [sys.executable, "-I", "-", "--pool", "auto"],
        input=embedded_selector(),
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_REPOSITORY": "volcengine/OpenViking",
            "GITHUB_RUN_ID": "42",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_NAME": selector_host,
            "RUNNER_READ_TOKEN": "",
            "TRUSTED_PR_REPOSITORIES": approved,
        },
        capture_output=True,
        check=True,
    )
    trusted = source == "volcengine/OpenViking" or expected_trusted
    expected = (
        ["self-hosted", "Linux", "X64", selector_host]
        if trusted and selector_host in router.POOLS
        else router.HOSTED
    )
    assert json.loads(output.read_text().split("=", 1)[1]) == expected
    if not trusted:
        reason = "Untrusted PRs"
    elif selector_host in router.POOLS:
        reason = "Inventory credentials absent"
    else:
        reason = "inventory unavailable"
    assert reason in result.stdout
    assert reason in summary.read_text()


def test_workflow_passes_only_configured_fork_trust():
    env = workflow("_linux-runner.yml")["jobs"]["select"]["steps"][0]["env"]
    assert env["TRUSTED_PR_REPOSITORIES"] == "${{ vars.CI_LINUX_TRUSTED_REPOSITORIES }}"


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
        assert "needs.linux-runner.outputs.runs_on" in job["runs-on"]
        assert "ubuntu:24.04" in str(job["container"])
        steps = job["steps"]
        checkout = next(
            i for i, step in enumerate(steps) if "actions/checkout@" in step.get("uses", "")
        )
        assert any(
            "apt-get" in step.get("run", "") and "git" in step["run"] for step in steps[:checkout]
        )
        assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in steps)


def test_rust_linux_builds_use_selected_pool_and_isolated_container():
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
    assert any(
        "apt-get" in step.get("run", "") and "git" in step["run"] for step in steps[:checkout]
    )


def test_distribution_builds_route_x64_without_changing_wheel_libc_or_arm_host():
    jobs = workflow("_build.yml")["jobs"]
    assert jobs["linux-runner"]["uses"] == "./.github/workflows/_linux-runner.yml"
    wheel = jobs["build-linux"]
    assert wheel["needs"] == ["build-studio", "linux-runner"]
    assert wheel["runs-on"] == (
        "${{ matrix.arch == 'aarch64' && fromJSON('[\"ubuntu-24.04-arm\"]') || "
        "fromJSON(needs.linux-runner.outputs.runs_on) }}"
    )
    assert wheel["container"] == "ubuntu:20.04"
    assert "(glibc 2.31)" in wheel["name"]
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
    assert any(
        "apt-get" in step.get("run", "") and "git" in step["run"] for step in steps[:checkout]
    )
    for name in ["build-studio", "build-sdist"]:
        job = jobs[name]
        assert job["needs"] == "linux-runner"
        assert job["runs-on"] == "${{ fromJSON(needs.linux-runner.outputs.runs_on) }}"
        assert job["container"] == "ubuntu:24.04"
        assert "git" in job["steps"][0]["run"]
        assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in job["steps"])


@pytest.mark.parametrize("caller", ["pr.yml", "release.yml"])
def test_distribution_callers_forward_pool_override_and_inventory_secret(caller):
    build = workflow(caller)["jobs"]["build"]
    assert build["with"]["linux_pool"] == "${{ inputs.linux_pool || '' }}"
    assert build["secrets"]["CI_RUNNER_READ_TOKEN"] == "${{ secrets.CI_RUNNER_READ_TOKEN }}"


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
