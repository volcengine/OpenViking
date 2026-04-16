from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx


PLAYER_IDS = [f"player_{i}" for i in range(1, 7)]
WEREWOLF_CHANNEL_IDS = ["god", *PLAYER_IDS]
BOT_API_TYPE = "bot_api"
DEFAULT_CONFIG_PATH = Path("~/.openviking/ov-multi.conf").expanduser()
DEFAULT_WORKSPACE = Path("~/.openviking/data").expanduser()
DEFAULT_VIKINGBOT_URL = "http://localhost:18790"
SCRIPT_DIR = Path(__file__).resolve().parent
SOUL_GOD_PATH = SCRIPT_DIR / "SOUL-god.md"
SOUL_PLAYER_PATH = SCRIPT_DIR / "SOUL-player.md"
GOD_SOUL_FILENAME = "SOUL.md"
PLAYER_SOUL_FILENAME = "SOUL.md"
WEREWOLF_SERVER_PATH = SCRIPT_DIR / "werewolf_server.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure and start the werewolf demo")
    parser.add_argument(
        "--config",
        "-c",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to ov.conf (default: ~/.openviking/ov-multi.conf)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=1995,
        help="Port for the werewolf UI server",
    )
    parser.add_argument(
        "--game-id",
        default="default",
        help="Game ID passed to werewolf_server.py",
    )
    parser.add_argument(
        "--game-mode",
        default="all_agents",
        choices=["all_agents", "human_player"],
        help="Game mode passed to werewolf_server.py",
    )
    parser.add_argument(
        "--smart-buttons",
        action="store_true",
        help="Enable smart buttons in the werewolf UI",
    )
    parser.add_argument(
        "--server-host",
        default="127.0.0.1",
        help="Host for OpenViking server",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=1933,
        help="Port for OpenViking server",
    )
    parser.add_argument(
        "--vikingbot-url",
        default=DEFAULT_VIKINGBOT_URL,
        help="Vikingbot gateway URL used by werewolf_server.py",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for the Vikingbot gateway health check",
    )
    return parser.parse_args()


def load_json_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json_config(config_path: Path, data: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def ensure_werewolf_channels(full_config: dict[str, Any]) -> None:
    bot_config = full_config.setdefault("bot", {})
    existing_channels = bot_config.get("channels")
    if not isinstance(existing_channels, list):
        existing_channels = []

    filtered_channels: list[dict[str, Any]] = []
    for channel in existing_channels:
        if not isinstance(channel, dict):
            filtered_channels.append(channel)
            continue
        if channel.get("type") == BOT_API_TYPE and channel.get("id") in WEREWOLF_CHANNEL_IDS:
            continue
        filtered_channels.append(channel)

    demo_channels = [
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "god",
            "ov_tools_enable": False,
        },
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "player_1",
            "profile_user_list": ["player_2", "player_3", "player_4", "player_5", "player_6"],
            "memory_user": "player_1",
        },
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "player_2",
            "profile_user_list": ["player_1", "player_3", "player_4", "player_5", "player_6"],
            "memory_user": "player_2",
        },
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "player_3",
            "profile_user_list": ["player_1", "player_2", "player_4", "player_5", "player_6"],
            "memory_user": "player_3",
        },
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "player_4",
            "ov_tools_enable": False,
        },
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "player_5",
            "ov_tools_enable": False,
        },
        {
            "type": BOT_API_TYPE,
            "enabled": True,
            "id": "player_6",
            "ov_tools_enable": False,
        },
    ]
    bot_config["channels"] = [*filtered_channels, *demo_channels]


def resolve_workspace(full_config: dict[str, Any]) -> Path:
    storage = full_config.get("storage")
    if not isinstance(storage, dict):
        storage = {}
        full_config["storage"] = storage

    workspace = storage.get("workspace")
    if not workspace:
        workspace = str(DEFAULT_WORKSPACE)
        storage["workspace"] = workspace

    return Path(workspace).expanduser().resolve()


