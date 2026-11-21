"""CodeQL routing must retain whole-language coverage for relevant changes."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "codeql_plan", ROOT / ".github/scripts/codeql_plan.py"
)
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)


class CodeQLSelectionTests(unittest.TestCase):
    def test_documentation_and_unrelated_languages_skip(self):
        for paths in (
            ["docs/example.py", "bot/docs/example.cpp"],
            ["README.md", "examples/README.md", "docs/design.rst", "logo.svg"],
            ["LICENSE", ".gitignore", ".editorconfig"],
            ["crates/ov_cli/src/main.rs", "web-studio/src/app.tsx"],
            [],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(planner.plan(paths), [])

    def test_python_changes_scan_only_python(self):
        self.assertEqual(
            planner.plan(["openviking/server/app.py", "sdk/python/client.pyi", "README.md"]),
            ["python"],
        )

    def test_native_sources_headers_and_templates_scan_only_cpp(self):
        for path in (
            "src/engine.cpp",
            "third_party/lib/data",
            "include/api.h",
            "include/api.hpp",
            "include/api.inl",
            "include/api.h.in",
            "lib/kernel.c",
            "lib/index.cxx",
            "lib/kernel.cuh",
        ):
            with self.subTest(path=path):
                self.assertEqual(planner.plan([path]), ["cpp"])

    def test_shared_build_and_scan_configuration_scan_both(self):
        for path in (
            "setup.py",
            "pyproject.toml",
            "uv.lock",
            "setup.cfg",
            "requirements-dev.txt",
            "scripts/build_support/versioning.py",
            "CMakeLists.txt",
            "src/cmake/options.cmake",
            ".gitmodules",
            ".github/workflows/_codeql.yml",
            ".github/workflows/pr.yml",
            ".github/actions/native/action.yml",
            ".github/scripts/codeql_plan.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(planner.plan([path]), ["python", "cpp"])

    def test_mixed_changes_scan_both(self):
        self.assertEqual(planner.plan(["lib/api.h", "openviking/app.py"]), ["python", "cpp"])

    def test_scheduled_manual_and_initial_push_scan_both(self):
        for event, base in (
            ("schedule", ""),
            ("workflow_dispatch", ""),
            ("push", "0" * 40),
            ("push", ""),
            ("pull_request", ""),
        ):
            with self.subTest(event=event, base=base):
                self.assertEqual(planner.select(event, base, "head"), ["python", "cpp"])

    def test_diff_errors_fail_instead_of_silently_skipping(self):
        with patch.object(
            planner.subprocess,
            "check_output",
            side_effect=planner.subprocess.CalledProcessError(128, "git"),
        ):
            with self.assertRaises(planner.subprocess.CalledProcessError):
                planner.select("push", "missing", "head")


class CodeQLDiffTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(directory.name)
        subprocess.run(["git", "init", "-q"], check=True)
        self.git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "CI test",
            "GIT_AUTHOR_EMAIL": "ci-test@example.invalid",
            "GIT_COMMITTER_NAME": "CI test",
            "GIT_COMMITTER_EMAIL": "ci-test@example.invalid",
        }

    def commit(self, files, parent=None):
        # Construct isolated history without touching user git configuration.
        entries = []
        for name, contents in sorted(files.items()):
            blob = (
                subprocess.check_output(
                    ["git", "hash-object", "-w", "--stdin"], input=contents.encode()
                )
                .decode()
                .strip()
            )
            entries.append(f"100644 blob {blob}\t{name}\n")
        tree = (
            subprocess.check_output(["git", "mktree"], input="".join(entries).encode())
            .decode()
            .strip()
        )
        command = ["git", "commit-tree", tree, "-m", "fixture"]
        if parent:
            command.extend(["-p", parent])
        return subprocess.check_output(command, env=self.git_env).decode().strip()

    def test_pr_excludes_base_branch_only_changes_but_push_includes_them(self):
        root = self.commit({"README.md": "base"})
        base = self.commit({"README.md": "base", "base.py": "pass"}, root)
        head = self.commit({"README.md": "base", "feature.cpp": "int x;"}, root)
        self.assertEqual(planner.select("pull_request", base, head), ["cpp"])
        self.assertEqual(planner.select("push", base, head), ["python", "cpp"])

    def test_source_renamed_to_documentation_still_scans_cpp(self):
        before = self.commit({"old.h": "int x;"})
        after = self.commit({"README.md": "int x;"}, before)
        self.assertEqual(planner.select("pull_request", before, after), ["cpp"])

    def test_deleted_python_file_still_scans_python(self):
        before = self.commit({"removed.py": "pass"})
        after = self.commit({}, before)
        self.assertEqual(planner.select("push", before, after), ["python"])


if __name__ == "__main__":
    unittest.main()
