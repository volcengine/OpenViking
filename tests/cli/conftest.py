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
CONFIGURED_API_KEY = os.getenv("OPENVIKING_API_KEY", "")
ROOT_API_KEY = os.getenv("OPENVIKING_ROOT_API_KEY", "")
TRUSTED_IDENTITY_HEADERS = os.getenv("OPENVIKING_TRUSTED_IDENTITY_HEADERS", "").lower() in (
    "1",
    "true",
    "yes",
)
CLI_ACCOUNT = os.getenv("OPENVIKING_ACCOUNT", "") if TRUSTED_IDENTITY_HEADERS else ""
CLI_USER = os.getenv("OPENVIKING_USER", "") if TRUSTED_IDENTITY_HEADERS else ""


def _admin_headers() -> dict[str, str]:
    if not ROOT_API_KEY:
        return {}
    return {"Authorization": f"Bearer {ROOT_API_KEY}"}


def _extract_user_key(users: list[object], user_id: str) -> str:
    for user in users:
        if isinstance(user, dict) and user.get("user_id") == user_id:
            api_key = user.get("api_key")
            if isinstance(api_key, str):
                return api_key
    return ""


def _resolve_api_key() -> str:
    """Resolve a user API key suitable for data-plane operations.

    Root keys (OPENVIKING_API_KEY/OPENVIKING_ROOT_API_KEY) cannot be used for
    data-plane operations like add-skill/ls/read. When only a root key is
    available we need to provision a regular user via admin APIs.

    Returns an empty string if we cannot obtain a valid user key; callers
    should skip tests in that case.
    """
    # Priority 1: Explicit test user key is already a user key - use directly
    explicit_user_key = os.getenv("OPENVIKING_CLI_TEST_API_KEY") or os.getenv(
        "OPENVIKING_USER_API_KEY", ""
    )
    if explicit_user_key:
        return explicit_user_key

    # Priority 2: If we don't have a root key, we can't provision users; return
    # CONFIGURED_API_KEY as-is (it might be a pre-provisioned user key)
    if not ROOT_API_KEY:
        return CONFIGURED_API_KEY

    # Priority 3: Use root key to provision/get a user key via admin API.
    # Retry a few times to handle server startup race conditions.
    account_id = CLI_ACCOUNT or "test-account"
    user_id = CLI_USER or "test-user"

    for attempt in range(5):
        try:
            list_resp = httpx.get(
                f"{BASE_URL}/api/v1/admin/accounts/{account_id}/users",
                headers=_admin_headers(),
                timeout=10.0,
            )
            if list_resp.status_code == 404:
                create_resp = httpx.post(
                    f"{BASE_URL}/api/v1/admin/accounts",
                    headers=_admin_headers(),
                    json={"account_id": account_id, "admin_user_id": user_id},
                    timeout=10.0,
                )
                if create_resp.status_code in (200, 201):
                    user_key = create_resp.json().get("result", {}).get("user_key")
                    if isinstance(user_key, str) and user_key:
                        return user_key
            elif list_resp.status_code == 200:
                users = list_resp.json().get("result", [])
                if isinstance(users, list):
                    user_key = _extract_user_key(users, user_id)
                    if user_key:
                        return user_key
                    user_exists = any(
                        (isinstance(user, dict) and user.get("user_id") == user_id)
                        or (isinstance(user, str) and user == user_id)
                        for user in users
                    )
                else:
                    user_exists = False

                if not user_exists:
                    register_resp = httpx.post(
                        f"{BASE_URL}/api/v1/admin/accounts/{account_id}/users",
                        headers=_admin_headers(),
                        json={"user_id": user_id, "role": "admin"},
                        timeout=10.0,
                    )
                    if register_resp.status_code in (200, 201):
                        user_key = register_resp.json().get("result", {}).get("user_key")
                        if isinstance(user_key, str) and user_key:
                            return user_key
                else:
                    httpx.put(
                        f"{BASE_URL}/api/v1/admin/accounts/{account_id}/users/{user_id}/role",
                        headers=_admin_headers(),
                        json={"role": "admin"},
                        timeout=10.0,
                    )
                    key_resp = httpx.post(
                        f"{BASE_URL}/api/v1/admin/accounts/{account_id}/users/{user_id}/key",
                        headers=_admin_headers(),
                        json={},
                        timeout=10.0,
                    )
                    if key_resp.status_code == 200:
                        user_key = key_resp.json().get("result", {}).get("user_key")
                        if isinstance(user_key, str) and user_key:
                            return user_key
        except Exception:
            pass
        time.sleep(2)

    # Failed to provision a user key after retries - return empty string
    # rather than falling back to root key (which doesn't work for data plane)
    return ""


