# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI fixtures that run against a real OpenViking server process."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Generator

import httpx
import pytest

from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV


def _get_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_health(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url}/health", timeout=1.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"OpenViking server failed to start: {last_error}")


@pytest.fixture(scope="session")
def openviking_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    storage_dir = tmp_path_factory.mktemp("openviking_cli_data")
    port = _get_free_port()

    base_conf_path = Path("examples/ov.conf").resolve()
    with open(base_conf_path) as f:
        conf_data = json.load(f)

    conf_data.setdefault("server", {})
    conf_data["server"]["host"] = "127.0.0.1"
    conf_data["server"]["port"] = port

    conf_data.setdefault("storage", {})
    conf_data["storage"]["workspace"] = str(storage_dir)
    conf_data["storage"].setdefault("vectordb", {})
    conf_data["storage"]["vectordb"]["backend"] = "local"
    conf_data["storage"].setdefault("agfs", {})
    conf_data["storage"]["agfs"]["backend"] = "local"

    tmp_conf = storage_dir / "ov.conf"
    with open(tmp_conf, "w") as f:
        json.dump(conf_data, f)

    env = os.environ.copy()
    env[OPENVIKING_CONFIG_ENV] = str(tmp_conf)

    cmd = [
        sys.executable,
        "-m",
        "openviking",
        "serve",
        "--config",
        str(tmp_conf),
    ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    url = f"http://127.0.0.1:{port}"

    try:
        _wait_for_health(url)
        yield url
    except RuntimeError:
        stdout, stderr = "", ""
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=5)
        else:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=10)
        raise RuntimeError(
            f"OpenViking server failed to start.\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# Remote CLI test infrastructure
# ---------------------------------------------------------------------------

OPENVIKING_BIN = os.getenv("OPENVIKING_CLI_BIN", "")
BASE_URL = os.getenv(
    "OPENVIKING_URL",
    os.getenv("SERVER_URL", "https://api.vikingdb.cn-beijing.volces.com/openviking"),
)
API_KEY = os.getenv("OPENVIKING_API_KEY", "")


def _resolve_bin():
    if OPENVIKING_BIN:
        if os.path.isfile(OPENVIKING_BIN):
            return OPENVIKING_BIN
    import sys as _sys
    bin_dir = os.path.dirname(_sys.executable)
    for name in ("openviking", "ov"):
        for d in [bin_dir, "/usr/local/bin", "/usr/bin", os.path.expanduser("~/.local/bin")]:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate):
                return candidate
    for name in ("openviking", "ov"):
        found = shutil.which(name)
        if found:
            return found
    try:
        result = subprocess.run(
            ["bash", "-lc", "command -v openviking || command -v ov"],
            capture_output=True, text=True, timeout=10,
        )
        path = result.stdout.strip()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return None


CLI_BIN = _resolve_bin()


