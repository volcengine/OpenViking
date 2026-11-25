"""Check dedicated API routing, half-load wheels and platform compatibility."""

import json
import re
import shutil
import subprocess
import sys
from os import environ, pathsep
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"
SELF_HOSTED = ["self-hosted", "Linux", "X64", "ci-linux-builds"]


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("node") is None,
    reason="The Linux CI action requires Node and POSIX command shims",
)
def test_cleanup_recovers_host_owner_when_checkout_recreates_workspace(tmp_path):
    """Execute the action with Linux stat/chown shims for a root-owned checkout."""
    workspace = tmp_path / "OpenViking" / "OpenViking"
    workspace.mkdir(parents=True)
    runner_temp = tmp_path / "_temp"
    runner_temp.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    stat = commands / "stat"
    stat.write_text(
        "#!/bin/sh\n"
        'if [ "$3" = "$EXPECTED_PARENT" ]; then\n'
        '  printf "1001:1002\\n"\n'
        "else\n"
        '  printf "0:0\\n"\n'
        "fi\n"
    )
    chown = commands / "chown"
    chown.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CHOWN_LOG"\n')
    stat.chmod(0o755)
    chown.chmod(0o755)
    state_file = tmp_path / "state"
    env_file = tmp_path / "environment"
    chown_log = tmp_path / "chown.log"
    env = {
        **environ,
        "PATH": f"{commands}{pathsep}{environ['PATH']}",
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_STATE": str(state_file),
        "GITHUB_ENV": str(env_file),
        "EXPECTED_PARENT": str(workspace.parent),
        "CHOWN_LOG": str(chown_log),
    }
    action = WORKFLOWS.parent / "actions/linux-test-env"
    subprocess.run(["node", str(action / "bootstrap.mjs")], env=env, check=True)
    state = dict(line.split("=", 1) for line in state_file.read_text().splitlines())
    assert state["owner"] == "1001:1002"
    home = Path(state["home"])
    assert home.parent == runner_temp
    assert f"HOME={home}\n" in env_file.read_text()
    assert workspace.as_posix() in (home / ".gitconfig").read_text()
    subprocess.run(
        ["node", str(action / "cleanup.mjs")],
        env={**env, **{f"STATE_{key}": value for key, value in state.items()}},
        check=True,
    )
    # Cleanup must restore the host UID/GID and avoid dereferencing symlinks.
    assert chown_log.read_text().splitlines() == ["-hR", "1001:1002", str(workspace), str(home)]


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
    ubuntu24_image="",
    ubuntu20_image="",
    check_linux_runner=False,
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
        "vars.CI_UBUNTU24_IMAGE": ubuntu24_image,
        "vars.CI_UBUNTU20_IMAGE": ubuntu20_image,
        "inputs.check_linux_runner": check_linux_runner,
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
    assert labels(wheel["container"]["image"], {"arch": "x86_64"}) == ["ubuntu:20.04"]
    image = "ghcr.io/volcengine/openviking-ci-ubuntu20@sha256:tested"
    assert labels(wheel["container"]["image"], {"arch": "x86_64"}, ubuntu20_image=image) == [image]
    assert labels(wheel["container"]["image"], {"arch": "aarch64"}, ubuntu20_image=image) == [
        "ubuntu:20.04"
    ]
    assert labels(wheel["runs-on"], {"arch": "aarch64"}) == ["ubuntu-24.04-arm"]


