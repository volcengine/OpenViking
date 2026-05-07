"""Werewolf game server with message routing and Web UI."""

import asyncio
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
import typer
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from loguru import logger

app = typer.Typer()


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class ChatMessage:
    """A chat message in the history."""

    channel_id: str
    content: str
    is_user: bool
    timestamp: float = field(default_factory=lambda: __import__("time").time())


@dataclass
class PendingReply:
    """Track pending player replies."""

    channel_id: str
    message_id: str
    timestamp: float


@dataclass
class GameState:
    """Shared game state."""

    game_id: str
    vikingbot_url: str
    config_path: Path
    running: bool = False
    channels: List[str] = field(default_factory=list)
    messages: List[ChatMessage] = field(default_factory=list)
    session_id: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    storage_path: Path = field(default_factory=Path)
    router_task: Optional[asyncio.Task] = None
    pending_replies: Dict[str, PendingReply] = field(default_factory=dict)
    message_queue: List[Dict[str, Any]] = field(default_factory=list)
    game_ended: bool = False
    remember_sent: bool = False  # Track if /remember command has been sent
    force_restarted: bool = False
    game_mode: str = "all_agents"
    has_human_player: bool = False
    human_player_channel: str = "human"
    waiting_for_human: bool = False
    human_player_message: Optional[str] = None
    human_messages: List[ChatMessage] = field(default_factory=list)
    auto_run_enabled: bool = False
    auto_run_mode: str = "off"
    auto_run_target_games: Optional[int] = None
    auto_run_started_completed_games: int = 0
    auto_restart_scheduled: bool = False
    starting_next_game: bool = False
    completed_games: int = 0
    auto_start_task: Optional[asyncio.Task] = None
    god_no_mention_retry_count: int = 0


# ============================================================================
# Configuration Loading
# ============================================================================


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    content = config_path.read_text(encoding="utf-8")
    return json.loads(content)


def get_bot_channels(config: Dict[str, Any]) -> List[str]:
    """Extract bot_api channel IDs from config."""
    channels = []
    bot_config = config.get("bot", {})
    channel_configs = bot_config.get("channels", [])
    for ch in channel_configs:
        if ch.get("type") == "bot_api" and ch.get("enabled", False):
            ch_id = ch.get("id")
            if ch_id:
                channels.append(ch_id)
    return channels


def get_storage_path(config: Dict[str, Any]) -> Path:
    """Get storage path from config."""
    storage_config = config.get("storage", {})
    bot_config = config.get("bot", {})
    sandbox_config = bot_config.get("sandbox", {})
    storage_workspace = sandbox_config.get("storage_workspace") or storage_config.get(
        "workspace", "~/.openviking/data"
    )
    return Path(storage_workspace).expanduser()


def get_viking_path(config: Dict[str, Any]) -> Path:
    """Get viking path from config (storage.workspace/viking)."""
    storage_path = get_storage_path(config)
    return storage_path / "viking"


def _internal_error_response(message: str = "Internal server error") -> JSONResponse:
    """Return a generic server error without leaking exception details."""
    return JSONResponse(content={"error": message}, status_code=500)


def _failed_operation_response(message: str) -> JSONResponse:
    """Return a generic operation failure without surfacing raw exception text."""
    return JSONResponse(content={"success": False, "error": message})


# ============================================================================
# API Client
# ============================================================================


async def send_to_channel(
    vikingbot_url: str,
    channel_id: str,
    message: str,
    session_id: str,
    user_id: str = "werewolf_server",
    need_reply: bool = True,
    timeout: float = 300.0,
) -> Dict[str, Any]:
    """
    Send a message to a specific bot_api channel.

    Returns:
        The response JSON from vikingbot.
    """
    url = f"{vikingbot_url.rstrip('/')}/bot/v1/chat/channel"
    payload = {
        "message": message,
        "session_id": session_id,
        "user_id": user_id,
        "stream": False,
        "channel_id": channel_id,
        "need_reply": need_reply,
    }

    timeout_config = httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=30.0)

    async with httpx.AsyncClient(timeout=timeout_config) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


# ============================================================================
# Message Parsing
# ============================================================================


def parse_mentions(content: str) -> List[str]:
    """
    Parse @ mentions from message content.

    Returns:
        List of channel IDs mentioned (e.g., ["player_1", "player_2"]).
    """
    pattern = r"@\s*(\w+)"
    mentions = re.findall(pattern, content)
    return mentions


def extract_content_without_mentions(content: str) -> str:
    """
    Extract message content without the @ mentions.

    Returns:
        The pure message content.
    """
    cleaned = re.sub(r"@\s*\w+\s*", "", content)
    return cleaned.strip()


def is_waiting_like_reply(content: str) -> bool:
    """Check whether god reply is an initialization/paused-state reply."""
    text = str(content or "")
    waiting_markers = [
        "初始化完成",
        "初始化已完成",
        "等待开始",
        "等待指令",
        "等待继续",
        '等待"开始"指令',
    ]
    return any(marker in text for marker in waiting_markers)


def parse_game_record(game_record_path: Path) -> Dict[str, Any]:
    """
    Parse GAME_RECORD.md to extract game status information.

    Returns:
        Dict with keys:
        - game_status: "进行中", "游戏结束", or "等待开始"
        - game_result: "狼人胜利", "平民胜利", or None
        - game_time: "白天", "黑夜", or None
        - players: list of dicts with "id", "role", "status"
    """
    result = {"game_status": None, "game_result": None, "game_time": None, "players": []}

    if not game_record_path.exists():
        logger.warning(f"GAME_RECORD.md not found at {game_record_path}")
        return result

    try:
        content = game_record_path.read_text(encoding="utf-8")

        status_match = re.search(r"游戏状态[:：]\s*\[?([^\]/\n]+)\]?", content)
        if status_match:
            result["game_status"] = status_match.group(1).strip()

        result_match = re.search(r"游戏结果[:：]\s*\[?([^\]/\n]+?)\]?\s*$", content, re.MULTILINE)
        if result_match:
            result_str = result_match.group(1).strip()
            if result_str and not result_str.startswith("##"):
                result["game_result"] = result_str

        time_match = re.search(r"游戏时间[:：]\s*\[?([^\]/\n]+)\]?", content)
        if time_match:
            result["game_time"] = time_match.group(1).strip()

        table_match = re.search(r"## 玩家列表\n\|.*?\n(?:\|.*?\n)+", content, re.MULTILINE)
        if table_match:
            table_content = table_match.group(0)
            lines = table_content.strip().split("\n")
            for line in lines[2:]:
                if line.strip().startswith("|"):
                    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                    if len(cells) >= 4:
                        player_id = cells[1]
                        role = cells[2]
                        status = cells[3].strip("[]")
                        if player_id and not all(c == "-" for c in player_id):
                            result["players"].append(
                                {"id": player_id, "role": role, "status": status}
                            )

    except Exception as e:
        logger.warning(f"Error parsing GAME_RECORD.md: {e}")

    return result


def is_game_ended_from_record(game_record_path: Path) -> tuple[bool, str]:
    """
    Check if game has ended by parsing GAME_RECORD.md.

    Returns:
        (is_ended, result) where result is "狼人胜利", "平民胜利", or None
    """
    record = parse_game_record(game_record_path)

    if record["game_status"] == "游戏结束":
        return True, record["game_result"]

    return False, None


def normalize_leaderboard_game_result(game_result: Any) -> str:
    result = str(game_result or "").strip()
    if not result:
        return ""
    if "狼人胜利" in result:
        return "狼人胜利"
    if "好人胜利" in result or "平民胜利" in result or ("狼人" in result and "死亡" in result):
        return "好人胜利"
    return result


def is_wolf_role_for_leaderboard(role: Any) -> bool:
    role_str = str(role or "").strip()
    return role_str in {
        "Werewolf",
        "WhiteWolfKing",
        "WolfCub",
        "WolfBeauty",
        "狼人",
        "白狼王",
        "狼美人",
        "狼崽",
    }


def is_dead_status_for_leaderboard(status: Any) -> bool:
    status_str = str(status or "")
    return any(token in status_str for token in ("死亡", "出局", "淘汰"))


def build_leaderboard_game_from_record(
    parsed_record: Dict[str, Any], *, session_id: str, game_mode: str
) -> Dict[str, Any]:
    winner = normalize_leaderboard_game_result(parsed_record.get("game_result"))
    players = []
    for player in parsed_record.get("players", []):
        role = player.get("role", "")
        dead = is_dead_status_for_leaderboard(player.get("status", ""))
        won = (winner == "狼人胜利" and is_wolf_role_for_leaderboard(role)) or (
            winner == "好人胜利" and not is_wolf_role_for_leaderboard(role)
        )
        score = (2 if won else 0) + (1 if not dead else 0)
        players.append(
            {
                "id": player.get("id", ""),
                "role": role,
                "won": won,
                "dead": dead,
                "score": score,
            }
        )

    return {
        "session_id": session_id,
        "game_mode": game_mode,
        "winner": winner,
        "players": players,
    }


