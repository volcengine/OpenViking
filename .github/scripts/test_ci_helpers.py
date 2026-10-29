"""Dependency-free tests for routing and artifact compatibility boundaries."""

import fnmatch
import io
import json
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_plan
import native_bundle

ROOT = Path(__file__).resolve().parents[2]


class PlanTests(unittest.TestCase):
    def test_native_and_build_changes_cover_every_platform(self):
        for path in (
            "crates/ragfs/src/lock/manager.rs",
            "crates/ragfs-python/Cargo.toml",
            "src/abi3_engine_backend.cpp",
            "third_party/README",
            "Cargo.lock",
            "Cargo.toml",
            "setup.py",
            "MANIFEST.in",
            "scripts/build_support/x86_profiles.py",
            ".github/actions/native-runtime/action.yml",
            ".github/workflows/_build.yml",
        ):
            with self.subTest(path=path):
                result = ci_plan.plan([path])
                self.assertTrue(result["deps_changed"])
                self.assertTrue(result["cuvs_changed"])
                self.assertEqual(result["wheel_os"], ci_plan.FULL_OS)

    def test_cli_only_uses_one_wheel_and_no_cuvs(self):
        result = ci_plan.plan(["crates/ov_cli/src/tui/ui.rs"])
        self.assertTrue(result["deps_changed"])
        self.assertTrue(result["api_changed"])
        self.assertFalse(result["cuvs_changed"])
        self.assertEqual(result["wheel_os"], ["ubuntu-24.04"])

    def test_python_only_keeps_api_without_wheels(self):
        result = ci_plan.plan(["openviking/service/fs_service.py"])
        self.assertTrue(result["api_changed"])
        self.assertFalse(result["deps_changed"])

    def test_studio_changes_build_packaging(self):
        result = ci_plan.plan(["web-studio/src/App.vue"])
        self.assertTrue(result["deps_changed"])
        self.assertEqual(result["wheel_os"], ["ubuntu-24.04"])

    def test_focused_cuvs_test_triggers_cuvs(self):
        for path in (
            "tests/vectordb/test_cuvs_config.py",
            "tests/vectordb/test_str_to_uint64.py",
            "openviking/storage/vectordb_adapters/local_adapter.py",
        ):
            self.assertTrue(ci_plan.plan([path])["cuvs_changed"])

    def test_independent_plugin_and_sdk_changes(self):
        for path in (
            "examples/dsh-memory-plugin/src/index.ts",
            "examples/pi-coding-agent-extension/index.ts",
            "agent-plugins/plugin.test.mjs",
            "examples/memory-plugin-shared/lib/client.mjs",
        ):
            with self.subTest(path=path):
                result = ci_plan.plan([path])
                self.assertTrue(result["plugins_changed"])
                self.assertFalse(result["api_changed"])
                self.assertFalse(result["deps_changed"])
        result = ci_plan.plan(["sdk/python/openviking_sdk/client.py"])
        self.assertTrue(result["sdk_python_changed"])
        self.assertFalse(result["api_changed"])

    def test_unknown_path_and_mixed_pr_keep_api(self):
        self.assertTrue(ci_plan.plan(["new-runtime/file.py"])["api_changed"])
        result = ci_plan.plan(["sdk/python/client.py", "openviking/server/app.py"])
        self.assertTrue(result["sdk_python_changed"])
        self.assertTrue(result["api_changed"])

    def test_runtime_markdown_keeps_api_tests(self):
        for path in (
            "openviking/system_assets/compile/example-skills/daily-report/SKILL.md",
            "bot/workspace/SOUL.md",
            "bot/workspace/skills/weather/SKILL.md",
            "openviking/prompts/new-template.md",
            "new-runtime/prompts/template.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(ci_plan.plan([path])["api_changed"])

    def test_known_documentation_skips_api_tests(self):
        for path in (
            "README.md",
            "README.zh-CN.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "docs/zh/concepts/09-transaction.md",
            "bot/docs/en/concepts/01-architecture.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(ci_plan.plan([path])["api_changed"])
        self.assertTrue(ci_plan.plan(["README.md", "bot/workspace/SOUL.md"])["api_changed"])

    def test_non_pr_events_keep_full_coverage(self):
        result = ci_plan.plan([], full=True)
        self.assertTrue(all(value for key, value in result.items() if key != "wheel_os"))
        self.assertEqual(result["wheel_os"], ci_plan.FULL_OS)

    def test_merge_base_diff_detects_deletion_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                return subprocess.check_output(["git", "-C", tmp, *args], text=True).strip()

            git("init", "-q")
            (root / "setup.py").write_text("old")
            git("add", "setup.py")
            git(
                "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "base"
            )
            base = git("rev-parse", "HEAD")
            (root / "setup.py").rename(root / "renamed.py")
            git("add", "-A")
            git(
                "-c",
                "user.name=CI",
                "-c",
                "user.email=ci@example.invalid",
                "commit",
                "-qm",
                "rename",
            )
            paths = git("diff", "--name-only", "--no-renames", f"{base}...HEAD").splitlines()
            self.assertIn("setup.py", paths)
            self.assertTrue(ci_plan.plan(paths)["deps_changed"])


class WorkflowContractTests(unittest.TestCase):
    @staticmethod
    def workflow_jobs(workflow):
        text = (ROOT / ".github/workflows" / workflow).read_text().split("\njobs:\n", 1)[1]
        return dict(re.findall(r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)", text, re.M | re.S))

    @staticmethod
    def evaluate_runner(expression, repository, platform="ubuntu-24.04", arch="x86_64"):
        for variable, value in (
            ("github.repository", repository),
            ("matrix.os", platform),
            ("matrix.arch", arch),
        ):
            expression = expression.replace(variable, repr(value))
        expression = expression.replace("&&", "and").replace("||", "or")
        expression = expression.replace("null", "None")
        return eval(expression, {"__builtins__": {}, "fromJSON": json.loads})

    def test_memory_suite_stays_on_original_ecs_host(self):
        job = self.workflow_jobs("oc2ov_test.yml")["p0-tests"]
        self.assertIn("runs-on: [self-hosted, Linux, X64, openviking-ecs]", job)
        self.assertNotIn("ov-ci-runner", job)
        self.assertNotIn("container:", job)

    def test_linux_ci_uses_dedicated_runner_with_matching_container(self):
        dedicated = ["self-hosted", "Linux", "X64", "ov-ci-runner"]
        workflows = {
            "pr.yml": (
                "plugin-tests",
                "openclaw-tests",
                "plugin-versions",
                "check-deps",
                "sdk-python-tests",
                "langchain-tests",
            ),
            "_build.yml": ("build-studio", "build-sdist", "build-linux"),
            "rust-cli.yml": ("test", "build"),
            "api_test.yml": ("prepare", "api-tests"),
            "_test_lite.yml": ("test-lite",),
            "_test_full.yml": ("test",),
            "api_test_effect.yml": ("effect-tests",),
            "hermes-plugin-tests.yml": ("hermes-plugin-tests", "quick-local-platforms"),
            "python-agent-framework.yml": ("contracts",),
            "framework-layout.yml": ("layout",),
            "host-compat.yml": ("check",),
            "_docs.yml": ("build",),
            "_codeql.yml": ("analyze",),
            "gateway-chart.yml": ("chart",),
            "gateway.yml": ("test",),
            "gateway-e2e.yml": ("kind",),
        }
        for workflow, job_ids in workflows.items():
            jobs = self.workflow_jobs(workflow)
            for job_id in job_ids:
                job = jobs[job_id]
                runner = re.search(r"    runs-on: \$\{\{ (.+) \}\}", job).group(1)
                container = re.search(r"    container: (.+)", job)
                with self.subTest(workflow=workflow, job=job_id):
                    self.assertEqual(
                        self.evaluate_runner(runner, "volcengine/OpenViking"), dedicated
                    )
                    hosted = self.evaluate_runner(runner, "example/OpenViking")
                    self.assertIn(hosted, ("ubuntu-24.04", ["ubuntu-24.04"], ["ubuntu-latest"]))
                    if workflow in ("gateway.yml", "gateway-e2e.yml"):
                        self.assertIsNone(container)
                        self.assertIn("docker info", job)
                    elif job_id == "build-linux":
                        self.assertEqual(container.group(1), "ubuntu:20.04")
                        self.assertEqual(
                            self.evaluate_runner(runner, "volcengine/OpenViking", arch="aarch64"),
                            ["ubuntu-24.04-arm"],
                        )
                    else:
                        expression = container.group(1)[3:-3].strip()
                        self.assertEqual(
                            self.evaluate_runner(expression, "volcengine/OpenViking"),
                            "ubuntu:24.04",
                        )
                        self.assertIsNone(self.evaluate_runner(expression, "example/OpenViking"))
                    if "matrix.os" in runner:
                        for platform in (
                            "ubuntu-24.04-arm",
                            "macos-14",
                            "macos-15-intel",
                            "windows-latest",
                            "windows-2025",
                        ):
                            self.assertEqual(
                                self.evaluate_runner(runner, "volcengine/OpenViking", platform),
                                platform,
                            )
                            if container:
                                self.assertIsNone(
                                    self.evaluate_runner(
                                        expression, "volcengine/OpenViking", platform
                                    )
                                )

    def test_gateway_cleanup_removes_only_current_run_resources(self):
        for workflow, job_id, step_name, expected in (
            (
                "gateway.yml",
                "test",
                "Remove this run's Redis cluster containers",
                ["rm -f ovgw-test-1-7000", "rm -f ovgw-test-1-7001", "rm -f ovgw-test-1-7002"],
            ),
            (
                "gateway-e2e.yml",
                "kind",
                "Remove this run's kind cluster",
                ["delete cluster --name ovgw-test-1"],
            ),
        ):
            job = self.workflow_jobs(workflow)[job_id]
            cleanup = job.split(f"- name: {step_name}\n", 1)[1]
            self.assertIn("if: always()", cleanup)
            script = cleanup.split("run:", 1)[1].strip()
            if script.startswith("|"):
                script = script[1:]
            output = subprocess.check_output(
                [
                    "bash",
                    "-c",
                    "docker() { printf '%s\\n' \"$*\"; }; "
                    "kind() { printf '%s\\n' \"$*\"; }; "
                    "GATEWAY_REDIS_PREFIX=ovgw-test-1; KIND_CLUSTER_NAME=ovgw-test-1;\n" + script,
                ],
                text=True,
            )
            self.assertEqual(output.splitlines(), expected)

    def test_docker_image_build_keeps_native_arm_runner(self):
        job = self.workflow_jobs("build-docker-image.yml")["build-and-push-image"]
        expression = re.search(r"    runs-on: \$\{\{ (.+) \}\}", job).group(1)
        for repository, platform, expected in (
            (
                "volcengine/OpenViking",
                "ubuntu-24.04",
                ["self-hosted", "Linux", "X64", "ov-ci-runner"],
            ),
            ("volcengine/OpenViking", "ubuntu-24.04-arm", "ubuntu-24.04-arm"),
            ("example/OpenViking", "ubuntu-24.04", "ubuntu-24.04"),
        ):
            self.assertEqual(
                self.evaluate_runner(
                    expression.replace("matrix.runner", "matrix.os"), repository, platform
                ),
                expected,
            )
        self.assertNotIn("container:", job)
        self.assertIn("docker info", job)

    def test_dedicated_native_runner_is_automatic_with_manual_hosted_override(self):
        text = (ROOT / ".github/workflows/api_test.yml").read_text()
        block = text.split("\n  native-runtime:\n", 1)[1].split("\n  cuvs-tests:", 1)[0]
        runner = re.search(r"    runs-on: \$\{\{ (.+) \}\}", block).group(1)
        container = re.search(r"    container: \$\{\{ (.+) \}\}", block).group(1)
        prepare = re.search(r"        if: (.+)", block).group(1)
        contexts = (
            ("volcengine/OpenViking", "workflow_dispatch", "ov-ci-runner", True),
            ("volcengine/OpenViking", "workflow_dispatch", "github-hosted", False),
            ("volcengine/OpenViking", "pull_request", "", True),
            ("volcengine/OpenViking", "schedule", "", True),
            ("volcengine/OpenViking", "release", "", True),
            ("volcengine/OpenViking", "pull_request", "github-hosted", True),
            ("volcengine/OpenViking", "schedule", "github-hosted", True),
            ("volcengine/OpenViking", "release", "github-hosted", True),
            ("zhanglistar/OpenViking", "workflow_dispatch", "ov-ci-runner", False),
            ("zhanglistar/OpenViking", "pull_request", "", False),
            ("zhanglistar/OpenViking", "schedule", "", False),
            ("zhanglistar/OpenViking", "release", "", False),
        )
        for repository, event, selection, use_self_hosted in contexts:
            values = {
                "github.repository": repository,
                "github.event_name": event,
                "inputs.native_runner": selection,
            }

            def evaluate(expression, values=values):
                for variable, value in values.items():
                    expression = expression.replace(variable, repr(value))
                expression = expression.replace("&&", "and").replace("||", "or")
                expression = expression.replace("null", "None")
                return eval(expression, {"__builtins__": {}, "fromJSON": json.loads})

            with self.subTest(repository=repository, event=event, selection=selection):
                self.assertEqual(
                    evaluate(runner),
                    ["self-hosted", "Linux", "X64", "ov-ci-runner"]
                    if use_self_hosted
                    else ["ubuntu-24.04"],
                )
                self.assertEqual(evaluate(container), "ubuntu:24.04" if use_self_hosted else None)
                self.assertEqual(evaluate(prepare), use_self_hosted)
        self.assertIn("shell: bash", block)
        self.assertLess(
            block.index("Prepare self-hosted build container"), block.index("actions/checkout")
        )
        self.assertIn("libssl-dev", block)
        self.assertIn("procps", block)
        manual = text.split("\n      native_runner:\n", 1)[1].split("\n      linux_only:", 1)[0]
        self.assertIn("default: ov-ci-runner", manual)

    def test_linux_only_trial_does_not_reduce_automatic_api_coverage(self):
        text = (ROOT / ".github/workflows/api_test.yml").read_text()
        api = text.split("\n  api-tests:\n", 1)[1]
        expression = re.search(r"        os: \$\{\{ (.+) \}\}", api).group(1)
        full = ["ubuntu-24.04", "macos-14", "windows-latest"]
        for event, ref, linux_only, platforms in (
            ("workflow_dispatch", "refs/heads/main", True, ["ubuntu-24.04"]),
            ("workflow_dispatch", "refs/heads/main", False, full),
            ("schedule", "refs/heads/main", True, full),
            ("release", "refs/tags/v0.5.3", True, full),
            ("pull_request", "refs/pull/6751/merge", True, ["ubuntu-24.04"]),
        ):
            rendered = expression
            for variable, value in (
                ("github.event_name", event),
                ("github.ref", ref),
                ("inputs.linux_only", linux_only),
            ):
                rendered = rendered.replace(variable, repr(value))
            rendered = rendered.replace("&&", "and").replace("||", "or")
            with self.subTest(event=event, linux_only=linux_only):
                self.assertEqual(
                    eval(rendered, {"__builtins__": {}, "fromJSON": json.loads}), platforms
                )

    def test_runtime_markdown_reaches_workflow_planner(self):
        for workflow in ("pr.yml", "api_test.yml"):
            text = (ROOT / ".github/workflows" / workflow).read_text()
            ignored = re.search(r"paths-ignore:\n((?:[ \t]+-.*\n)+)", text).group(1)
            patterns = [line.strip()[2:].strip("'\"") for line in ignored.splitlines()]
            for path in (
                "openviking/system_assets/compile/example-skills/daily-report/SKILL.md",
                "openviking/prompts/new-template.md",
                "bot/workspace/SOUL.md",
            ):
                with self.subTest(workflow=workflow, path=path):
                    self.assertFalse(any(fnmatch.fnmatchcase(path, p) for p in patterns))

    def test_only_official_main_saves_compiler_caches(self):
        text = (ROOT / ".github/actions/compiler-cache/action.yml").read_text()
        steps = re.findall(
            r"    - uses: (actions/cache(?:/restore)?@v5)\n(.*?)(?=\n    - |\Z)",
            text,
            re.S,
        )
        self.assertEqual(len(steps), 4)
        contexts = (
            ("volcengine/OpenViking", "refs/heads/main", "push", True),
            ("volcengine/OpenViking", "refs/heads/main", "schedule", True),
            ("volcengine/OpenViking", "refs/heads/main", "workflow_dispatch", True),
            ("volcengine/OpenViking", "refs/pull/6751/merge", "pull_request", False),
            ("volcengine/OpenViking", "refs/heads/main", "pull_request", False),
            ("volcengine/OpenViking", "refs/tags/v0.5.3", "release", False),
            ("volcengine/OpenViking", "refs/heads/develop", "push", False),
            ("zhanglistar/OpenViking", "refs/heads/main", "push", False),
        )
        for repository, ref, event, should_save in contexts:
            enabled = []
            for action, body in steps:
                condition = re.search(r"      if: (.+)", body).group(1)
                for variable, value in (
                    ("github.repository", repository),
                    ("github.ref", ref),
                    ("github.event_name", event),
                ):
                    condition = condition.replace(variable, repr(value))
                condition = condition.replace("||", "or").replace("&&", "and")
                if eval(condition, {"__builtins__": {}}):
                    enabled.append(action)
                self.assertIn("${{ github.sha }}", re.search(r"        key: (.+)", body).group(1))
                self.assertNotIn("github.sha", body.split("restore-keys:", 1)[1])
            with self.subTest(repository=repository, ref=ref, event=event):
                self.assertEqual(
                    enabled,
                    ["actions/cache@v5"] * 2 if should_save else ["actions/cache/restore@v5"] * 2,
                )
        self.assertIn("CCACHE_MAXSIZE=256M", text)
        self.assertIn("CARGO_INCREMENTAL=0", text)

    def test_cuvs_required_check_context_is_preserved(self):
        caller = (ROOT / ".github/workflows/api_test.yml").read_text()
        reusable = (ROOT / ".github/workflows/_test_lite.yml").read_text()
        block = caller.split("\n  cuvs-tests:\n", 1)[1].split("\n  api-tests:", 1)[0]
        self.assertIn("uses: ./.github/workflows/_test_lite.yml", block)
        self.assertIn("os_json: '[\"ubuntu-24.04\"]'", block)
        self.assertIn("python_json: '[\"3.11\"]'", block)
        self.assertIn(
            "name: Lite Test (${{ matrix.os }}, ${{ matrix.python-version }})",
            reusable,
        )


class CompilerCacheTests(unittest.TestCase):
    """Exercise the configured keys against immutable, prefix-restored snapshots."""

    def setUp(self):
        text = (ROOT / ".github/actions/compiler-cache/action.yml").read_text()
        self.steps = re.findall(
            r"    - uses: (actions/cache(?:/restore)?@v5)\n(.*?)(?=\n    - |\Z)",
            text,
            re.S,
        )
        self.snapshots = {}

    def run_cache(self, sha, *, main=False, built=None, overrides=None):
        values = {
            "github.sha": sha,
            "runner.os": "Linux",
            "runner.arch": "X64",
            "inputs.target": "host",
            "steps.toolchain.outputs.hash": "toolchain-a",
            "hashFiles('Cargo.lock')": "lock-a",
        }
        values.update(overrides or {})
        restored, keys = [], []
        context = {
            "github.repository": "volcengine/OpenViking",
            "github.ref": "refs/heads/main" if main else "refs/pull/6751/merge",
            "github.event_name": "push" if main else "pull_request",
        }
        for action, body in self.steps:
            condition = re.search(r"      if: (.+)", body).group(1)
            for variable, value in context.items():
                condition = condition.replace(variable, repr(value))
            condition = condition.replace("||", "or").replace("&&", "and")
            if not eval(condition, {"__builtins__": {}}):
                continue
            rendered = re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: values[m.group(1)], body)
            key = re.search(r"        key: (.+)", rendered).group(1)
            prefixes = re.search(r"        restore-keys: \|\n((?:          .+\n?)+)", rendered)
            found = key if key in self.snapshots else None
            if found is None:
                for prefix in prefixes.group(1).splitlines() if prefixes else ():
                    found = next(
                        (k for k in reversed(self.snapshots) if k.startswith(prefix.strip())),
                        None,
                    )
                    if found is not None:
                        break
            restored.append(self.snapshots.get(found))
            keys.append(key)
            # actions/cache's post-job save uses the primary key, not the
            # restored prefix match. An exact hit cannot overwrite an entry.
            if action == "actions/cache@v5" and key not in self.snapshots:
                self.snapshots[key] = built
        self.assertEqual(len(keys), 2)
        return restored, keys

    def test_later_main_saves_new_compiler_output_and_pr_restores_it(self):
        restored, first_keys = self.run_cache("commit-a", main=True, built="compiled-a")
        self.assertEqual(restored, [None, None])
        restored, second_keys = self.run_cache("commit-b", main=True, built="compiled-b")
        self.assertEqual(restored, ["compiled-a"] * 2)
        self.assertTrue(
            all(first != second for first, second in zip(first_keys, second_keys, strict=True))
        )
        self.assertEqual([self.snapshots[key] for key in second_keys], ["compiled-b"] * 2)

        # A rerun of the same main commit gets an exact hit without overwriting.
        before = self.snapshots.copy()
        restored, _ = self.run_cache("commit-b", main=True, built="rerun")
        self.assertEqual(restored, ["compiled-b"] * 2)
        self.assertEqual(self.snapshots, before)
        restored, _ = self.run_cache("pr-merge", built="pr-output")
        self.assertEqual(restored, ["compiled-b"] * 2)
        self.assertEqual(self.snapshots, before)

    def test_restore_never_crosses_runner_or_toolchain_boundaries(self):
        self.run_cache("commit-a", main=True, built="compiled-a")
        for overrides in (
            {"runner.os": "Windows"},
            {"runner.arch": "ARM64"},
            {"steps.toolchain.outputs.hash": "toolchain-b"},
            {"inputs.target": "cross-target", "steps.toolchain.outputs.hash": "cross-toolchain"},
        ):
            with self.subTest(overrides=overrides):
                restored, _ = self.run_cache("pr-merge", overrides=overrides)
                self.assertEqual(restored, [None, None])
        self.assertEqual(len(self.snapshots), 2)

    def test_cargo_prefers_same_lockfile_before_newer_toolchain_cache(self):
        self.run_cache("commit-a", main=True, built="compiled-lock-a")
        restored, _ = self.run_cache(
            "commit-b",
            main=True,
            built="compiled-lock-b",
            overrides={"hashFiles('Cargo.lock')": "lock-b"},
        )
        self.assertEqual(restored, ["compiled-lock-a"] * 2)
        restored, _ = self.run_cache("pr-merge")
        self.assertEqual(restored, ["compiled-lock-a", "compiled-lock-b"])

    def test_legacy_cache_keys_can_seed_new_commit_entries(self):
        _, keys = self.run_cache("a" * 40)
        self.snapshots = {key.rsplit("-", 1)[0]: "legacy" for key in keys}
        restored, new_keys = self.run_cache("commit-b", main=True, built="compiled-b")
        self.assertEqual(restored, ["legacy"] * 2)
        self.assertEqual([self.snapshots[key] for key in new_keys], ["compiled-b"] * 2)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "runtime.tar.gz"
        self.files = (
            "openviking/bin/ov",
            "openviking/lib/ragfs_python.abi3.so",
            "openviking/storage/vectordb/engine/_x86_sse3.abi3.so",
        )
        self.context = {
            "schema": 1,
            "sha": "a" * 40,
            "os": "Linux",
            "arch": "x86_64",
            "platform": "linux-x86_64",
            "libc": "glibc 2.39",
            "abi": "cp310-abi3",
            "features": native_bundle.FEATURES,
            "build_options": {},
        }
        self.addCleanup(patch.stopall)
        self.mock_identity = patch.object(
            native_bundle, "identity", return_value=self.context
        ).start()
        for name in self.files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        native_bundle.pack(self.root, self.archive)
        for name in self.files:
            (self.root / name).unlink()

    def rewrite(self, transform):
        with tarfile.open(self.archive) as source:
            entries = [
                (member, source.extractfile(member).read()) for member in source.getmembers()
            ]
        with tarfile.open(self.archive, "w:gz") as destination:
            for member, data in transform(entries):
                member.size = len(data)
                destination.addfile(member, io.BytesIO(data))

    def test_round_trip_preserves_files_and_permissions(self):
        native_bundle.restore(self.root, self.archive)
        for name in self.files:
            path = self.root / name
            self.assertEqual(path.read_bytes(), name.encode())
            self.assertTrue(path.stat().st_mode & 0o111)

    def test_provenance_and_abi_mismatch_fail_before_writes(self):
        for key in ("sha", "arch", "os", "libc", "platform", "abi", "features", "build_options"):
            with self.subTest(key=key):
                self.mock_identity.return_value = dict(self.context, **{key: "wrong"})
                with self.assertRaisesRegex(ValueError, key):
                    native_bundle.restore(self.root, self.archive)
                self.assertFalse((self.root / self.files[0]).exists())

    def test_modified_binary_fails_before_writes(self):
        self.rewrite(
            lambda entries: [
                (member, b"corrupt" if member.name == self.files[-1] else data)
                for member, data in entries
            ]
        )
        with self.assertRaisesRegex(ValueError, "checksum"):
            native_bundle.restore(self.root, self.archive)
        self.assertFalse((self.root / self.files[0]).exists())

    def test_path_traversal_and_symlink_rejected(self):
        for path in ("../outside.so", "/tmp/outside.so", "openviking/lib/../../outside.so"):
            self.assertFalse(native_bundle.allowed(path))

        def symlink(entries):
            entries[-1][0].type = tarfile.SYMTYPE
            entries[-1][0].linkname = "../outside"
            return entries

        self.rewrite(symlink)
        with self.assertRaisesRegex(ValueError, "Invalid bundle member"):
            native_bundle.restore(self.root, self.archive)

    def test_non_abi3_artifacts_cannot_be_packed(self):
        for name in (
            "openviking/lib/ragfs_python.cpython-311.so",
            "openviking/storage/vectordb/engine/_x86_sse3.cpython-311.so",
        ):
            self.assertFalse(native_bundle.allowed(name))
        with self.assertRaisesRegex(ValueError, "Missing stable-ABI"):
            native_bundle.pack(self.root, self.archive)

    def test_duplicate_and_missing_members_rejected(self):
        self.rewrite(lambda entries: entries + [entries[-1]])
        with self.assertRaisesRegex(ValueError, "file count"):
            native_bundle.restore(self.root, self.archive)

    def test_free_threaded_python_rejected(self):
        with patch.object(native_bundle.sysconfig, "get_config_var", return_value=1):
            with self.assertRaisesRegex(ValueError, "CPython"):
                native_bundle.restore(self.root, self.archive)


if __name__ == "__main__":
    unittest.main()