@pytest.mark.skipif(sys.platform == "win32", reason="Linux compiler selection uses bash")
@pytest.mark.parametrize("glibc,expected_status", [("glibc 2.31", 0), ("glibc 2.39", 1)])
def test_wheel_toolchain_rejects_newer_glibc(tmp_path, glibc, expected_status):
    step = next(
        step
        for step in jobs("_build.yml")["build-linux"]["steps"]
        if step.get("name") == "Select compiler toolchain (Linux)"
    )
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    for name, output in [
        ("getconf", glibc),
        ("gcc", "gcc 9"),
        ("g++", "g++ 9"),
        ("gcc-10", "gcc 10"),
        ("g++-10", "g++ 10"),
    ]:
        executable = command_dir / name
        executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
        executable.chmod(0o755)
    env_file = tmp_path / "environment"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        env={
            **environ,
            "PATH": f"{command_dir}{pathsep}{environ['PATH']}",
            "GITHUB_ENV": str(env_file),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected_status
    if expected_status == 0:
        assert env_file.read_text().splitlines() == [
            "CC=gcc-10",
            "CXX=g++-10",
            "OV_REQUIRE_RAGFS_BUILD=1",
        ]
    else:
        assert "glibc 2.31" in result.stdout
        assert not env_file.exists()


def test_ubuntu20_image_preinstalls_all_wheel_bootstrap_packages():
    step = next(
        step
        for step in jobs("_build.yml")["build-linux"]["steps"]
        if "packages=(" in step.get("run", "")
    )
    packages = re.search(r"packages=\((.*?)\)", step["run"], re.DOTALL).group(1).split()
    dockerfile = (WORKFLOWS.parent / "images/ubuntu20-ci.Dockerfile").read_text()
    installed = re.search(
        r"install -y --no-install-recommends(.*?)&&", dockerfile, re.DOTALL
    ).group(1)
    assert set(packages) == set(installed.replace("\\", "").split())
    assert "clang" not in packages
    assert dockerfile.startswith("FROM ubuntu:20.04\n")
    publish = next(
        step
        for step in jobs("ci-linux-image.yml")["build"]["steps"]
        if step.get("name") == "Verify installed system dependencies"
    )
    verified = re.search(r"packages=\((.*?)\)", publish["run"], re.DOTALL).group(1).split()
    assert set(verified) == set(packages)


def test_studio_stays_hosted_and_native_runtime_keeps_ubuntu24_baseline():
    for file, name in [("_build.yml", "build-studio")]:
        job = jobs(file)[name]
        assert job["runs-on"] == "ubuntu-24.04"
        assert "container" not in job
        assert all(step.get("uses") != "./.github/actions/linux-test-env" for step in job["steps"])
    native = jobs("api_test.yml")["native-runtime"]
    assert labels(native["container"]["image"], {}) == ["ubuntu:24.04"]
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
    assert labels(job["container"]["image"], {"arch": "x86_64"}) == ["ubuntu:20.04"]
    assert 0 < job["timeout-minutes"] <= 60
    assert any(step.get("uses") == "./.github/actions/linux-test-env" for step in job["steps"])


@pytest.mark.parametrize(
    "file,name,self_hosted_parity",
    [
        ("_build.yml", "build-linux", 0),
    ],
)
def test_selected_builds_alternate_half_the_runs(file, name, self_hosted_parity):
    expression = jobs(file)[name]["runs-on"]
    actual = [labels(expression, {"arch": "x86_64"}, run_number=number) for number in range(1, 101)]
    assert actual.count(SELF_HOSTED) == 50
    for number, result in enumerate(actual, 1):
        assert result == (SELF_HOSTED if number % 2 == self_hosted_parity else ["ubuntu-24.04"])


@pytest.mark.parametrize("name", ["prepare", "native-runtime"])
def test_trusted_api_jobs_never_wait_for_hosted_based_on_run_parity(name):
    expression = jobs("api_test.yml")[name]["runs-on"]
    assert [
        labels(
            expression,
            {},
            source="volcengine/OpenViking",
            event="pull_request",
            run_number=number,
        )
        for number in range(1, 101)
    ] == [SELF_HOSTED] * 100


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
    assert "!inputs.build_wheels" in diagnostic["if"]
    assert all("uses" not in step for step in diagnostic["steps"])


@pytest.mark.parametrize("number", [1, 2])
@pytest.mark.parametrize("check_linux_runner", [False, True])
@pytest.mark.parametrize(
    "arch,expected",
    [
        ("x86_64", ["ubuntu-24.04"]),
        ("aarch64", ["ubuntu-24.04-arm"]),
    ],
)
def test_manual_runner_check_does_not_change_wheel_routing(
    number, check_linux_runner, arch, expected
):
    assert (
        labels(
            jobs("_build.yml")["build-linux"]["runs-on"],
            {"arch": arch},
            event="workflow_dispatch",
            check_linux_runner=check_linux_runner,
            run_number=number,
        )
        == expected
    )


@pytest.mark.parametrize("number", [1, 2])
@pytest.mark.parametrize("source", ["volcengine/OpenViking", "fork/OpenViking"])
@pytest.mark.parametrize("image", ["", "ghcr.io/volcengine/openviking-ci-ubuntu24:tested"])
def test_api_consumers_inherit_the_trusted_native_producer_pool(number, source, image):
    workflow = jobs("api_test.yml")
    native = workflow["native-runtime"]
    destination = labels(
        native["runs-on"], {}, source=source, event="pull_request", run_number=number
    )
    assert destination == (SELF_HOSTED if source == "volcengine/OpenViking" else ["ubuntu-24.04"])
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
        pool["env"]["TEST_CONTAINER"],
        {},
        ubuntu24_image=image,
        contexts={"runner.environment": environment},
    )[0]
    selected_image = image or "ubuntu:24.04"
    assert labels(native["container"]["image"], {}, ubuntu24_image=image) == [selected_image]
    assert container == selected_image
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
        ("_test_full.yml", "test"),
        ("_codeql.yml", "analyze"),
        ("api_test_effect.yml", "effect-tests"),
    ],
)
def test_linux_test_containers_bootstrap_and_cleanup(file, name):
    job = jobs(file)[name]
    steps = job["steps"]
    bootstrap = steps[0]
    expected_condition = (
        "matrix.os == 'ubuntu-24.04'"
        if file in {"_test_lite.yml", "_test_full.yml"}
        else "runner.os == 'Linux'"
    )
    assert bootstrap["if"] == expected_condition
    assert "Acquire::https::CaInfo=/tmp/runner-ca-certificates.crt" in bootstrap["run"]
    assert "APT::Update::Error-Mode=any" in bootstrap["run"]
    assert "git ca-certificates curl python3 cmake build-essential" in bootstrap["run"]
    assert "ca-certificates.crt:/tmp/runner-ca-certificates.crt:ro" in job["container"]["options"]
    cleanup = next(step for step in steps if step.get("uses") == "./.github/actions/linux-test-env")
    assert cleanup["if"] == expected_condition
    assert steps.index(cleanup) < next(
        index for index, step in enumerate(steps) if step.get("uses") == "actions/setup-python@v6"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Ubuntu bootstrap uses bash/POSIX tools")
@pytest.mark.parametrize(
    "file,name",
    [
        ("_build.yml", "build-linux"),
        ("api_test.yml", "native-runtime"),
        ("api_test.yml", "api-tests"),
        ("api_test.yml", "database-tests"),
        ("_test_lite.yml", "test-lite"),
        ("_test_full.yml", "test"),
        ("_codeql.yml", "analyze"),
        ("api_test_effect.yml", "effect-tests"),
    ],
)
@pytest.mark.parametrize("installed", [True, False])
def test_preinstalled_image_skips_apt_but_partial_image_bootstraps(tmp_path, file, name, installed):
    job = jobs(file)[name]
    step = next(step for step in job["steps"] if "packages=(" in step.get("run", ""))
    shell = step.get("shell", job.get("defaults", {}).get("run", {}).get("shell"))
    if shell and shell.startswith("${{"):
        shell = labels(shell, {"os": "ubuntu-24.04"})[0]
    # Container jobs default to sh, which cannot execute the package arrays.
    assert shell == "bash"
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    apt_log = tmp_path / "apt.log"
    scripts = {
        # A removed package can have dpkg metadata and exit successfully.
        "dpkg-query": (
            '#!/bin/sh\nif [ "$3" = "$MISSING_PACKAGE" ]; then\n'
            'printf "deinstall ok config-files"\nelse\n'
            'printf "install ok installed"\nfi\n'
        ),
        "sed": "#!/bin/sh\nexit 0\n",
        "apt-get": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$APT_LOG"\n',
        "ln": "#!/bin/sh\nexit 0\n",
        "dpkg-reconfigure": "#!/bin/sh\nexit 0\n",
    }
    for name, script in scripts.items():
        executable = command_dir / name
        executable.write_text(script)
        executable.chmod(0o755)
    result = subprocess.run(
        [shell, "-e", "-o", "pipefail", "-c", step["run"]],
        env={
            **environ,
            "PATH": f"{command_dir}{pathsep}{environ['PATH']}",
            "APT_LOG": str(apt_log),
            "MISSING_PACKAGE": "" if installed else "gcc-10" if file == "_build.yml" else "git",
            "UBUNTU_MIRROR": "",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    if installed:
        assert "already installed" in result.stdout
        assert not apt_log.exists()
    else:
        calls = apt_log.read_text().splitlines()
        assert len(calls) == 2
        assert calls[0].endswith(" update")
        assert "install -y --no-install-recommends" in calls[1]
        assert "git" in calls[1].split()
        if file == "_build.yml":
            assert {"gcc-10", "g++-10"} <= set(calls[1].split())
            assert "missing required packages: gcc-10; falling back to apt" in result.stdout


@pytest.mark.parametrize(
    "file,name",
    [
        ("_test_lite.yml", "test-lite"),
        ("_test_full.yml", "test"),
        ("_codeql.yml", "analyze"),
        ("api_test_effect.yml", "effect-tests"),
    ],
)
@pytest.mark.parametrize("image", ["", "ghcr.io/volcengine/openviking-ci-ubuntu24:tested"])
def test_hosted_linux_checks_use_ci_image_with_stock_fallback(file, name, image):
    job = jobs(file)[name]
    contexts = {"inputs.linux_container": ""}
    assert labels(
        job["container"]["image"],
        {"os": "ubuntu-24.04"},
        ubuntu24_image=image,
        contexts=contexts,
    ) == [image or "ubuntu:24.04"]
    if file != "_codeql.yml":
        for os in ["macos-15", "windows-latest"]:
            assert labels(
                job["container"]["image"], {"os": os}, ubuntu24_image=image, contexts=contexts
            ) == [""]
    assert all("sudo apt-get" not in step.get("run", "") for step in job["steps"])
    bootstrap = next(step for step in job["steps"] if "packages=(" in step.get("run", ""))
    packages = re.search(r"packages=\((.*?)\)", bootstrap["run"], re.DOTALL).group(1).split()
    dockerfile = (WORKFLOWS.parent / "images/ubuntu24-ci.Dockerfile").read_text()
    installed = re.search(
        r"install -y --no-install-recommends(.*?)&&", dockerfile, re.DOTALL
    ).group(1)
    assert set(packages) <= set(installed.replace("\\", "").split())


def test_lite_explicit_image_override_takes_precedence():
    assert labels(
        jobs("_test_lite.yml")["test-lite"]["container"]["image"],
        {"os": "ubuntu-24.04"},
        ubuntu24_image="configured-image",
        contexts={"inputs.linux_container": "producer-image"},
    ) == ["producer-image"]


def test_effect_dependencies_do_not_build_project_before_native_toolchain():
    steps = jobs("api_test_effect.yml")["effect-tests"]["steps"]
    sync = next(step for step in steps if step.get("name") == "Install dependencies using uv sync")
    assert "--no-install-project" in sync["run"]
    rust = next(step for step in steps if step.get("uses") == "dtolnay/rust-toolchain@v1")
    build = next(step for step in steps if step.get("name") == "Build C++ extensions")
    install = next(
        step
        for step in steps
        if step.get("name") == "Verify native runtime and install Python entry points"
    )
    assert steps.index(sync) < steps.index(rust) < steps.index(build) < steps.index(install)
    assert "touch openviking/bin/ov" not in build["run"]
    assert "uv pip install --no-deps --no-build-isolation --editable ." in install["run"]
    for flag in [
        "OV_SKIP_CPP_BUILD",
        "OV_SKIP_RAGFS_BUILD",
        "OV_SKIP_OV_BUILD",
        "OV_SKIP_STUDIO_BUILD",
    ]:
        assert install["env"][flag] == "1"
    # A later implicit sync would build the project again outside the skip env.
    for step in steps:
        for command in re.findall(r"\buv run ([^\n]+)", step.get("run", "")):
            assert "--no-sync" in command or "--no-project" in command


@pytest.mark.skipif(sys.platform == "win32", reason="Workflow build step uses bash")
@pytest.mark.parametrize(
    "description,git_status,expected",
    [
        ("3b2de91", 0, "0.0.0.dev0"),
        ("v0.8.7-12-g3b2de91", 0, "0.8.7.dev0"),
        ("0.8.7", 0, "0.8.7.dev0"),
        ("", 128, "0.0.0.dev0"),
    ],
)
def test_effect_version_fallback_is_pep440_and_reaches_build(
    tmp_path, description, git_status, expected
):
    from packaging.version import Version

    commands = tmp_path / "bin"
    commands.mkdir()
    git = commands / "git"
    git.write_text(f"#!/bin/sh\nprintf '%s\\n' '{description}'\nexit {git_status}\n")
    uv = commands / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING" > "$BUILD_VERSION"\n'
    )
    git.chmod(0o755)
    uv.chmod(0o755)
    github_env = tmp_path / "github-env"
    build_version = tmp_path / "build-version"
    env = {
        **environ,
        "PATH": str(commands) + pathsep + environ["PATH"],
        "GITHUB_ENV": str(github_env),
        "BUILD_VERSION": str(build_version),
    }
    env.pop("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING", None)
    step = next(
        step
        for step in jobs("api_test_effect.yml")["effect-tests"]["steps"]
        if step.get("name") == "Build C++ extensions"
    )
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], env=env, check=True)
    assert build_version.read_text().strip() == expected
    assert github_env.read_text().strip() == (
        f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING={expected}"
    )
    assert str(Version(expected)) == expected