API_KEY = _resolve_api_key()


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
            capture_output=True,
            text=True,
            timeout=10,
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
        "timeout": 120.0,
        "output": "table",
        "echo_command": True,
        "upload": {},
    }
    if CLI_ACCOUNT:
        config["account"] = CLI_ACCOUNT
    if CLI_USER:
        config["user"] = CLI_USER
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    return config_path


CLI_CONFIG_PATH = _write_cli_config()
ADD_RESOURCE_WAIT_TIMEOUT = "300"
ADD_RESOURCE_COMMAND_TIMEOUT = 360
WRITE_WAIT_TIMEOUT = "300"
WRITE_COMMAND_TIMEOUT = 360


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
            [CLI_BIN, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=_env(),
        )
        if result.returncode != 0 and "GLIBC" in result.stderr:
            return False
        return True
    except Exception:
        return False


CLI_COMPATIBLE = _check_cli_compatible()


def pytest_collection_modifyitems(config, items):
    skip_reason = None
    if not CLI_COMPATIBLE:
        skip_reason = "openviking CLI not available"
        if CLI_BIN is None:
            skip_reason = "openviking CLI binary not found. Install via: curl -fsSL http://openviking.tos-cn-beijing.volces.com/cli/install.sh | bash"
        else:
            try:
                result = subprocess.run(
                    [CLI_BIN, "version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=_env(),
                )
                if "GLIBC" in result.stderr:
                    skip_reason = "openviking CLI binary is not compatible with this system (GLIBC version mismatch)"
            except Exception:
                pass
    elif not API_KEY:
        skip_reason = "Could not obtain a valid user API key for data-plane operations"

    if skip_reason:
        skip_cli = pytest.mark.skip(reason=skip_reason)
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


def is_cli_auth_error(result: dict) -> bool:
    """Check if a CLI result indicates an authentication error.

    Matches both raw API errors (UNAUTHENTICATED/Unauthorized) and the
    user-friendly Rust CLI error card ("Authentication Error" / "rejected the API key").
    """
    stderr = (result.get("stderr") or "").lower()
    stdout = (result.get("stdout") or "").lower()
    combined = stderr + " " + stdout
    auth_markers = [
        "unauthenticated",
        "authentication error",
        "rejected the api key",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "api key invalid",
    ]
    return any(marker in combined for marker in auth_markers)


def skip_if_auth_error(result: dict) -> None:
    """Skip the test if the result indicates an authentication error."""
    if is_cli_auth_error(result):
        pytest.skip("Upstream API authentication unavailable")


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
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_env(),
    )
    stdout = result.stdout.strip()
    data = _parse_cli_json(stdout)
    return {
        "exit_code": result.returncode,
        "json": data,
        "stdout": stdout,
        "stderr": result.stderr.strip(),
    }


def _is_retryable_api_error(result: dict) -> bool:
    """Check if a CLI error is retryable (resource busy, conflict, network issue)."""
    if is_cli_auth_error(result):
        return False
    stderr = (result.get("stderr") or "").lower()
    stdout = (result.get("stdout") or "").lower()
    combined = stderr + " " + stdout
    retryable_markers = [
        "conflict",
        "resource is busy",
        "busy",
        "network error",
        "connection error",
        "could not reach openviking",
        "timeout",
        "temporarily unavailable",
    ]
    return any(marker in combined for marker in retryable_markers)


def _retry_cli_call(args, *, attempts=15, interval=10, timeout=120):
    """Generic CLI call retry helper for write operations.

    Retries on CONFLICT/busy/network errors, skips on auth errors,
    returns immediately on non-retryable errors.
    """
    r = None
    for _attempt in range(attempts):
        r = ov(args, timeout=timeout)
        if r["exit_code"] == 0:
            return r
        skip_if_auth_error(r)
        if _is_retryable_api_error(r):
            time.sleep(interval)
            continue
        # Non-retryable error, return immediately
        return r
    return r


def ov_add_resource(path, to_uri, *extra_args, attempts=15, interval=10):
    """Add a resource with retries for CONFLICT/busy/network errors."""
    args = [
        "add-resource",
        path,
        "--to",
        to_uri,
        *extra_args,
        "--wait",
        "--timeout",
        ADD_RESOURCE_WAIT_TIMEOUT,
        "-o",
        "json",
    ]
    return _retry_cli_call(
        args, attempts=attempts, interval=interval, timeout=ADD_RESOURCE_COMMAND_TIMEOUT
    )


