import os
import subprocess
from pathlib import Path


def test_ov_cli_prefers_injected_openviking_version(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    target_dir = tmp_path / "cargo-target"
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target_dir)
    env["OPENVIKING_VERSION"] = "9.9.9.dev1"

    subprocess.run(
        ["cargo", "build", "--release", "-p", "ov_cli"],
        cwd=repo_root,
        env=env,
        check=True,
    )

    binary_name = "ov.exe" if os.name == "nt" else "ov"
    binary_path = target_dir / "release" / binary_name
    result = subprocess.run(
        [str(binary_path), "version"],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "9.9.9.dev1"
