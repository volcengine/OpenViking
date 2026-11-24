import os
import subprocess
import sys


def test_viking_fs_imports_in_fresh_process(tmp_path):
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(tmp_path / "missing-ov.conf")

    result = subprocess.run(
        [sys.executable, "-c", "from openviking.storage.viking_fs import VikingFS"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 0, result.stderr
