#!/usr/bin/env python3
"""Check real Hermes subdirectory installation and dependency admission on POSIX.

The installed plugin must equal the plugin directory at the checked-out commit,
file for file, and the provider and every module of its core/ subpackage must
import from the installed copy.
"""

import argparse
import errno
import hashlib
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


PLUGIN_SUBDIR = "examples/hermes-plugin"
# Written by Python or pytest, never by the installer; skipped on the installed side.
CACHE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
CACHE_SUFFIXES = (".pyc", ".pyo")


def _git_object_id(path):
    """The Git blob id of ``path``'s content (or of its link target for a symlink)."""
    data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def source_tree(repository, ref):
    """``{relative path: blob id}`` of the plugin directory at ``ref``.

    ``hermes plugins install <repo>#<subdir> --ref <sha>`` publishes a sparse
    checkout of that subdirectory at that commit and skips nothing in it, so
    the committed tree, not the working tree, is what the install must equal.
    """
    listing = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", "--full-tree", ref, "--", PLUGIN_SUBDIR],
        cwd=repository,
    )
    tree = {}
    for record in filter(None, listing.split(b"\0")):
        meta, _, name = record.partition(b"\t")
        _mode, kind, object_id = meta.decode().split()
        if kind != "blob":
            raise RuntimeError(f"Unexpected {kind} in the plugin tree: {name.decode()}")
        tree[name.decode()[len(PLUGIN_SUBDIR) + 1 :]] = object_id
    if "__init__.py" not in tree or not any(name.startswith("core/") for name in tree):
        raise RuntimeError(f"No plugin package with a core/ subpackage at {ref}:{PLUGIN_SUBDIR}")
    return tree


def installed_tree(installed):
    """``{relative path: blob id}`` of the installed plugin, without caches."""
    tree = {}
    for path in sorted(installed.rglob("*")):
        relative = path.relative_to(installed)
        if CACHE_PARTS.intersection(relative.parts) or path.name.endswith(CACHE_SUFFIXES):
            continue
        if path.is_symlink() or path.is_file():
            tree[relative.as_posix()] = _git_object_id(path)
    return tree


def compare_trees(source, installed):
    """Raise when the installed plugin is not the source tree, file for file."""
    problems = [f"missing: {name}" for name in sorted(source.keys() - installed.keys())]
    problems += [f"unexpected: {name}" for name in sorted(installed.keys() - source.keys())]
    problems += [
        f"differs: {name}"
        for name in sorted(source.keys() & installed.keys())
        if source[name] != installed[name]
    ]
    if problems:
        shown = "\n  ".join(problems[:40])
        more = f"\n  ... and {len(problems) - 40} more" if len(problems) > 40 else ""
        raise RuntimeError(f"Installed plugin differs from the source tree:\n  {shown}{more}")
    return len(source)


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
    identifier = repository.as_uri() + "#" + PLUGIN_SUBDIR
    source = source_tree(repository, ref)
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
        installed_files = compare_trees(source, installed_tree(installed))
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
import importlib
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
installed = (home / "plugins/openviking").resolve()
package = type(provider).__module__
core = sorted(p.stem for p in (installed / "core").glob("*.py") if p.stem != "__init__")
assert core, installed
for name in [package, package + ".core"] + [package + ".core." + stem for stem in core]:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(installed), (name, module.__file__)
provider.shutdown()
print(json.dumps({"installed": True, "enabled": True, "external_module": type(provider).__module__,
                  "core_modules": len(core),
                  "psutil": psutil.__version__, "home": str(home),
                  "python": str(project_python(Path.cwd()))}))
"""
        result = subprocess.check_output(
            [python, "-c", probe], cwd=host, env=env, text=True, timeout=timeout
        )
        evidence = json.loads(result.strip().splitlines()[-1])
        evidence["source_ref"] = ref
        evidence["installed_files"] = installed_files
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