def _write_cli_config():
    config_dir = os.path.join(tempfile.gettempdir(), "openviking_cli_test_config")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "ovcli.conf")
    config = {
        "url": BASE_URL,
        "api_key": API_KEY,
        "account": "test-account",
        "user": "test-user",
        "timeout": 120.0,
        "output": "table",
        "echo_command": True,
        "upload": {},
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return config_path


CLI_CONFIG_PATH = _write_cli_config()


def _env():
    env = os.environ.copy()
    env["OPENVIKING_CLI_CONFIG_FILE"] = CLI_CONFIG_PATH
    env["OPENVIKING_URL"] = BASE_URL
    env["OPENVIKING_API_KEY"] = API_KEY
    return env


def _check_cli_compatible():
    if CLI_BIN is None:
        return False
    try:
        result = subprocess.run(
            [CLI_BIN, "version"], capture_output=True, text=True, timeout=10, env=_env(),
        )
        if result.returncode != 0 and "GLIBC" in result.stderr:
            return False
        return True
    except Exception:
        return False


CLI_COMPATIBLE = _check_cli_compatible()


def pytest_collection_modifyitems(config, items):
    if not CLI_COMPATIBLE:
        reason = "openviking CLI not available"
        if CLI_BIN is None:
            reason = "openviking CLI binary not found. Install via: curl -fsSL http://openviking.tos-cn-beijing.volces.com/cli/install.sh | bash"
        else:
            try:
                result = subprocess.run(
                    [CLI_BIN, "version"], capture_output=True, text=True, timeout=10, env=_env(),
                )
                if "GLIBC" in result.stderr:
                    reason = "openviking CLI binary is not compatible with this system (GLIBC version mismatch)"
            except Exception:
                pass
        skip_cli = pytest.mark.skip(reason=reason)
        for item in items:
            if item.get_closest_marker("cli_remote"):
                item.add_marker(skip_cli)


def _parse_cli_json(stdout):
    json_start = stdout.find("{")
    if json_start == -1:
        return None
    json_str = stdout[json_start:]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    depth = 0
    for i, ch in enumerate(json_str):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(json_str[: i + 1])
                except json.JSONDecodeError:
                    break
    return None


CLI_ACCOUNT = os.getenv("OPENVIKING_ACCOUNT", "test-account")
CLI_USER = os.getenv("OPENVIKING_USER", "test-user")


def _inject_global_args(args):
    global_args = []
    if CLI_ACCOUNT:
        global_args.extend(["--account", CLI_ACCOUNT])
    if CLI_USER:
        global_args.extend(["--user", CLI_USER])
    if not args or args[0] in ("version", "--version", "-V", "help", "--help", "-h"):
        return args
    return global_args + args


def ov(args, timeout=120):
    cmd = [CLI_BIN] + _inject_global_args(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=_env(),
    )
    stdout = result.stdout.strip()
    data = _parse_cli_json(stdout)
    return {
        "exit_code": result.returncode,
        "json": data,
        "stdout": stdout,
        "stderr": result.stderr.strip(),
    }


def _wait_for_resource_ready(uri, retries=20, interval=10):
    for attempt in range(retries):
        r = ov(["read", uri, "-o", "json"], timeout=30)
        if r["exit_code"] == 0 and len(r["stdout"]) > 0:
            return True
        time.sleep(interval)
    return False


def _find_file_in_pack(pack_uri, retries=10, interval=5):
    for attempt in range(retries):
        ls_r = ov(["ls", pack_uri, "-o", "json"])
        if ls_r["json"] and "result" in ls_r["json"]:
            items = ls_r["json"]["result"]
            for item in items:
                if isinstance(item, dict) and item.get("isDir") is False:
                    return item["uri"]
        time.sleep(interval)
    return None


@pytest.fixture(scope="session", autouse=True)
def ensure_resources_dir():
    r = ov(["mkdir", "viking://resources", "-o", "json"], timeout=120)
    if r["exit_code"] != 0 and "already exists" not in r["stderr"].lower():
        r2 = ov(["stat", "viking://resources", "-o", "json"])
        if r2["exit_code"] != 0:
            pass


@pytest.fixture(scope="session")
def test_dir_uri(ensure_resources_dir):
    uri = f"viking://resources/cli_test_{uuid.uuid4().hex[:8]}"
    r = None
    for attempt in range(5):
        r = ov(["mkdir", uri, "-o", "json"], timeout=120)
        if r["exit_code"] == 0:
            break
        time.sleep(5)
    assert r["exit_code"] == 0, f"mkdir failed after retries: {r['stderr']}"
    yield uri
    for attempt in range(10):
        r = ov(["rm", uri, "-r", "-o", "json"], timeout=120)
        if r["exit_code"] == 0:
            break
        time.sleep(5)


@pytest.fixture(scope="session")
def test_pack_uri(test_dir_uri):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("# CLI Test\n\nThis is a test file for CLI automation.")
        temp_path = f.name
    try:
        pack_uri = f"{test_dir_uri}/test_pack"
        r = None
        for attempt in range(5):
            r = ov(["add-resource", temp_path, "--to", pack_uri, "--wait", "-o", "json"], timeout=120)
            if r["exit_code"] == 0:
                break
            time.sleep(5)
        assert r["exit_code"] == 0, f"add-resource failed after retries: {r['stderr']}"
    finally:
        os.unlink(temp_path)
    file_uri = _find_file_in_pack(pack_uri, retries=15, interval=5)
    if file_uri:
        ready = _wait_for_resource_ready(file_uri, retries=20, interval=10)
        assert ready, f"Resource {file_uri} did not become ready in time"
    return pack_uri


@pytest.fixture(scope="session")
def test_file_uri(test_pack_uri):
    file_uri = _find_file_in_pack(test_pack_uri, retries=10, interval=5)
    assert file_uri is not None, (
        f"Could not find file inside pack {test_pack_uri}. "
        f"ls result: {ov(['ls', test_pack_uri, '-o', 'json'])['stdout'][:300]}"
    )
    return file_uri


@pytest.fixture(scope="session")
def test_session_id():
    r = None
    for attempt in range(5):
        r = ov(["session", "new", "-o", "json"], timeout=120)
        if r["exit_code"] == 0:
            break
        time.sleep(5)
    assert r["exit_code"] == 0, f"session new failed: {r['stderr']}"
    session_id = r["json"]["result"]["session_id"]
    yield session_id
    ov(["session", "delete", session_id, "-o", "json"])