def get_leaderboard_path(storage_path: Path) -> Path:
    leaderboard_dir = storage_path / "bot" / "workspace" / "werewolf"
    leaderboard_dir.mkdir(parents=True, exist_ok=True)
    return leaderboard_dir / "LEADERBOARD.json"


def load_leaderboard(storage_path: Path) -> Dict[str, Any]:
    leaderboard_path = get_leaderboard_path(storage_path)
    if leaderboard_path.exists():
        try:
            return json.loads(leaderboard_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"games": [], "players": {}}


def save_leaderboard(storage_path: Path, data: Dict[str, Any]):
    leaderboard_path = get_leaderboard_path(storage_path)
    leaderboard_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_game_to_leaderboard_from_record(
    *,
    storage_path: Path,
    session_id: str,
    game_mode: str,
) -> Dict[str, Any]:
    if str(game_mode or "") == "human_player":
        return {
            "success": True,
            "skipped": True,
            "reason": "human_player games are excluded from leaderboard",
        }

    if not session_id:
        return {"success": False, "error": "missing_session_id"}

    god_record_path = storage_path / "bot" / "workspace" / "bot_api__god" / "GAME_RECORD.md"
    parsed_record = parse_game_record(god_record_path)
    if parsed_record.get("game_status") != "游戏结束":
        return {"success": False, "error": "game_record_not_finished"}

    game_data = build_leaderboard_game_from_record(
        parsed_record,
        session_id=session_id,
        game_mode=game_mode,
    )
    if not game_data.get("winner"):
        return {"success": False, "error": "missing_game_result"}
    if not game_data.get("players"):
        return {"success": False, "error": "missing_players"}

    leaderboard = load_leaderboard(storage_path)
    if any(
        str(game.get("session_id") or "") == session_id for game in leaderboard.get("games", [])
    ):
        return {
            "success": True,
            "skipped": True,
            "reason": "already_saved",
            "leaderboard": leaderboard,
        }

    leaderboard["games"].append(
        {
            "session_id": session_id,
            "timestamp": time.time(),
            "winner": game_data.get("winner", ""),
            "players": game_data.get("players", []),
        }
    )

    for player in game_data.get("players", []):
        player_id = player.get("id", "")
        if not player_id:
            continue
        if player_id not in leaderboard["players"]:
            leaderboard["players"][player_id] = {
                "id": player_id,
                "games_played": 0,
                "games_won": 0,
                "total_score": 0,
                "roles": {},
            }
        player_stats = leaderboard["players"][player_id]
        player_stats["games_played"] += 1
        if player.get("won", False):
            player_stats["games_won"] += 1
        player_stats["total_score"] += player.get("score", 0)
        role = player.get("role", "未知")
        if role not in player_stats["roles"]:
            player_stats["roles"][role] = 0
        player_stats["roles"][role] += 1

    save_leaderboard(storage_path, leaderboard)
    return {"success": True, "leaderboard": leaderboard}


# ============================================================================
# UI File Initialization
# ============================================================================


def generate_session_id() -> str:
    """Generate a session ID based on current time."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_conversation_to_file(storage_path: Path, session_id: str, messages: List[ChatMessage]):
    """Save conversation history to a file."""
    bot_workspace = storage_path / "bot" / "workspace" / "werewolf"
    bot_workspace.mkdir(parents=True, exist_ok=True)

    file_path = bot_workspace / f"CONVERSATION_{session_id}.md"

    lines = [f"# 狼人杀对话记录 - {session_id}\n"]

    for msg in messages:
        speaker = msg.channel_id
        timestamp = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")
        lines.append(f"\n## [{timestamp}] {speaker}\n")
        lines.append(msg.content)

    file_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Conversation saved to {file_path}")


def get_player_game_md_path(
    storage_path: Path, channel_id: str, human_player_channel: str = "human"
) -> Path:
    """Get GAME.md path for a player channel."""
    bot_workspace = storage_path / "bot" / "workspace"
    if channel_id == human_player_channel:
        return bot_workspace / human_player_channel / "GAME.md"
    return bot_workspace / f"bot_api__{channel_id}" / "GAME.md"


def get_players_snapshot(
    storage_path: Path, channels: List[str], human_player_channel: str = "human"
) -> List[Dict[str, Any]]:
    """Read current players info from GAME.md files."""
    players = []
    player_idx = 1
    for ch in channels:
        if ch == "god":
            continue
        if player_idx > 8:
            break

        game_md_path = get_player_game_md_path(storage_path, ch, human_player_channel)
        role = "未知"
        if game_md_path.exists():
            content = game_md_path.read_text(encoding="utf-8")
            role_match = re.search(r"身份[:：]\s*(.+)", content)
            if role_match:
                role = role_match.group(1).strip()

        players.append(
            {
                "id": ch,
                "seat": player_idx,
                "role": role,
            }
        )
        player_idx += 1
    return players


def get_replay_state_path(storage_path: Path, session_id: str) -> Path:
    """Get replay state archive path for a session."""
    bot_workspace = storage_path / "bot" / "workspace" / "werewolf"
    bot_workspace.mkdir(parents=True, exist_ok=True)
    return bot_workspace / f"REPLAY_STATE_{session_id}.json"


def get_runtime_state_path(storage_path: Path) -> Path:
    """Get runtime state file path used for restart recovery."""
    bot_workspace = storage_path / "bot" / "workspace" / "werewolf"
    bot_workspace.mkdir(parents=True, exist_ok=True)
    return bot_workspace / "RUNTIME_STATE.json"


def save_runtime_state(storage_path: Path, *, game_mode: str) -> Path:
    """Persist lightweight runtime state for server restarts."""
    runtime_state_path = get_runtime_state_path(storage_path)
    payload = {
        "saved_at": time.time(),
        "game_mode": game_mode,
    }
    runtime_state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return runtime_state_path


def load_runtime_state(storage_path: Path) -> Dict[str, Any]:
    """Load lightweight runtime state for server restarts."""
    runtime_state_path = get_runtime_state_path(storage_path)
    if not runtime_state_path.exists():
        return {}

    try:
        return json.loads(runtime_state_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Error loading runtime state from {runtime_state_path}: {e}")
        return {}


def build_replay_base_info(
    game_record_text: str, parsed_record: Dict[str, Any], players: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build minimal replay base info from archived game record and players."""
    dead = []
    record_player_by_id = {player.get("id"): player for player in parsed_record.get("players", [])}
    for idx, player in enumerate(players):
        status = str(record_player_by_id.get(player.get("id"), {}).get("status", ""))
        if any(token in status for token in ["死亡", "出局", "淘汰"]):
            dead.append(idx)

    game_time = str(parsed_record.get("game_time") or "")
    winner = str(parsed_record.get("game_result") or "")
    is_night = "黑夜" in game_time
    phase = "night" if is_night else "day"
    round_match = re.search(r"第\s*(\d+)\s*[轮天]", game_record_text)
    round_value = int(round_match.group(1)) if round_match else None
    return {
        "dead": dead,
        "badge": None,
        "isNight": is_night,
        "round": round_value,
        "day": round_value,
        "winner": winner,
        "phase": phase,
        "lastSection": parsed_record.get("game_status") or "",
    }


def archive_replay_state(
    storage_path: Path, channels: List[str], session_id: str, force: bool = False
) -> Optional[Path]:
    """Archive replay state for a session so replay does not depend on live files."""
    if not session_id:
        return None

    replay_state_path = get_replay_state_path(storage_path, session_id)
    if replay_state_path.exists() and not force:
        return replay_state_path

    god_record_path = storage_path / "bot" / "workspace" / "bot_api__god" / "GAME_RECORD.md"
    if not god_record_path.exists():
        logger.warning(f"Skip replay state archive; GAME_RECORD.md not found at {god_record_path}")
        return None

    game_record = god_record_path.read_text(encoding="utf-8")
    parsed_record = parse_game_record(god_record_path)
    players = get_players_snapshot(storage_path, channels)
    payload = {
        "session_id": session_id,
        "archived_at": time.time(),
        "game_record": game_record,
        "parsed_record": parsed_record,
        "players": players,
        "base_info": build_replay_base_info(game_record, parsed_record, players),
    }
    replay_state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"Replay state archived to {replay_state_path}")
    return replay_state_path


