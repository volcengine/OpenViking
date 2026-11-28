"""Check the overlay helper's Docker boundary without building an image."""

import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HELPER = Path(__file__).with_name("build_overlay.py")
SELF_CHECK = (
    'RUN python -I -c "import redis; from openviking_gateway.config import GatewayConfig; '
    "assert 'storage' in GatewayConfig.model_fields; from pathlib import Path; "
    "import openviking_gateway; "
    "assert list((Path(openviking_gateway.__file__).parent / 'lua').glob('*.lua'))\""
)


@unittest.skipIf(
    sys.platform == "win32", "Overlay Dockerfile checks require Linux CI file-sharing semantics"
)
class OverlayBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "business source"
        (self.root / "openviking_gateway" / "lua").mkdir(parents=True)
        (self.root / "openviking_gateway" / "__init__.py").write_text("gateway fixture\n")
        (self.root / "openviking_gateway" / "lua" / "claim.lua").write_text("return 1\n")
        (self.root / ".dockerignore").write_text("*.log\n!openviking_gateway/kept.log\n")
        for name, content in {
            "openviking/changed.py": "changed = True\n",
            "openviking/unchanged.py": "unchanged = True\n",
            "openviking/data.json": "{}\n",
            "openviking_cli/new.py": "new = True\n",
        }.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def invoke(self, files, inspect, *, helper=HELPER, extra=()):
        args = [
            str(helper),
            "--source-root",
            str(self.root),
            "--base-ref",
            "v0.5.2",
            "--tag",
            "ovgw-e2e:local",
            *extra,
        ]
        with (
            patch.object(sys, "argv", args),
            patch("subprocess.check_output", return_value="\n".join(files)) as diff,
            patch("subprocess.run", side_effect=inspect),
        ):
            runpy.run_path(str(helper), run_name="__main__")
        diff.assert_called_once_with(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=AM",
                "v0.5.2",
                "--",
                "openviking",
                "openviking_cli",
            ],
            cwd=self.root.resolve(),
            text=True,
        )

    def test_only_selected_python_files_reach_one_final_overlay_copy(self):
        dockerfiles = []
        original_ignore = (self.root / ".dockerignore").read_bytes()
        original_files = {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

        def inspect(command, *, check):
            self.assertTrue(check)
            self.assertEqual(command[:5], ["docker", "build", "--platform", "linux/amd64", "-t"])
            self.assertEqual(command[5:7], ["ovgw-e2e:local", "-f"])
            self.assertEqual(command[-1], str(self.root.resolve()))
            dockerfile = Path(command[7])
            dockerfiles.append(dockerfile)
            self.assertEqual(
                dockerfile.read_text().splitlines(),
                [
                    "FROM scratch AS overlay",
                    "COPY openviking_gateway/ /openviking_gateway/",
                    "COPY openviking/changed.py /openviking/changed.py",
                    "COPY openviking_cli/new.py /openviking_cli/new.py",
                    "FROM ghcr.io/volcengine/openviking:v0.5.2",
                    "COPY --from=overlay / /app/.venv/lib/python3.13/site-packages/",
                    SELF_CHECK,
                ],
            )
            self.assertEqual((self.root / ".dockerignore").read_bytes(), original_ignore)
            self.assertEqual(
                (self.root / "openviking_gateway/lua/claim.lua").read_text(), "return 1\n"
            )

        self.invoke(
            ["openviking/changed.py", "openviking/data.json", "openviking_cli/new.py"], inspect
        )
        self.assertEqual(len(dockerfiles), 1)
        self.assertFalse(dockerfiles[0].exists())
        self.assertEqual(
            {
                p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*")
                if p.is_file()
            },
            original_files,
        )

    def test_gateway_only_overlay_keeps_the_release_and_selfcheck(self):
        def inspect(command, *, check):
            self.assertTrue(check)
            self.assertEqual(
                Path(command[7]).read_text().splitlines(),
                [
                    "FROM scratch AS overlay",
                    "COPY openviking_gateway/ /openviking_gateway/",
                    "FROM ghcr.io/volcengine/openviking:v0.5.2",
                    "COPY --from=overlay / /app/.venv/lib/python3.13/site-packages/",
                    SELF_CHECK,
                ],
            )

        self.invoke([], inspect)

    def test_relocated_helper_builds_the_explicit_business_source(self):
        relocated = Path(self.temporary.name) / "diagnostic tools" / "build_overlay.py"
        relocated.parent.mkdir()
        shutil.copy2(HELPER, relocated)

        def inspect(command, *, check):
            self.assertTrue(check)
            self.assertEqual(command[-1], str(self.root.resolve()))
            self.assertIn("FROM fixture-release:v0.5.2", Path(command[7]).read_text().splitlines())
            self.assertNotIn(str(relocated.parent), command)

        self.invoke([], inspect, helper=relocated, extra=("--base", "fixture-release:v0.5.2"))

    def test_docker_failure_propagates_and_removes_the_temporary_dockerfile(self):
        dockerfiles = []

        def fail(command, *, check):
            self.assertTrue(check)
            dockerfiles.append(Path(command[7]))
            raise subprocess.CalledProcessError(17, command)

        with self.assertRaises(subprocess.CalledProcessError) as error:
            self.invoke([], fail)
        self.assertEqual(error.exception.returncode, 17)
        self.assertFalse(dockerfiles[0].exists())


if __name__ == "__main__":
    unittest.main()