@pytest.mark.parametrize("file,name", [("_test_lite.yml", "test-lite"), ("_test_full.yml", "test")])
def test_reusable_test_shell_supports_versioned_windows_runners(file, name):
    shell = jobs(file)[name]["defaults"]["run"]["shell"]
    assert "startsWith(matrix.os, 'windows-')" in shell
    assert "'pwsh'" in shell and "'bash'" in shell


@pytest.mark.parametrize("container", ["", "ubuntu:24.04"])
def test_database_services_use_container_dns_or_host_loopback(container):
    job = jobs("api_test.yml")["database-tests"]
    contexts = {"needs.native-runtime.outputs.test_container": container}
    for variable, service in [
        ("OV_TEST_POSTGRES", "postgres"),
        ("OV_TEST_MYSQL", "mysql"),
        ("OPENVIKING_WATCH_MYSQL_PARAMS", "mysql"),
    ]:
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
        assert json.loads(value)["host"] == (service if container else "127.0.0.1")
    redis = "redis://redis:6379" if container else "redis://127.0.0.1:6379"
    assert labels(job["env"]["OV_TEST_REDIS"], {}, contexts=contexts) == [redis]
    for service, port in [("postgres", "5432"), ("mysql", "3306"), ("redis", "6379")]:
        assert labels(job["services"][service]["ports"][0], {}, contexts=contexts) == [
            port if container else f"{port}:{port}"
        ]
