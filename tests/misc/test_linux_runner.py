"""Offline regression coverage for runner inventory, routing and isolation."""

import importlib.util
import io
import json
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


def runner(pool="openviking-ecs", status="online", busy=False, labels=None):
    return {
        "status": status,
        "busy": busy,
        "labels": [{"name": name} for name in (labels or ["self-hosted", "Linux", "X64", pool])],
    }


@pytest.mark.parametrize("pool", ["auto", "github-hosted", *router.POOLS])
def test_forks_always_use_hosted_even_when_pool_forced(pool):
    assert router.select(pool, [runner()], trusted=False)[0] == router.HOSTED


@pytest.mark.parametrize(
    "inventory",
    [
        None,
        [],
        [runner(status="offline")],
        [runner(busy=True)],
        [runner(labels=["self-hosted", "Linux", "ARM64", "openviking-ecs"])],
        [runner(labels=["self-hosted", "Linux", "X64"])],
        [runner(labels=["self-hosted", "Windows", "X64", "openviking-ecs"])],
    ],
)
def test_auto_falls_back_when_no_eligible_idle_runner(inventory):
    assert router.select("auto", inventory)[0] == router.HOSTED


def test_auto_rotates_idle_pools_and_ignores_inventory_order():
    inventory = [runner("ov-ci-runner"), runner(), runner()]
    assert router.select("auto", inventory, run_id=2)[0][-1] == "openviking-ecs"
    assert router.select("auto", inventory, run_id=3)[0][-1] == "ov-ci-runner"
    assert router.select("auto", inventory[::-1], run_id=3)[0][-1] == "ov-ci-runner"


def test_label_matching_is_case_insensitive():
    inventory = [runner(labels=["SELF-HOSTED", "linux", "x64", "OV-CI-RUNNER"])]
    assert router.select("auto", inventory)[0][-1] == "ov-ci-runner"


def test_forced_pool_does_not_choose_another_self_hosted_pool():
    assert router.select("ov-ci-runner", [runner()])[0] == router.HOSTED
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


def test_p0_is_pinned_and_gateway_resources_are_isolated():
    p0 = workflow("oc2ov_test.yml")["jobs"]["p0-tests"]
    assert p0["runs-on"] == ["self-hosted", "Linux", "X64", "openviking-ecs"]
    assert "container" not in p0
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