def build_restart_message(state: GameState) -> str:
    """Build the restart/init message for god."""
    player_list = []
    player_idx = 1
    for ch in state.channels:
        if ch != "god" and player_idx <= 8:
            game_md_path = get_player_game_md_path(
                state.storage_path, ch, state.human_player_channel
            )
            player_list.append(f"{player_idx}号: {ch}，GAME.md地址：{game_md_path}")
            player_idx += 1

    bot_workspace = state.storage_path / "bot" / "workspace"

    return f"""重新开始游戏

游戏配置：
- 玩家数: {len(player_list)}
- 玩家列表:
{chr(10).join(f"  - {p}" for p in player_list)}
- GAME_RECORD.md 位置：{bot_workspace}/bot_api__god/GAME_RECORD.md
- 对话记录位置：{bot_workspace}/werewolf/CONVERSATION_{state.session_id}.md

请初始化游戏文件，然后等待\"开始\"指令。"""


def ensure_human_workspace(storage_path: Path, human_channel: str = "human") -> Path:
    """Ensure the human player workspace and GAME.md exist."""
    human_workspace = storage_path / "bot" / "workspace" / human_channel
    human_workspace.mkdir(parents=True, exist_ok=True)
    human_game_md = human_workspace / "GAME.md"
    if not human_game_md.exists():
        human_game_md.write_text(
            "# 真实玩家游戏文件\n\n请编辑此文件来设置你的角色和状态。\n", encoding="utf-8"
        )
    return human_game_md


def build_channels_for_game_mode(
    config: Dict[str, Any], game_mode: str, human_channel: str = "human"
) -> List[str]:
    """Build channel list for the requested game mode."""
    channels = get_bot_channels(config)
    if game_mode != "human_player":
        return channels

    if len(channels) > 1 and "god" in channels:
        non_god_channels = [ch for ch in channels if ch != "god"]
        if len(non_god_channels) > 0:
            channels = ["god"] + non_god_channels[:-1]
        channels.append(human_channel)
    return channels


def apply_game_mode_to_state(state: GameState, game_mode: str) -> None:
    """Apply game mode settings to state."""
    state.game_mode = game_mode
    state.has_human_player = game_mode == "human_player"
    state.human_player_channel = "human"
    state.channels = build_channels_for_game_mode(
        state.config, game_mode, state.human_player_channel
    )
    state.waiting_for_human = False
    state.human_player_message = None
    state.human_messages.clear()
    save_runtime_state(state.storage_path, game_mode=state.game_mode)
    if state.has_human_player:
        human_game_md = ensure_human_workspace(state.storage_path, state.human_player_channel)
        logger.info(f"Human player mode enabled. Channels: {state.channels}")
        logger.info(f"Human player GAME.md at: {human_game_md}")


def cancel_auto_start_task(state: GameState):
    """Cancel pending auto-start task if any."""
    current_task = asyncio.current_task()
    if (
        state.auto_start_task
        and not state.auto_start_task.done()
        and state.auto_start_task is not current_task
    ):
        state.auto_start_task.cancel()
    if state.auto_start_task is not current_task:
        state.auto_start_task = None


def get_completed_games_in_auto_run(state: GameState) -> int:
    """Return how many games have finished since current auto-run plan started."""
    return max(0, state.completed_games - state.auto_run_started_completed_games)


def get_remaining_auto_run_games(state: GameState) -> Optional[int]:
    """Return remaining games in fixed mode, None for non-fixed modes."""
    if (
        not state.auto_run_enabled
        or state.auto_run_mode != "fixed"
        or not state.auto_run_target_games
    ):
        return None
    return max(0, state.auto_run_target_games - get_completed_games_in_auto_run(state))


def should_continue_auto_run(state: GameState) -> bool:
    """Check whether auto-run should continue into another game."""
    if not state.auto_run_enabled:
        return False
    if state.auto_run_mode == "infinite":
        return True
    if state.auto_run_mode == "fixed":
        remaining = get_remaining_auto_run_games(state)
        return bool(remaining and remaining > 0)
    return False


def disable_auto_run(state: GameState):
    """Disable auto-run and clear related transient state."""
    state.auto_run_enabled = False
    state.auto_run_mode = "off"
    state.auto_run_target_games = None
    state.auto_run_started_completed_games = state.completed_games
    state.auto_restart_scheduled = False
    state.starting_next_game = False
    cancel_auto_start_task(state)


async def stop_router_task(state: GameState):
    """Stop current router task if it is running."""
    if state.running and state.router_task:
        state.running = False
        state.router_task.cancel()
        try:
            await state.router_task
        except asyncio.CancelledError:
            pass
    state.router_task = None


async def begin_game_loop(
    state: GameState, initial_message: str, *, reset_game_flags: bool = False
) -> None:
    """Start the router loop with a god message."""
    if "god" not in state.channels:
        raise ValueError("god channel not found")

    if reset_game_flags:
        state.game_ended = False
        state.remember_sent = False
        state.force_restarted = False

    state.running = True
    state.router_task = asyncio.create_task(
        message_router_loop(state, initial_channel="god", initial_message=initial_message)
    )


async def restart_game_session(state: GameState) -> str:
    """Create a new session and run the init step for a new game."""
    await stop_router_task(state)
    cancel_auto_start_task(state)

    if state.session_id:
        save_conversation_to_file(state.storage_path, state.session_id, state.messages)
        archive_replay_state(state.storage_path, state.channels, state.session_id, force=True)

    state.session_id = generate_session_id()
    state.messages.clear()
    state.human_messages.clear()
    state.waiting_for_human = False
    state.human_player_message = None
    state.game_ended = False
    state.remember_sent = False
    state.force_restarted = True
    state.auto_restart_scheduled = False

    restart_message = build_restart_message(state)
    await begin_game_loop(state, restart_message)
    return state.session_id


async def start_current_game(state: GameState) -> None:
    """Send the standard start command for the current session."""
    await stop_router_task(state)
    cancel_auto_start_task(state)
    await begin_game_loop(state, "开始", reset_game_flags=True)


async def continue_current_game(state: GameState) -> None:
    """Continue the current paused game."""
    await stop_router_task(state)
    cancel_auto_start_task(state)
    await begin_game_loop(state, "继续本局游戏")


def schedule_next_game(state: GameState, delay_seconds: float = 1.5):
    """Schedule automatic restart + start for the next game."""
    if (
        not should_continue_auto_run(state)
        or state.auto_restart_scheduled
        or state.starting_next_game
    ):
        return

    state.auto_restart_scheduled = True

    async def _auto_start_next_game():
        try:
            logger.info(f"Auto-run scheduling next game in {delay_seconds}s")
            await asyncio.sleep(delay_seconds)

            if not should_continue_auto_run(state):
                logger.info("Auto-run no longer eligible before next game started; skipping")
                return

            state.starting_next_game = True
            await restart_game_session(state)

            init_router_task = state.router_task
            if init_router_task:
                logger.info("Auto-run waiting for god initialization reply before sending start")
                try:
                    await init_router_task
                except asyncio.CancelledError:
                    logger.info("Initialization router task was cancelled before auto start")
                    return

            if not should_continue_auto_run(state):
                logger.info("Auto-run no longer eligible after restart; skipping auto start")
                return

            await start_current_game(state)
            logger.info(f"Auto-run started next game, session_id={state.session_id}")
        except asyncio.CancelledError:
            logger.info("Auto-run next-game task cancelled")
        except Exception as e:
            logger.exception(f"Error during auto-run next game: {e}")
        finally:
            state.starting_next_game = False
            state.auto_restart_scheduled = False
            state.auto_start_task = None

    state.auto_start_task = asyncio.create_task(_auto_start_next_game())