def ensure_sandbox(full_config: dict[str, Any], workspace: Path) -> None:
    bot_config = full_config.setdefault("bot", {})
    sandbox = bot_config.get("sandbox")
    if not isinstance(sandbox, dict):
        sandbox = {}
        bot_config["sandbox"] = sandbox

    sandbox["mode"] = "per-channel"
    restrict_workspaces = sandbox.get("restrictWorkspaces")
    if not isinstance(restrict_workspaces, dict):
        restrict_workspaces = {}
        sandbox["restrictWorkspaces"] = restrict_workspaces

    restrict_workspaces["bot_api__god"] = str((workspace / "bot").resolve())


def prepare_workspace(workspace: Path) -> None:
    bot_workspace = workspace / "bot" / "workspace"
    bot_workspace.mkdir(parents=True, exist_ok=True)

    for channel_id in WEREWOLF_CHANNEL_IDS:
        channel_dir = bot_workspace / f"bot_api__{channel_id}"
        channel_dir.mkdir(parents=True, exist_ok=True)
        if channel_id == "god":
            shutil.copyfile(SOUL_GOD_PATH, channel_dir / GOD_SOUL_FILENAME)
        else:
            shutil.copyfile(SOUL_PLAYER_PATH, channel_dir / PLAYER_SOUL_FILENAME)


def validate_assets() -> None:
    missing = [str(path) for path in (SOUL_GOD_PATH, SOUL_PLAYER_PATH, WEREWOLF_SERVER_PATH) if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required demo files: " + ", ".join(missing))


def wait_for_health(url: str, timeout_seconds: float) -> None:
    deadline = time.time() + timeout_seconds
    last_error = "unknown error"

    with httpx.Client(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            time.sleep(1)

    raise RuntimeError(f"Timed out waiting for Vikingbot health check at {url}: {last_error}")


def terminate_process(process: subprocess.Popen[str], name: str) -> None:
    if process.poll() is not None:
        return
    print(f"Stopping {name} (pid={process.pid})...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def start_processes(args: argparse.Namespace, config_path: Path) -> int:
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(config_path)

    openviking_cmd = [
        "openviking-server",
        "--config",
        str(config_path),
        "--host",
        args.server_host,
        "--port",
        str(args.server_port),
        "--with-bot",
        "--bot-url",
        args.vikingbot_url,
    ]
    werewolf_cmd = [
        sys.executable,
        str(WEREWOLF_SERVER_PATH),
        "--config",
        str(config_path),
        "--port",
        str(args.ui_port),
        "--game-id",
        args.game_id,
        "--game-mode",
        args.game_mode,
        "--vikingbot-url",
        args.vikingbot_url,
    ]
    if args.smart_buttons:
        werewolf_cmd.append("--smart-buttons")

    print(f"Using config: {config_path}")
    print(f"Starting OpenViking: {' '.join(openviking_cmd)}")
    openviking_process = subprocess.Popen(openviking_cmd, env=env)

    try:
        wait_for_health(f"{args.vikingbot_url.rstrip('/')}/bot/v1/health", args.startup_timeout)
        print(f"Starting werewolf server: {' '.join(werewolf_cmd)}")
        werewolf_process = subprocess.Popen(werewolf_cmd, env=env)
    except Exception:
        terminate_process(openviking_process, "OpenViking")
        raise

    processes = [
        ("werewolf server", werewolf_process),
        ("OpenViking", openviking_process),
    ]

    try:
        while True:
            for name, process in processes:
                returncode = process.poll()
                if returncode is not None:
                    print(f"{name} exited with code {returncode}")
                    for other_name, other_process in processes:
                        if other_process is not process:
                            terminate_process(other_process, other_name)
                    return returncode
            time.sleep(1)
    except KeyboardInterrupt:
        print("Received interrupt, shutting down...")
        for name, process in processes:
            terminate_process(process, name)
        return 130


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()

    validate_assets()
    full_config = load_json_config(config_path)
    workspace = resolve_workspace(full_config)
    ensure_werewolf_channels(full_config)
    ensure_sandbox(full_config, workspace)
    save_json_config(config_path, full_config)
    prepare_workspace(workspace)

    print(f"Workspace: {workspace}")
    print(f"Prepared channels: {', '.join(WEREWOLF_CHANNEL_IDS)}")
    return start_processes(args, config_path)


if __name__ == "__main__":
    raise SystemExit(main())
