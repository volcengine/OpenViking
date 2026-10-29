"""Dependency-free tests for routing and artifact compatibility boundaries."""

import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ci_plan
import native_bundle


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