def load_latest_conversation(storage_path: Path) -> tuple[List[ChatMessage], Optional[str]]:
    """Load the latest conversation from files. Returns (messages, session_id)."""
    bot_workspace = storage_path / "bot" / "workspace" / "werewolf"
    if not bot_workspace.exists():
        return [], None

    # Find all conversation files
    conversation_files = sorted(
        bot_workspace.glob("CONVERSATION_*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    )

    if not conversation_files:
        return [], None

    latest_file = conversation_files[0]
    logger.info(f"Loading latest conversation from {latest_file}")

    # Extract session_id from filename (CONVERSATION_20260331_153000.md)
    session_id = latest_file.stem.replace("CONVERSATION_", "")

    content = latest_file.read_text(encoding="utf-8")
    messages = []

    # Parse the markdown format
    current_speaker = None
    current_content = []

    for line in content.split("\n"):
        line = line.rstrip()
        # Match section header: ## [HH:MM:SS] speaker
        match = re.match(r"^## \[([0-9:]+)\] (.+)$", line)
        if match:
            # Save previous message
            if current_speaker and current_content:
                # Try to parse timestamp, or use current time
                try:
                    # We don't have the date, just use current time
                    ts = time.time()
                except ValueError:
                    ts = time.time()

                messages.append(
                    ChatMessage(
                        channel_id=current_speaker,
                        content="\n".join(current_content).strip(),
                        is_user=(current_speaker == "admin"),
                        timestamp=ts,
                    )
                )

            # Start new message
            current_speaker = match.group(2)
            current_content = []
        elif line and not line.startswith("# "):
            # Content line
            if current_speaker is not None:
                current_content.append(line)

    # Save the last message
    if current_speaker and current_content:
        try:
            ts = time.time()
        except ValueError:
            ts = time.time()

        messages.append(
            ChatMessage(
                channel_id=current_speaker,
                content="\n".join(current_content).strip(),
                is_user=(current_speaker == "admin"),
                timestamp=ts,
            )
        )

    logger.info(
        f"Loaded {len(messages)} messages from latest conversation, session_id={session_id}"
    )
    return messages, session_id


def init_ui_files(storage_path: Path, channels: List[str], game_id: str = "default"):
    """Initialize UI file structure.

    Note: Actual game files are maintained by the agents themselves in:
    - {storage_path}/bot/workspace/bot_api__god/GAME_RECORD.md (god's record)
    - {storage_path}/bot/workspace/bot_api__player_*/GAME.md (player files)
    """
    # Just ensure the bot/workspace directory exists
    bot_workspace_path = storage_path / "bot" / "workspace"
    bot_workspace_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"UI files initialized in {storage_path}")
    logger.info(f"  Looking for game records in: {bot_workspace_path}/bot_api__god/GAME_RECORD.md")


# ============================================================================
# New Helper Functions for Multi-Player Routing
# ============================================================================


async def broadcast_to_players(
    state: GameState,
    message: str,
    mentioned_players: List[str],
    sender_id: str = "god",
) -> List[Dict[str, Any]]:
    """
    Broadcast message to all players:
    - Mentioned players: need_reply=True, wait for reply
    - Other players: need_reply=False, just receive
    - Human player: if mentioned, wait for user input

    Returns:
        List of replies from mentioned players
    """
    import time

    tasks = []
    reply_channels = []
    all_player_channels = []
    human_mentioned = False

    # Build player seat number map first
    player_seat_map = {}
    seat_idx = 1
    for ch in state.channels:
        if ch == "god":
            continue
        player_seat_map[ch] = seat_idx
        seat_idx += 1

    # Get sender prefix
    if sender_id == "god":
        sender_prefix = "god："
    else:
        sender_seat = player_seat_map.get(sender_id, sender_id)
        sender_prefix = f"{sender_seat}号："

    # Check if human player is mentioned
    if state.has_human_player and state.human_player_channel in mentioned_players:
        human_mentioned = True
        mentioned_players = [ch for ch in mentioned_players if ch != state.human_player_channel]
        logger.info("Human player mentioned, will wait for user input")

    for ch in state.channels:
        if ch == "god":
            continue
        if ch == state.human_player_channel:
            # Skip human player in bot broadcast
            all_player_channels.append(ch)
            continue
        all_player_channels.append(ch)

        is_mentioned = ch in mentioned_players

        message_for_player = f"{sender_prefix}{message}"

        # Create send task with sender_id
        task = send_to_channel(
            vikingbot_url=state.vikingbot_url,
            channel_id=ch,
            message=message_for_player,
            session_id=state.session_id,
            user_id=sender_id,
            need_reply=is_mentioned,
        )
        tasks.append(task)

        if is_mentioned:
            reply_channels.append(ch)

    logger.info(
        f"Broadcasting to {len(all_player_channels)} players, {len(reply_channels)} need reply: {reply_channels}"
    )

    # Send all messages concurrently (don't record internal broadcasts)
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Record replies from players (these are visible)
    replies = []
    result_idx = 0
    for ch in all_player_channels:
        if ch == state.human_player_channel:
            continue
        result = results[result_idx] if result_idx < len(results) else None
        result_idx += 1

        is_mentioned = ch in reply_channels

        # If this player was mentioned and replied successfully, record the reply
        if is_mentioned and not isinstance(result, Exception):
            response_content = result.get("message", "") or ""
            state.messages.append(
                ChatMessage(
                    channel_id=ch,
                    content=response_content,
                    is_user=False,
                    timestamp=time.time(),
                )
            )
            logger.info(f"Received reply from {ch}: {response_content[:100]}...")
            replies.append({"channel_id": ch, "response": result})

    # Handle human player
    if human_mentioned:
        logger.info("Waiting for human player input...")
        state.waiting_for_human = True

        human_message = f"{sender_prefix}{message}"
        state.human_messages.append(
            ChatMessage(
                channel_id="god",
                content=human_message,
                is_user=False,
                timestamp=time.time(),
            )
        )

        # Wait for human player to respond
        while state.waiting_for_human and state.running:
            await asyncio.sleep(0.1)

        if state.human_player_message:
            reply_content = state.human_player_message
            state.human_player_message = None
            replies.append(
                {"channel_id": state.human_player_channel, "response": {"message": reply_content}}
            )
            logger.info(f"Received human player reply: {reply_content[:100]}...")

    # Save after receiving player replies
    if state.session_id:
        save_conversation_to_file(state.storage_path, state.session_id, state.messages)

    return replies


async def broadcast_message_to_players(
    state: GameState,
    message: str,
    sender_id: str,
    exclude_players: List[str] = None,
):
    """
    Broadcast a message to all players (except excluded ones) with need_reply=False.
    """
    if exclude_players is None:
        exclude_players = []

    # Build player seat number map
    player_seat_map = {}
    seat_idx = 1
    for ch in state.channels:
        if ch == "god":
            continue
        player_seat_map[ch] = seat_idx
        seat_idx += 1

    # Get sender's seat number
    if sender_id == "god":
        sender_prefix = "god："
    else:
        sender_seat = player_seat_map.get(sender_id, sender_id)
        sender_prefix = f"{sender_seat}号："

    # Collect all player channels except excluded
    tasks = []
    for ch in state.channels:
        if ch == "god":
            continue
        if ch in exclude_players:
            continue

        message_for_player = f"{sender_prefix}{message}"

        task = send_to_channel(
            vikingbot_url=state.vikingbot_url,
            channel_id=ch,
            message=message_for_player,
            session_id=state.session_id,
            user_id=sender_id,
            need_reply=False,
        )
        tasks.append(task)

    if tasks:
        logger.info(f"Broadcasting message from {sender_id} to {len(tasks)} players")
        await asyncio.gather(*tasks, return_exceptions=True)


def build_message_for_god(player_replies: List[Dict[str, Any]], all_channels: List[str]) -> str:
    """Build a message for god from player replies."""
    if not player_replies:
        return "没有玩家回复"

    # Build seat number map first
    player_seat_map = {}
    seat_idx = 1
    for ch in all_channels:
        if ch == "god":
            continue
        player_seat_map[ch] = seat_idx
        seat_idx += 1

    parts = []
    for reply in player_replies:
        content = reply.get("response", {}).get("message", "")
        channel_id = reply["channel_id"]
        if channel_id == "god":
            parts.append(f"god：{content}")
        else:
            seat_num = player_seat_map.get(channel_id, channel_id)
            parts.append(f"{seat_num}号：{content}")

    return "\n".join(parts)


# ============================================================================
# Message Router (Rewritten)
# ============================================================================


async def message_router_loop(
    state: GameState,
    initial_channel: str = "god",
    initial_message: str = "开始",
    is_admin_initiated: bool = True,
):
    """
    Main message routing loop - rewritten for multi-player support.

    Flow:
    1. Send message to god (or current channel)
    2. Parse @ mentions from god's reply
    3. Broadcast to all players:
       - @mentioned players: need_reply=True
       - others: need_reply=False
    4. Collect replies from mentioned players
    5. Send replies back to god
    6. Repeat...
    """
    import time

    logger.info(f"Starting message router for game {state.game_id}")

    current_channel = initial_channel
    current_message = initial_message
    loop_count = 0
    max_loops = 1000
    current_sender_id = "admin"  # Track who is sending this message

    try:
        while state.running and loop_count < max_loops:
            loop_count += 1

            # 0. Record admin message only for the first round (admin -> god)
            if loop_count == 1:
                msg_timestamp = time.time()
                state.messages.append(
                    ChatMessage(
                        channel_id="admin",
                        content=current_message,
                        is_user=True,
                        timestamp=msg_timestamp,
                    )
                )
                # Save immediately so UI can show it
                if state.session_id:
                    save_conversation_to_file(state.storage_path, state.session_id, state.messages)

            # 1. Send message to current channel (usually god)
            logger.info(
                f"Sending to {current_channel} (from {current_sender_id}): {current_message[:100]}..."
            )
            try:
                response = await send_to_channel(
                    vikingbot_url=state.vikingbot_url,
                    channel_id=current_channel,
                    message=current_message,
                    session_id=state.session_id,
                    user_id=current_sender_id,
                    need_reply=True,
                )
            except Exception as e:
                logger.exception(f"Error sending to {current_channel}: {e}")
                await asyncio.sleep(1)
                continue

            # 2. Get response content
            response_content = response.get("message", "") or ""
            logger.info(f"Received from {current_channel}: {response_content[:100]}...")

            # 3. Record agent's reply (always record agent responses)
            state.messages.append(
                ChatMessage(
                    channel_id=current_channel,
                    content=response_content,
                    is_user=False,
                    timestamp=time.time(),
                )
            )

            # 4. Parse mentions and pure content early (needed for game end broadcasting)
            mentions = parse_mentions(response_content)
            pure_content = extract_content_without_mentions(response_content)
            if not pure_content:
                pure_content = current_message

            # Check if game has ended by parsing GAME_RECORD.md
            god_record_path = (
                state.storage_path / "bot" / "workspace" / "bot_api__god" / "GAME_RECORD.md"
            )
            has_game_ended, game_result = is_game_ended_from_record(god_record_path)
            valid_mentions = [m for m in mentions if m in state.channels and m != "god"]
            if valid_mentions:
                state.god_no_mention_retry_count = 0

            # Only treat the game as fully ended after god has produced the final conclusion
            # and no longer asks any player for follow-up actions.
            if (
                current_channel == "god"
                and has_game_ended
                and not state.remember_sent
                and not valid_mentions
            ):
                state.game_ended = True
                logger.info(
                    f"Game ended detected from GAME_RECORD.md after final god reply, result: {game_result}"
                )

                if pure_content:
                    await broadcast_message_to_players(
                        state=state,
                        message=pure_content,
                        sender_id="god",
                        exclude_players=[],
                    )
                    logger.info("Game end message broadcasted to all players")

                if state.session_id:
                    save_conversation_to_file(state.storage_path, state.session_id, state.messages)
                    archive_replay_state(
                        state.storage_path, state.channels, state.session_id, force=True
                    )

                leaderboard_result = save_game_to_leaderboard_from_record(
                    storage_path=state.storage_path,
                    session_id=state.session_id,
                    game_mode=state.game_mode,
                )
                if leaderboard_result.get("success"):
                    if leaderboard_result.get("skipped"):
                        logger.info(
                            f"Leaderboard save skipped: {leaderboard_result.get('reason', 'unknown')}"
                        )
                    else:
                        logger.info("Leaderboard saved from authoritative GAME_RECORD.md")
                else:
                    logger.warning(
                        f"Failed to save leaderboard from GAME_RECORD.md: {leaderboard_result.get('error', 'unknown')}"
                    )

                try:
                    await send_to_channel(
                        vikingbot_url=state.vikingbot_url,
                        channel_id="god",
                        message="/remember",
                        session_id=state.session_id,
                        user_id="system",
                        need_reply=False,
                    )
                    logger.info("/remember sent to god")

                    for ch in [channel for channel in state.channels if channel != "god"]:
                        try:
                            await send_to_channel(
                                vikingbot_url=state.vikingbot_url,
                                channel_id=ch,
                                message="/remember",
                                session_id=state.session_id,
                                user_id="system",
                                need_reply=False,
                            )
                            logger.info(f"/remember sent to {ch}")
                        except Exception as e:
                            logger.exception(f"Error sending /remember to {ch}: {e}")

                    state.remember_sent = True
                    state.completed_games += 1
                    logger.info(
                        f"/remember sent to all, game complete! completed_games={state.completed_games}"
                    )
                except Exception as e:
                    logger.exception(f"Error sending /remember: {e}")

                if should_continue_auto_run(state):
                    schedule_next_game(state)
                elif state.auto_run_enabled:
                    logger.info("Auto-run target reached; disabling auto-run")
                    disable_auto_run(state)

                break

            if current_channel == "god" and has_game_ended and valid_mentions:
                logger.info(
                    f"GAME_RECORD indicates end ({game_result}), but god is still waiting on players: {valid_mentions}; delaying end handling"
                )

            # 5. Save conversation to file
            if state.session_id:
                save_conversation_to_file(state.storage_path, state.session_id, state.messages)

            waiting_like_reply = is_waiting_like_reply(response_content)
            if (
                not valid_mentions
                and current_channel == "god"
                and not has_game_ended
                and not waiting_like_reply
            ):
                if state.god_no_mention_retry_count >= 2:
                    logger.warning(
                        "God reply still has no valid player mention after fallback retries; stopping router"
                    )
                    break

                state.god_no_mention_retry_count += 1
                current_channel = "god"
                current_message = "你上个回复没有@任何玩家，检查下如果游戏结束，更新GAME_RECORD.md信息后，再回复结束信息；如果游戏没结束，继续@一个玩家进行"
                current_sender_id = "admin_fallback_no_mention"
                logger.warning(
                    f"God reply missing valid player mention while game not ended; sending admin fallback retry #{state.god_no_mention_retry_count}"
                )
                await asyncio.sleep(0.1)
                continue

            if not mentions:
                # 如果没有 @ 提及，可能是游戏初始化完成，等待继续
                if waiting_like_reply:
                    logger.info("Game initialized, waiting for start command...")
                    state.god_no_mention_retry_count = 0
                    break  # 退出循环，等待下次调用
                else:
                    logger.info("No mentions in response, waiting for next command")
                    break

            # Validate mentioned channels exist
            if not valid_mentions:
                logger.warning(
                    f"No valid player mentions in: {mentions}, available: {state.channels}"
                )
                break

            logger.info(f"Mentioned players: {valid_mentions}")

            # 6. Broadcast to all players - sender is current_channel (god)
            player_replies = await broadcast_to_players(
                state=state,
                message=pure_content,
                mentioned_players=valid_mentions,
                sender_id=current_channel,
            )

            if not player_replies:
                logger.warning("No player replies received")
                break

            # 7. First, broadcast player replies to all other players (need_reply=False)
            if player_replies:
                for reply in player_replies:
                    reply_channel = reply["channel_id"]
                    reply_content = reply.get("response", {}).get("message", "")
                    if reply_content and reply_channel != state.human_player_channel:
                        await broadcast_message_to_players(
                            state=state,
                            message=reply_content,
                            sender_id=reply_channel,
                            exclude_players=[reply_channel],
                        )

            # 8. Build message for god from player replies
            god_message = build_message_for_god(player_replies, state.channels)
            logger.info(f"Sending player replies back to god: {god_message[:100]}...")

            # 9. Next iteration: send replies back to god
            current_channel = "god"
            current_message = god_message
            # Use specific player_id as sender_id (use first player if multiple replies)
            if player_replies:
                current_sender_id = player_replies[0]["channel_id"]
            else:
                current_sender_id = "players"

            await asyncio.sleep(0.1)

        if loop_count >= max_loops:
            logger.warning(f"Reached max loops ({max_loops}), stopping router")

    except asyncio.CancelledError:
        logger.info("Message router cancelled")
    except Exception as e:
        logger.exception(f"Error in message router: {e}")
    finally:
        state.running = False
        logger.info("Message router stopped")


# ============================================================================
# Web UI - FastAPI
# ============================================================================


def create_fastapi_app(state: GameState) -> FastAPI:
    """Create the FastAPI application for the Web UI."""
    fastapi_app = FastAPI(title="Werewolf Game Server")

    ui_html_path = Path(__file__).parent / "werewolfUI.html"
    ui_html = ""
    if ui_html_path.exists():
        ui_html = ui_html_path.read_text(encoding="utf-8")
    else:
        ui_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Werewolf Game</title></head>
        <body><h1>Werewolf UI not found</h1></body>
        </html>
        """

    test_html_path = Path(__file__).parent / "test_server.html"
    test_html = ""
    if test_html_path.exists():
        test_html = test_html_path.read_text(encoding="utf-8")

    debug_html_path = Path(__file__).parent / "debug.html"
    debug_html = ""
    if debug_html_path.exists():
        debug_html = debug_html_path.read_text(encoding="utf-8")

    @fastapi_app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the main UI page."""
        return HTMLResponse(content=ui_html)

    @fastapi_app.get("/test", response_class=HTMLResponse)
    async def test_page():
        """Serve the test/control panel page."""
        return HTMLResponse(content=test_html)

    @fastapi_app.get("/debug", response_class=HTMLResponse)
    async def debug_page():
        """Serve the debug page."""
        return HTMLResponse(content=debug_html)

    @fastapi_app.get("/fonts/{filename}")
    async def serve_font(filename: str):
        """Serve font files."""
        font_root = Path(__file__).parent.resolve()
        try:
            font_path = (font_root / filename).resolve()
        except OSError:
            return HTMLResponse(content="", status_code=404)
        if font_path.is_relative_to(font_root) and font_path.is_file():
            return FileResponse(font_path)
        return HTMLResponse(content="", status_code=404)

    @fastapi_app.get("/api/status")
    async def get_status():
        """Get current game status."""
        # Report current status without mutating game flow state.
        # The router loop owns the end-of-game transition so god's final reply
        # can still be received, broadcast to players, and followed by /remember.

        return JSONResponse(
            content={
                "game_id": state.game_id,
                "session_id": state.session_id,
                "running": state.running,
                "channels": state.channels,
                "message_count": len(state.messages),
                "smart_buttons": state.config.get("smart_buttons", False),
                "game_ended": state.game_ended,
                "force_restarted": state.force_restarted,
                "game_mode": state.game_mode,
                "has_human_player": state.has_human_player,
                "human_player_channel": state.human_player_channel,
                "waiting_for_human": state.waiting_for_human,
                "auto_run_enabled": state.auto_run_enabled,
                "auto_run_mode": state.auto_run_mode,
                "auto_run_target_games": state.auto_run_target_games,
                "auto_run_remaining_games": get_remaining_auto_run_games(state),
                "auto_restart_scheduled": state.auto_restart_scheduled,
                "starting_next_game": state.starting_next_game,
                "completed_games": state.completed_games,
            }
        )

    @fastapi_app.get("/api/human/messages")
    async def get_human_messages():
        """Get human private chat history."""
        if not state.has_human_player:
            return JSONResponse(content={"messages": []})

        return JSONResponse(
            content={
                "messages": [
                    {
                        "channel_id": msg.channel_id,
                        "content": msg.content,
                        "is_user": msg.is_user,
                        "timestamp": msg.timestamp,
                    }
                    for msg in state.human_messages
                ]
            }
        )

    @fastapi_app.post("/api/human/send")
    async def send_human_message(payload: dict):
        """Send a message from human player."""
        if not state.has_human_player:
            return JSONResponse(
                content={"success": False, "error": "Human player mode not enabled"}
            )
        if not state.waiting_for_human:
            return JSONResponse(content={"success": False, "error": "Not waiting for human input"})

        message = str(payload.get("message", "")).strip()
        if not message:
            return JSONResponse(content={"success": False, "error": "Message is empty"})

        target = str(payload.get("target") or "god").strip().lower()
        if target not in {"god", "all"}:
            return JSONResponse(content={"success": False, "error": "Invalid target"})

        import time

        timestamp = time.time()
        state.human_messages.append(
            ChatMessage(
                channel_id=state.human_player_channel,
                content=message,
                is_user=True,
                timestamp=timestamp,
            )
        )

        if target == "all":
            state.messages.append(
                ChatMessage(
                    channel_id=state.human_player_channel,
                    content=message,
                    is_user=False,
                    timestamp=timestamp,
                )
            )
            await broadcast_message_to_players(
                state,
                message,
                sender_id=state.human_player_channel,
                exclude_players=[state.human_player_channel],
            )

        state.human_player_message = message
        state.waiting_for_human = False

        if state.session_id:
            save_conversation_to_file(state.storage_path, state.session_id, state.messages)

        return JSONResponse(content={"success": True, "target": target})

    @fastapi_app.get("/api/human/game-md")
    async def get_human_game_md():
        """Get human player's GAME.md content."""
        if not state.has_human_player:
            return JSONResponse(content={"error": "Human player mode not enabled"}, status_code=400)

        human_game_md = state.storage_path / "bot" / "workspace" / "human" / "GAME.md"
        if not human_game_md.exists():
            return JSONResponse(
                content={"content": "# 真实玩家游戏文件\n\n请编辑此文件来设置你的角色和状态。\n"}
            )

        return JSONResponse(content={"content": human_game_md.read_text(encoding="utf-8")})

    @fastapi_app.post("/api/human/game-md")
    async def save_human_game_md(payload: dict):
        """Save human player's GAME.md content."""
        if not state.has_human_player:
            return JSONResponse(content={"error": "Human player mode not enabled"}, status_code=400)

        content = payload.get("content", "")
        human_game_md = state.storage_path / "bot" / "workspace" / "human" / "GAME.md"
        human_game_md.parent.mkdir(parents=True, exist_ok=True)
        human_game_md.write_text(content, encoding="utf-8")

        return JSONResponse(content={"success": True})

    @fastapi_app.get("/api/messages")
    async def get_messages():
        """Get full message history."""
        return JSONResponse(
            content={
                "messages": [
                    {
                        "channel_id": msg.channel_id,
                        "content": msg.content,
                        "is_user": msg.is_user,
                        "timestamp": msg.timestamp,
                    }
                    for msg in state.messages
                ]
            }
        )

    @fastapi_app.get("/api/players")
    async def get_players():
        """Get player info, reading roles from GAME.md."""
        players = get_players_snapshot(
            state.storage_path, state.channels, state.human_player_channel
        )
        return JSONResponse(content={"players": players})

    @fastapi_app.post("/api/start")
    async def start_game(payload: Optional[Dict[str, Any]] = None):
        """Start the game."""
        if state.running or (state.router_task and not state.router_task.done()):
            return JSONResponse(content={"success": False, "error": "Game already running"})

        requested_game_mode = str(
            (payload or {}).get("game_mode") or state.game_mode or "all_agents"
        ).strip()
        if requested_game_mode not in {"all_agents", "human_player"}:
            return JSONResponse(content={"success": False, "error": "Invalid game_mode"})

        apply_game_mode_to_state(state, requested_game_mode)

        try:
            await start_current_game(state)
        except ValueError:
            logger.warning("Failed to start game")
            return _failed_operation_response("Failed to start game")

        return JSONResponse(
            content={"success": True, "session_id": state.session_id, "game_mode": state.game_mode}
        )

    @fastapi_app.post("/api/restart")
    async def restart_game(payload: Optional[Dict[str, Any]] = None):
        """Restart the game."""
        requested_game_mode = str(
            (payload or {}).get("game_mode") or state.game_mode or "all_agents"
        ).strip()
        if requested_game_mode not in {"all_agents", "human_player"}:
            return JSONResponse(content={"success": False, "error": "Invalid game_mode"})

        apply_game_mode_to_state(state, requested_game_mode)

        try:
            session_id = await restart_game_session(state)
        except ValueError:
            logger.warning("Failed to restart game")
            return _failed_operation_response("Failed to restart game")

        return JSONResponse(
            content={"success": True, "session_id": session_id, "game_mode": state.game_mode}
        )

    @fastapi_app.post("/api/continue")
    async def continue_game():
        """Continue the current game by nudging god to proceed."""
        if state.running or (state.router_task and not state.router_task.done()):
            return JSONResponse(content={"success": False, "error": "Game already running"})

        if state.game_ended:
            return JSONResponse(content={"success": False, "error": "Game already ended"})

        try:
            await continue_current_game(state)
        except ValueError:
            logger.warning("Failed to continue game")
            return _failed_operation_response("Failed to continue game")

        return JSONResponse(content={"success": True, "session_id": state.session_id})

    @fastapi_app.post("/api/stop")
    async def stop_game():
        """Stop the game."""
        if not state.running and not state.auto_restart_scheduled and not state.starting_next_game:
            return JSONResponse(content={"success": False, "error": "Game not running"})

        disable_auto_run(state)
        await stop_router_task(state)

        return JSONResponse(content={"success": True})

    @fastapi_app.post("/api/auto-run")
    async def set_auto_run(payload: Dict[str, Any]):
        """Enable or disable automatic continuous games."""
        enabled = bool(payload.get("enabled", False))
        if not enabled:
            disable_auto_run(state)
            return JSONResponse(
                content={
                    "success": True,
                    "auto_run_enabled": state.auto_run_enabled,
                    "auto_run_mode": state.auto_run_mode,
                    "auto_run_target_games": state.auto_run_target_games,
                    "auto_run_remaining_games": get_remaining_auto_run_games(state),
                    "auto_restart_scheduled": state.auto_restart_scheduled,
                    "starting_next_game": state.starting_next_game,
                    "completed_games": state.completed_games,
                }
            )

        mode = str(payload.get("mode") or "fixed").strip().lower()
        target_games_raw = payload.get("target_games")

        if mode not in {"fixed", "infinite"}:
            return JSONResponse(
                content={"success": False, "error": "auto-run mode must be 'fixed' or 'infinite'"}
            )

        target_games = None
        if mode == "fixed":
            try:
                target_games = int(target_games_raw)
            except (TypeError, ValueError):
                return JSONResponse(
                    content={"success": False, "error": "target_games must be an integer >= 1"}
                )
            if target_games < 1:
                return JSONResponse(
                    content={"success": False, "error": "target_games must be an integer >= 1"}
                )

        state.auto_run_enabled = True
        state.auto_run_mode = mode
        state.auto_run_target_games = target_games
        state.auto_run_started_completed_games = state.completed_games
        state.auto_restart_scheduled = False
        state.starting_next_game = False
        cancel_auto_start_task(state)

        if not state.running and (not state.router_task or state.router_task.done()):
            schedule_next_game(state, delay_seconds=0.1)

        return JSONResponse(
            content={
                "success": True,
                "auto_run_enabled": state.auto_run_enabled,
                "auto_run_mode": state.auto_run_mode,
                "auto_run_target_games": state.auto_run_target_games,
                "auto_run_remaining_games": get_remaining_auto_run_games(state),
                "auto_restart_scheduled": state.auto_restart_scheduled,
                "starting_next_game": state.starting_next_game,
                "completed_games": state.completed_games,
            }
        )

    @fastapi_app.get("/api/openviking/tree")
    async def get_openviking_tree():
        """Get OpenViking memory directory tree structure.

        Returns the tree structure of:
        - {viking_path}/default/agent/
        - {viking_path}/default/user/
        """
        viking_path = get_viking_path(state.config)
        default_path = viking_path / "default"

        tree = {
            "agent": {"path": str(default_path / "agent"), "files": []},
            "user": {"path": str(default_path / "user"), "files": []},
        }

        # Scan agent directory
        agent_path = default_path / "agent"
        if agent_path.exists():
            tree["agent"]["files"] = scan_directory_tree(agent_path, "")

        # Scan user directory
        user_path = default_path / "user"
        if user_path.exists():
            tree["user"]["files"] = scan_directory_tree(user_path, "")

        return JSONResponse(content=tree)

    @fastapi_app.get("/api/openviking/file")
    async def get_openviking_file(path: str):
        """Get a specific file from OpenViking memory.

        Path format: "agent/subpath/file.md" or "user/subpath/file.md"
        """
        default_root = (get_viking_path(state.config) / "default").resolve()
        try:
            file_path = (default_root / path).resolve()
        except OSError:
            return JSONResponse(content={"error": "File not found"}, status_code=404)

        if not file_path.is_relative_to(default_root):
            return JSONResponse(content={"error": "File not found"}, status_code=404)

        if not file_path.exists():
            return JSONResponse(content={"error": "File not found"}, status_code=404)

        if file_path.is_dir():
            return JSONResponse(content={"error": "Path is a directory"}, status_code=400)

        try:
            content = file_path.read_text(encoding="utf-8")
            return JSONResponse(content={"path": path, "content": content, "name": file_path.name})
        except Exception:
            logger.exception("Failed to read OpenViking file")
            return _internal_error_response("Failed to read file")

    @fastapi_app.get("/api/conversations")
    async def get_conversations():
        """Get list of conversation files."""
        bot_workspace = state.storage_path / "bot" / "workspace" / "werewolf"

        if not bot_workspace.exists():
            return JSONResponse(content={"files": []})

        conversation_files = sorted(
            bot_workspace.glob("CONVERSATION_*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        )

        files = []
        for f in conversation_files:
            session_id = f.stem.replace("CONVERSATION_", "")
            files.append(
                {
                    "session_id": session_id,
                    "filename": f.name,
                    "modified": f.stat().st_mtime,
                    "size": f.stat().st_size,
                }
            )

        return JSONResponse(content={"files": files, "current_session_id": state.session_id})

    @fastapi_app.get("/api/conversation/{session_id}")
    async def get_conversation(session_id: str):
        """Get a specific conversation file content."""
        bot_workspace = state.storage_path / "bot" / "workspace" / "werewolf"
        file_path = bot_workspace / f"CONVERSATION_{session_id}.md"

        if not file_path.exists():
            return JSONResponse(content={"error": "Conversation not found"}, status_code=404)

        try:
            content = file_path.read_text(encoding="utf-8")
            return JSONResponse(
                content={"session_id": session_id, "content": content, "filename": file_path.name}
            )
        except Exception:
            logger.exception("Failed to read conversation file")
            return _internal_error_response("Failed to read conversation")

    @fastapi_app.get("/api/replay-state/{session_id}")
    async def get_replay_state(session_id: str):
        """Get archived replay state for a session."""
        replay_state_path = get_replay_state_path(state.storage_path, session_id)
        if not replay_state_path.exists():
            return JSONResponse(content={"error": "Replay state not found"}, status_code=404)

        try:
            payload = json.loads(replay_state_path.read_text(encoding="utf-8"))
            return JSONResponse(content=payload)
        except Exception:
            logger.exception("Failed to load replay state")
            return _internal_error_response("Failed to load replay state")

    @fastapi_app.get("/api/bot-sessions")
    async def get_bot_sessions():
        """Get list of bot session files."""
        sessions_path = state.storage_path / "bot" / "sessions"

        if not sessions_path.exists():
            return JSONResponse(content={"files": [], "current_session_id": state.session_id})

        session_files = sorted(
            sessions_path.glob("bot_api__*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )

        files = []
        for f in session_files:
            # Parse filename: bot_api__player_1__20260331_203954.jsonl
            parts = f.stem.split("__")
            if len(parts) >= 3:
                channel_id = parts[1]
                file_session_id = "__".join(parts[2:])
            else:
                channel_id = "unknown"
                file_session_id = f.stem

            files.append(
                {
                    "channel_id": channel_id,
                    "session_id": file_session_id,
                    "filename": f.name,
                    "modified": f.stat().st_mtime,
                    "size": f.stat().st_size,
                }
            )

        return JSONResponse(content={"files": files, "current_session_id": state.session_id})

    @fastapi_app.get("/api/bot-session/{filename}")
    async def get_bot_session(filename: str):
        """Get a specific bot session file content."""
        sessions_path = state.storage_path / "bot" / "sessions"
        file_path = sessions_path / filename

        if not file_path.exists():
            return JSONResponse(content={"error": "Session file not found"}, status_code=404)

        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
            # Format JSONL as readable text
            formatted_lines = []
            for line in lines:
                if line.strip():
                    try:
                        data = json.loads(line)
                        if data.get("_type") == "metadata":
                            formatted_lines.append("# 会话元数据\n")
                            formatted_lines.append(f"- 创建时间: {data.get('created_at', '')}")
                            formatted_lines.append(f"- 更新时间: {data.get('updated_at', '')}")
                            formatted_lines.append("")
                        else:
                            role = data.get("role", "")
                            content = data.get("content", "")
                            timestamp = data.get("timestamp", "")
                            sender = data.get("sender_id", "")

                            if role == "user":
                                formatted_lines.append(f"## <user> ({sender}) - {timestamp}")
                            elif role == "assistant":
                                formatted_lines.append(f"## <assistant> ({sender}) - {timestamp}")
                            else:
                                formatted_lines.append(f"## {role} - {timestamp}")

                            formatted_lines.append("")
                            formatted_lines.append(content)

                            # Add token usage if available
                            token_usage = data.get("token_usage")
                            if token_usage:
                                formatted_lines.append("")
                                formatted_lines.append(
                                    f"*Token使用: prompt={token_usage.get('prompt_tokens', 0)}, completion={token_usage.get('completion_tokens', 0)}, total={token_usage.get('total_tokens', 0)}*"
                                )

                            formatted_lines.append("")
                    except json.JSONDecodeError:
                        formatted_lines.append(line)

            content = "\n".join(formatted_lines)
            return JSONResponse(
                content={
                    "filename": filename,
                    "content": content,
                    "raw_content": file_path.read_text(encoding="utf-8"),
                }
            )
        except Exception:
            logger.exception("Failed to read bot session file")
            return _internal_error_response("Failed to read session file")

    @fastapi_app.get("/api/game-file/{channel_id}/{filename}")
    async def get_game_file(channel_id: str, filename: str):
        """Get a game file (GAME.md or GAME_RECORD.md) for a channel."""
        bot_workspace = state.storage_path / "bot" / "workspace"

        if channel_id == "god" and filename == "GAME_RECORD.md":
            file_path = bot_workspace / "bot_api__god" / "GAME_RECORD.md"
        else:
            file_path = bot_workspace / f"bot_api__{channel_id}" / filename

        if not file_path.exists():
            return JSONResponse(content={"error": "File not found"}, status_code=404)

        try:
            content = file_path.read_text(encoding="utf-8")
            return JSONResponse(
                content={"channel_id": channel_id, "filename": filename, "content": content}
            )
        except Exception:
            logger.exception("Failed to read game file")
            return _internal_error_response("Failed to read game file")

    @fastapi_app.get("/api/leaderboard")
    async def get_leaderboard():
        """Get leaderboard data."""
        return JSONResponse(content=load_leaderboard(state.storage_path))

    @fastapi_app.post("/api/leaderboard/save")
    async def save_game_to_leaderboard(game_data: Dict[str, Any]):
        """Save a completed game to leaderboard using authoritative GAME_RECORD.md data."""
        result = save_game_to_leaderboard_from_record(
            storage_path=state.storage_path,
            session_id=str(game_data.get("session_id") or state.session_id or ""),
            game_mode=str(game_data.get("game_mode") or state.game_mode or ""),
        )
        status_code = 200 if result.get("success") else 400
        return JSONResponse(content=result, status_code=status_code)

    def scan_directory_tree(root_path: Path, relative_path: str) -> List[Dict[str, Any]]:
        """Recursively scan a directory and build a tree structure."""
        result = []

        try:
            for item in sorted(root_path.iterdir()):
                if item.name.startswith(".") and item.is_file():
                    # Skip hidden files but include .abstract.md and .overview.md
                    if item.name not in [".abstract.md", ".overview.md"]:
                        continue

                item_rel_path = f"{relative_path}/{item.name}" if relative_path else item.name

                if item.is_dir():
                    result.append(
                        {
                            "name": item.name,
                            "path": item_rel_path,
                            "type": "directory",
                            "children": scan_directory_tree(item, item_rel_path),
                        }
                    )
                else:
                    result.append(
                        {
                            "name": item.name,
                            "path": item_rel_path,
                            "type": "file",
                            "size": item.stat().st_size,
                        }
                    )
        except Exception as e:
            logger.warning(f"Error scanning directory {root_path}: {e}")

        return result

    @fastapi_app.get("/data/{path:path}")
    async def serve_data_file(path: str):
        """Serve files from storage folder.

        Priority:
        1. /data/ -> list bot/workspace/
        2. /data/werewolf/GAME_RECORD.md -> bot_api__god/GAME_RECORD.md
        3. /data/* -> bot/workspace/*
        4. /data/* -> storage_path/*
        """
        storage_root = state.storage_path.resolve()
        bot_workspace = (storage_root / "bot" / "workspace").resolve()

        def resolve_under(base_dir: Path, relative_path: str) -> Optional[Path]:
            try:
                candidate = (base_dir / relative_path).resolve()
            except OSError:
                return None
            if not candidate.is_relative_to(base_dir):
                return None
            return candidate

        # Special case: root path - list bot/workspace
        if path == "" or path == "/":
            return await list_directory(bot_workspace, "")

        # Special handling for werewolf/GAME_RECORD.md - map to god's GAME_RECORD.md
        if path == "werewolf/GAME_RECORD.md":
            god_record_path = resolve_under(bot_workspace, "bot_api__god/GAME_RECORD.md")
            if god_record_path and god_record_path.is_file():
                return FileResponse(god_record_path)

        # First try: bot/workspace/{path}
        workspace_path = resolve_under(bot_workspace, path)
        if workspace_path and workspace_path.exists():
            if workspace_path.is_file():
                return FileResponse(workspace_path)
            elif workspace_path.is_dir():
                return await list_directory(workspace_path, path)

        # Second try: storage_path/{path}
        file_path = resolve_under(storage_root, path)
        if file_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)

        dir_path = resolve_under(storage_root, path)
        if dir_path and dir_path.exists() and dir_path.is_dir():
            return await list_directory(dir_path, path)

        return HTMLResponse(content="", media_type="text/markdown", status_code=404)

    async def list_directory(dir_path: Path, url_path: str):
        """Generate a simple HTML directory listing."""
        clean_segments = [segment for segment in url_path.split("/") if segment]
        parent_segments = clean_segments[:-1]
        parent_href = "/data/" + "/".join(quote(segment, safe="") for segment in parent_segments)
        if not parent_segments:
            parent_href = "/data/"
        entries = []
        for item in sorted(dir_path.iterdir()):
            href_segments = clean_segments + [item.name]
            href = "/data/" + "/".join(quote(segment, safe="") for segment in href_segments)
            display_name = item.name
            if item.is_dir():
                href += "/"
                display_name += "/"
            entries.append(
                f'<a href="{html.escape(href, quote=True)}">{html.escape(display_name)}</a><br>'
            )

        html_content = f"""
        <!DOCTYPE html>
        <html><body>
        <h1>/{html.escape(url_path)}</h1>
        {f'<a href="{html.escape(parent_href, quote=True)}">../</a><br>' if url_path else ""}
        {"".join(entries)}
        </body></html>
        """
        return HTMLResponse(content=html_content)

    return fastapi_app


# ============================================================================
# Main Command
# ============================================================================


def main(
    port: int = typer.Option(1995, "--port", "-p", help="UI port"),
    vikingbot_url: str = typer.Option(
        "http://localhost:18790", "--vikingbot-url", help="Vikingbot API URL"
    ),
    config_path: str = typer.Option(
        "~/.openviking/ov-multi.conf", "--config", "-c", help="Config file path"
    ),
    game_id: str = typer.Option("default", "--game-id", help="Game ID"),
    smart_buttons: bool = typer.Option(
        False, "--smart-buttons", "-s", help="Enable smart button visibility control"
    ),
    game_mode: str = typer.Option(
        "all_agents", "--game-mode", "-m", help="Game mode: all_agents or human_player"
    ),
):
    """Start the werewolf game server."""
    import uvicorn

    config_path_resolved = Path(config_path).expanduser()

    logger.info(f"Loading config from {config_path_resolved}")
    config = load_config(config_path_resolved)
    logger.info(f"Loaded channels: {get_bot_channels(config)}")

    storage_path = get_storage_path(config)
    logger.info(f"Storage path: {storage_path}")

    runtime_state = load_runtime_state(storage_path)
    persisted_game_mode = str(runtime_state.get("game_mode") or "").strip()
    if persisted_game_mode in {"all_agents", "human_player"} and game_mode == "all_agents":
        game_mode = persisted_game_mode
        logger.info(f"Restored game mode from runtime state: {game_mode}")

    channels = build_channels_for_game_mode(config, game_mode)
    if game_mode == "human_player":
        human_game_md = ensure_human_workspace(storage_path)
        logger.info(f"Human player mode enabled. Channels: {channels}")
        logger.info(f"Human player GAME.md at: {human_game_md}")

    init_ui_files(storage_path, channels, game_id)

    # Load latest conversation from previous session
    previous_messages = []
    previous_session_id = None
    try:
        previous_messages, previous_session_id = load_latest_conversation(storage_path)
    except Exception as e:
        logger.exception(f"Error loading previous conversation: {e}")

    # Generate initial session ID, or use previous one if available
    if previous_session_id:
        initial_session_id = previous_session_id
        logger.info(f"Using previous session ID: {initial_session_id}")
    else:
        initial_session_id = generate_session_id()
        logger.info(f"Generated new session ID: {initial_session_id}")

    state = GameState(
        game_id=game_id,
        vikingbot_url=vikingbot_url,
        config_path=config_path_resolved,
        channels=channels,
        config=config,
        storage_path=storage_path,
        session_id=initial_session_id,
        game_mode=game_mode,
    )

    # Store smart_buttons in config for easy access
    state.config["smart_buttons"] = smart_buttons
    apply_game_mode_to_state(state, game_mode)

    # Load messages if available
    if previous_messages:
        state.messages.extend(previous_messages)
        logger.info(f"Loaded {len(previous_messages)} messages from previous session")

    fastapi_app = create_fastapi_app(state)

    logger.info(f"Starting Werewolf Server on port {port}")
    logger.info(f"UI will be available at http://localhost:{port}")
    logger.info(f"Storage path: {storage_path}")
    logger.info(f"Bot workspace: {storage_path / 'bot' / 'workspace'}")
    logger.info(
        f"Checking for GAME_RECORD.md: {storage_path / 'bot' / 'workspace' / 'bot_api__god' / 'GAME_RECORD.md'}"
    )

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    typer.run(main)