def ov_add_skill(path, *extra_args, attempts=15, interval=10):
    """Add a skill with retries for CONFLICT/busy/network errors."""
    args = [
        "add-skill",
        path,
        *extra_args,
        "--wait",
        "-o",
        "json",
    ]
    return _retry_cli_call(args, attempts=attempts, interval=interval, timeout=120)


def ov_mkdir(uri, *extra_args, attempts=15, interval=5):
    """Create a directory with retries for CONFLICT/busy/network errors."""
    args = ["mkdir", uri, *extra_args, "-o", "json"]
    return _retry_cli_call(args, attempts=attempts, interval=interval, timeout=120)


def ov_retry(args, *, attempts=5, interval=5, timeout=120, retry_if=None):
    r = None
    for attempt in range(attempts):
        r = ov(args, timeout=timeout)
        if r["exit_code"] == 0:
            return r
        skip_if_auth_error(r)
        # Determine if we should retry
        should_retry = True
        if retry_if is not None:
            should_retry = retry_if(r)
        else:
            should_retry = _is_retryable_api_error(r)
        if not should_retry:
            return r
        if attempt < attempts - 1:
            time.sleep(interval)
    return r


def ov_rm(uri, *, recursive=True, attempts=15, interval=5):
    args = ["rm", uri, "-o", "json"]
    if recursive:
        args.insert(2, "-r")
    return ov_retry(args, attempts=attempts, interval=interval)


def ov_mv(src_uri, dst_uri):
    return ov_retry(["mv", src_uri, dst_uri, "-o", "json"], attempts=20, interval=15)


def ov_write(uri, content, *extra_args):
    return ov_retry(
        [
            "write",
            uri,
            "--content",
            content,
            *extra_args,
            "--wait",
            "--timeout",
            WRITE_WAIT_TIMEOUT,
            "-o",
            "json",
        ],
        attempts=15,
        interval=20,
        timeout=WRITE_COMMAND_TIMEOUT,
    )


def ov_reindex(uri):
    return ov_retry(["reindex", uri, "--wait", "true", "-o", "json"], attempts=15, interval=15)


def ov_session_new():
    return ov_retry(["session", "new", "-o", "json"], attempts=5, interval=5)


def ov_session_delete(session_id):
    return ov(["session", "delete", session_id, "-o", "json"])


def _wait_for_resource_ready(uri, retries=20, interval=10):
    for _attempt in range(retries):
        r = ov(["read", uri, "-o", "json"], timeout=30)
        if r["exit_code"] == 0 and len(r["stdout"]) > 0:
            return True
        time.sleep(interval)
    return False


def _find_file_in_pack(pack_uri, retries=10, interval=5):
    for _attempt in range(retries):
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
    r = ov_mkdir("viking://resources")
    if r["exit_code"] != 0:
        if "already exists" in (r.get("stderr") or "").lower():
            return
        skip_if_auth_error(r)
        r2 = ov(["stat", "viking://resources", "-o", "json"])
        if r2["exit_code"] != 0:
            skip_if_auth_error(r2)
            pytest.fail(f"Failed to create resources dir: {r['stderr'][:300]}")


@pytest.fixture(scope="session")
def ensure_user_skills_dir():
    uri = "viking://user/skills"
    r = ov_mkdir(uri)
    if r["exit_code"] == 0 or "already exists" in (r.get("stderr") or "").lower():
        return
    skip_if_auth_error(r)
    stat_r = ov(["stat", uri, "-o", "json"], timeout=120)
    if stat_r["exit_code"] == 0:
        return
    skip_if_auth_error(stat_r)
    pytest.fail(f"mkdir {uri} failed after retries: {r['stderr'][:300]}")


@pytest.fixture(scope="session")
def test_dir_uri(ensure_resources_dir):
    uri = f"viking://resources/cli_test_{uuid.uuid4().hex[:8]}"
    r = ov_mkdir(uri)
    assert r["exit_code"] == 0, f"mkdir failed after retries: {r['stderr'][:300]}"
    yield uri
    ov_rm(uri, attempts=10, interval=5)


@pytest.fixture(scope="session")
def test_pack_uri(test_dir_uri):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("# CLI Test\n\nThis is a test file for CLI automation.")
        temp_path = f.name
    try:
        pack_uri = f"{test_dir_uri}/test_pack"
        r = ov_add_resource(temp_path, pack_uri)
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
    r = ov_session_new()
    assert r["exit_code"] == 0, f"session new failed: {r['stderr']}"
    session_id = r["json"]["result"]["session_id"]
    yield session_id
    ov_session_delete(session_id)
