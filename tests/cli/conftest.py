# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""CLI fixtures that run against a real OpenViking server process."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import httpx
import pytest

LOCAL_NO_PROXY_ENV = {
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "ALL_PROXY": "",
    "all_proxy": "",
}


def _get_free_port() -> int:
    """Reserve a free port for the test server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_health(url: str, timeout_s: float = 20.0) -> None:
    """Poll the health endpoint until the server is ready."""
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url}/health", timeout=1.0, trust_env=False)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"OpenViking server failed to start: {last_error}")


def _resolve_base_conf_path() -> Path:
    """Resolve base example config path with backward-compatible fallback."""
    candidates = (Path("examples/ov.conf"), Path("examples/ov.conf.example"))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "No base config found, expected one of: "
        + ", ".join(str(path) for path in candidates)
    )


def _ensure_agfs_binary_available() -> None:
    """Skip integration tests when AGFS local binary is unavailable."""
    binary_name = "agfs-server.exe" if os.name == "nt" else "agfs-server"
    binary_path = (Path("openviking") / "bin" / binary_name).resolve()
    if not binary_path.exists():
        pytest.skip(f"AGFS binary not found: {binary_path}")


@pytest.fixture(scope="session")
def openviking_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """Start a real OpenViking server for CLI tests."""
    _ensure_agfs_binary_available()

    storage_dir = tmp_path_factory.mktemp("openviking_cli_data")
    port = _get_free_port()

    # Load the base example config and override storage path + server port
    base_conf_path = _resolve_base_conf_path()
    with open(base_conf_path, encoding="utf-8") as f:
        conf_data = json.load(f)

    conf_data.setdefault("server", {})
    conf_data["server"]["host"] = "127.0.0.1"
    conf_data["server"]["port"] = port

    conf_data.setdefault("storage", {})
    conf_data["storage"].setdefault("vectordb", {})
    conf_data["storage"]["vectordb"]["backend"] = "local"
    conf_data["storage"]["vectordb"]["path"] = str(storage_dir)
    conf_data["storage"].setdefault("agfs", {})
    conf_data["storage"]["agfs"]["backend"] = "local"
    conf_data["storage"]["agfs"]["path"] = str(storage_dir)

    # Write temporary ov.conf
    tmp_conf = storage_dir / "ov.conf"
    with open(tmp_conf, "w") as f:
        json.dump(conf_data, f)

    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(tmp_conf)
    env.update(LOCAL_NO_PROXY_ENV)

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
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
