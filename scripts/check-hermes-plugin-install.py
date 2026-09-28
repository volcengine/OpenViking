#!/usr/bin/env python3
"""Check real Hermes subdirectory installation and dependency admission on POSIX."""

import argparse
import errno
import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def enable(command, *, host, env, log, timeout):
    """Give the CLI a terminal and accept consent for this repository's plugin."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(host)
        os.execve(command[0], command + ["plugins", "enable", "openviking"], env)
    output = bytearray()
    cursor = 0
    finished = False
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    output.extend(os.read(fd, 65536))
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                for prompt in (
                    b"Prepare these with Hermes through PM now? [y/N]:",
                    b"Grant these capabilities? [y/N]",
                ):
                    match = output.find(prompt, cursor)
                    if match >= 0:
                        cursor = match + len(prompt)
                        os.write(fd, b"y\n")
            ended, status = os.waitpid(pid, os.WNOHANG)
            if ended:
                finished = True
                if os.waitstatus_to_exitcode(status) != 0:
                    raise RuntimeError("Hermes plugin enable failed; see " + str(log))
                return
        raise TimeoutError("Hermes plugin enable timed out; see " + str(log))
    finally:
        if not finished:
            os.killpg(pid, signal.SIGTERM)
            os.waitpid(pid, 0)
        os.close(fd)
        log.write_bytes(output)


def check(host, repository, root, timeout):
    home = root / "user" / ".hermes"
    home.mkdir(parents=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENVIKING_", "HERMES_", "__HERMES_"))
        and key not in {"PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"}
    }
    env.update(
        HOME=str(home.parent),
        HERMES_HOME=str(home),
        HERMES_RUNTIME_DIR=str(root / "runtime"),
        HERMES_ENABLE_PROJECT_PLUGINS="0",
        TERM="xterm-256color",
    )
    command = [sys.executable, str(host / "hermes")]
    ref = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    identifier = repository.as_uri() + "#examples/hermes-plugin"
    bundled = host / "plugins" / "memory" / "openviking"
    hidden = root / "bundled-openviking"
    if bundled.exists():
        bundled.rename(hidden)
    try:
        with (root / "install.log").open("w") as log:
            subprocess.run(
                command + ["plugins", "install", identifier, "--ref", ref, "--no-enable"],
                cwd=host,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=True,
            )
        installed = home / "plugins" / "openviking"
        for name in (
            "__init__.py",
            "_setup.py",
            "native_memory_mirror.py",
            "plugin.yaml",
            "pyproject.toml",
        ):
            if (installed / name).read_bytes() != (
                repository / "examples/hermes-plugin" / name
            ).read_bytes():
                raise RuntimeError("Installed plugin differs from the checked-out source: " + name)
        enable(command, host=host, env=env, log=root / "enable.log", timeout=timeout)
        # PM can retire the original .venv after publishing. Read its selected
        # interpreter through the stdlib-only path module before the next command.
        python = subprocess.check_output(
            [
                str(Path(sys._base_executable).resolve()),
                "-c",
                "from pathlib import Path; from pm.environments import project_python; "
                "print(project_python(Path.cwd()))",
            ],
            cwd=host,
            env=env,
            text=True,
            timeout=timeout,
        ).strip()
        command = [python, str(host / "hermes")]
        with (root / "validate.log").open("w") as log:
            subprocess.run(
                command + ["plugins", "validate", str(installed)],
                cwd=host,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=True,
            )
        probe = """
import hermes_bootstrap
import json
from pathlib import Path
import psutil
from ruamel.yaml import YAML
from hermes_constants import get_hermes_home
from plugins.memory import find_provider_dir, load_memory_provider
from pm.environments import project_python
home = get_hermes_home()
config = YAML(typ="safe").load((home / "config.yaml").read_text())
assert "openviking" in config["plugins"]["enabled"], config.get("plugins")
assert "openviking" not in config["plugins"].get("disabled", [])
assert find_provider_dir("openviking") == home / "plugins/openviking"
provider = load_memory_provider("openviking", register_skills=False)
assert type(provider).__module__.startswith("_hermes_user_memory."), type(provider)
provider.shutdown()
print(json.dumps({"installed": True, "enabled": True, "external_module": type(provider).__module__,
                  "psutil": psutil.__version__, "home": str(home),
                  "python": str(project_python(Path.cwd()))}))
"""
        result = subprocess.check_output(
            [python, "-c", probe], cwd=host, env=env, text=True, timeout=timeout
        )
        evidence = json.loads(result.strip().splitlines()[-1])
        evidence["source_ref"] = ref
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence))
    finally:
        if hidden.exists():
            hidden.rename(bundled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hermes-root",
        type=Path,
        required=True,
        help="Isolated Hermes checkout with its dependencies installed",
    )
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, help="Keep the isolated profile and logs here")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    host, repository = args.hermes_root.resolve(), args.repository.resolve()
    if args.output_dir:
        root = args.output_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        check(host, repository, root, args.timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="hermes-plugin-install-") as directory:
            root = Path(directory)
            try:
                check(host, repository, root, args.timeout)
            except Exception:
                for name in ("install.log", "enable.log", "validate.log"):
                    if (root / name).exists():
                        print(
                            name + ":\n" + (root / name).read_text(errors="replace")[-6000:],
                            file=sys.stderr,
                        )
                raise


if __name__ == "__main__":
    main()
