"""Werewolf game server with message routing and Web UI."""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import typer
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from loguru import logger
from dataclasses import dataclass, field

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
    timestamp: float = field(default_factory=lambda: __import__('time').time())


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
    force_restarted: bool = False
    has_human_player: bool = False
    human_player_channel: str = "human"
    waiting_for_human: bool = False
    human_player_message: Optional[str] = None


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
    storage_workspace = sandbox_config.get("storage_workspace") or storage_config.get("workspace", "~/.openviking/data")
    return Path(storage_workspace).expanduser()


def get_viking_path(config: Dict[str, Any]) -> Path:
    """Get viking path from config (storage.workspace/viking)."""
    storage_path = get_storage_path(config)
    return storage_path / "viking"


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


def load_latest_conversation(storage_path: Path) -> tuple[List[ChatMessage], Optional[str]]:
    """Load the latest conversation from files. Returns (messages, session_id)."""
    bot_workspace = storage_path / "bot" / "workspace" / "werewolf"
    if not bot_workspace.exists():
        return [], None

    # Find all conversation files
    conversation_files = sorted(
        bot_workspace.glob("CONVERSATION_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
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
    current_timestamp = None
    current_content = []

    for line in content.split("\n"):
        line = line.rstrip()
        # Match section header: ## [HH:MM:SS] speaker
        match = re.match(r'^## \[([0-9:]+)\] (.+)$', line)
        if match:
            # Save previous message
            if current_speaker and current_content:
                # Try to parse timestamp, or use current time
                try:
                    # We don't have the date, just use current time
                    ts = time.time()
                except ValueError:
                    ts = time.time()

                messages.append(ChatMessage(
                    channel_id=current_speaker,
                    content="\n".join(current_content).strip(),
                    is_user=(current_speaker == "admin"),
                    timestamp=ts
                ))

            # Start new message
            time_str = match.group(1)
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

        messages.append(ChatMessage(
            channel_id=current_speaker,
            content="\n".join(current_content).strip(),
            is_user=(current_speaker == "admin"),
            timestamp=ts
        ))

    logger.info(f"Loaded {len(messages)} messages from latest conversation, session_id={session_id}")
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
        logger.info(f"Human player mentioned, will wait for user input")

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

    logger.info(f"Broadcasting to {len(all_player_channels)} players, {len(reply_channels)} need reply: {reply_channels}")

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
            state.messages.append(ChatMessage(
                channel_id=ch,
                content=response_content,
                is_user=False,
                timestamp=time.time(),
            ))
            logger.info(f"Received reply from {ch}: {response_content[:100]}...")
            replies.append({
                "channel_id": ch,
                "response": result
            })

    # Handle human player
    if human_mentioned:
        logger.info(f"Waiting for human player input...")
        state.waiting_for_human = True

        # Record the message to human player
        human_message = f"{sender_prefix}{message}"
        state.messages.append(ChatMessage(
            channel_id=sender_id,
            content=human_message,
            is_user=False,
            timestamp=time.time(),
        ))
        if state.session_id:
            save_conversation_to_file(state.storage_path, state.session_id, state.messages)

        # Wait for human player to respond
        while state.waiting_for_human and state.running:
            await asyncio.sleep(0.1)

        if state.human_player_message:
            # Add human reply
            reply_content = state.human_player_message
            state.human_player_message = None
            replies.append({
                "channel_id": state.human_player_channel,
                "response": {"message": reply_content}
            })
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
        channel_id = reply['channel_id']
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
                state.messages.append(ChatMessage(
                    channel_id="admin",
                    content=current_message,
                    is_user=True,
                    timestamp=msg_timestamp,
                ))
                # Save immediately so UI can show it
                if state.session_id:
                    save_conversation_to_file(state.storage_path, state.session_id, state.messages)

            # 1. Send message to current channel (usually god)
            logger.info(f"Sending to {current_channel} (from {current_sender_id}): {current_message[:100]}...")
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
            state.messages.append(ChatMessage(
                channel_id=current_channel,
                content=response_content,
                is_user=False,
                timestamp=time.time(),
            ))

            # 4. Save conversation to file
            if state.session_id:
                save_conversation_to_file(state.storage_path, state.session_id, state.messages)

            # 4. Parse mentions from response
            mentions = parse_mentions(response_content)
            pure_content = extract_content_without_mentions(response_content)
            if not pure_content:
                pure_content = current_message

            if not mentions:
                # 如果没有 @ 提及，可能是游戏初始化完成，等待继续
                if "初始化完成" in response_content or "等待" in response_content:
                    logger.info("Game initialized, waiting for start command...")
                    # 保持运行，但不继续发送消息，等待外部命令
                    await asyncio.sleep(1)
                    break  # 退出循环，等待下次调用
                else:
                    logger.info("No mentions in response, waiting for next command")
                    break

            # Validate mentioned channels exist
            valid_mentions = [m for m in mentions if m in state.channels and m != "god"]
            if not valid_mentions:
                logger.warning(f"No valid player mentions in: {mentions}, available: {state.channels}")
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
                    reply_channel = reply['channel_id']
                    reply_content = reply.get("response", {}).get("message", "")
                    if reply_content:
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
        font_path = Path(__file__).parent / filename
        if font_path.exists():
            return FileResponse(font_path)
        return HTMLResponse(content="", status_code=404)

    @fastapi_app.get("/api/status")
    async def get_status():
        """Get current game status."""
        # Check if game has ended by looking at GAME_RECORD.md
        if not state.game_ended:
            god_record_path = state.storage_path / "bot" / "workspace" / "bot_api__god" / "GAME_RECORD.md"
            if god_record_path.exists():
                try:
                    content = god_record_path.read_text(encoding="utf-8")
                    if "游戏结束" in content:
                        state.game_ended = True
                        # Also set running to False when game ends
                        if state.running:
                            state.running = False
                except Exception:
                    pass

        return JSONResponse(content={
            "game_id": state.game_id,
            "session_id": state.session_id,
            "running": state.running,
            "channels": state.channels,
            "message_count": len(state.messages),
            "smart_buttons": state.config.get("smart_buttons", False),
            "game_ended": state.game_ended,
            "force_restarted": state.force_restarted,
            "has_human_player": state.has_human_player,
            "human_player_channel": state.human_player_channel,
            "waiting_for_human": state.waiting_for_human,
        })

    @fastapi_app.post("/api/human/send")
    async def send_human_message(payload: dict):
        """Send a message from human player."""
        if not state.has_human_player:
            return JSONResponse(content={"success": False, "error": "Human player mode not enabled"})

        message = payload.get("message", "")
        if not message:
            return JSONResponse(content={"success": False, "error": "Message is empty"})

        import time
        # Record human message
        state.messages.append(ChatMessage(
            channel_id=state.human_player_channel,
            content=message,
            is_user=True,
            timestamp=time.time(),
        ))
        if state.session_id:
            save_conversation_to_file(state.storage_path, state.session_id, state.messages)

        # Set the message for the router
        state.human_player_message = message
        state.waiting_for_human = False

        return JSONResponse(content={"success": True})

    @fastapi_app.get("/api/human/game-md")
    async def get_human_game_md():
        """Get human player's GAME.md content."""
        if not state.has_human_player:
            return JSONResponse(content={"error": "Human player mode not enabled"}, status_code=400)

        human_game_md = state.storage_path / "bot" / "workspace" / "human" / "GAME.md"
        if not human_game_md.exists():
            return JSONResponse(content={"content": "# 真实玩家游戏文件\n\n请编辑此文件来设置你的角色和状态。\n"})

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
        return JSONResponse(content={
            "messages": [
                {
                    "channel_id": msg.channel_id,
                    "content": msg.content,
                    "is_user": msg.is_user,
                    "timestamp": msg.timestamp
                }
                for msg in state.messages
            ]
        })

    @fastapi_app.get("/api/players")
    async def get_players():
        """Get player info, reading roles from GAME.md."""
        players = []
        player_idx = 1
        for ch in state.channels:
            if ch == "god":
                continue
            if player_idx > 8:
                break

            # Read this player's GAME.md
            game_md_path = state.storage_path / "bot" / "workspace" / f"bot_api__{ch}" / "GAME.md"
            role = "未知"
            if game_md_path.exists():
                content = game_md_path.read_text(encoding="utf-8")
                role_match = re.search(r"身份[:：]\s*(.+)", content)
                if role_match:
                    role = role_match.group(1).strip()

            players.append({
                "id": ch,
                "seat": player_idx,
                "role": role
            })
            player_idx += 1

        return JSONResponse(content={"players": players})

    @fastapi_app.post("/api/start")
    async def start_game():
        """Start the game."""
        if state.running:
            return JSONResponse(content={"success": False, "error": "Game already running"})

        if "god" not in state.channels:
            return JSONResponse(content={"success": False, "error": "god channel not found"})

        state.running = True
        state.game_ended = False
        state.router_task = asyncio.create_task(
            message_router_loop(state, initial_channel="god", initial_message="开始")
        )
        return JSONResponse(content={"success": True})

    @fastapi_app.post("/api/restart")
    async def restart_game():
        """Restart the game."""
        if state.running and state.router_task:
            state.running = False
            state.router_task.cancel()
            try:
                await state.router_task
            except asyncio.CancelledError:
                pass
            state.router_task = None

        # Generate new session ID and clear messages
        state.session_id = generate_session_id()
        state.messages.clear()
        state.game_ended = False
        state.force_restarted = True

        if "god" not in state.channels:
            return JSONResponse(content={"success": False, "error": "god channel not found"})

        # Build restart message with player info and workspace
        player_list = []
        player_idx = 1
        bot_workspace = state.storage_path / "bot" / "workspace"
        for ch in state.channels:
            if ch != "god" and player_idx <= 8:
                player_list.append(f"{player_idx}号: {ch}，GAME.md地址：{bot_workspace}/bot_api__{ch}/GAME.md")
                player_idx += 1

        restart_message = f"""重新开始游戏

游戏配置：
- 玩家数: {len(player_list)}
- 玩家列表:
{chr(10).join(f'  - {p}' for p in player_list)}
- GAME_RECORD.md 位置：{bot_workspace}/bot_api__god/GAME_RECORD.md
- 对话记录位置：{bot_workspace}/werewolf/CONVERSATION_{state.session_id}.md

请初始化游戏文件，然后等待"开始"指令。"""

        state.running = True
        state.router_task = asyncio.create_task(
            message_router_loop(state, initial_channel="god", initial_message=restart_message)
        )
        return JSONResponse(content={"success": True, "session_id": state.session_id})

    @fastapi_app.post("/api/stop")
    async def stop_game():
        """Stop the game."""
        if not state.running:
            return JSONResponse(content={"success": False, "error": "Game not running"})

        state.running = False
        if state.router_task:
            state.router_task.cancel()
            try:
                await state.router_task
            except asyncio.CancelledError:
                pass
            state.router_task = None

        return JSONResponse(content={"success": True})

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
            "user": {"path": str(default_path / "user"), "files": []}
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
        viking_path = get_viking_path(state.config)
        file_path = viking_path / "default" / path

        if not file_path.exists():
            return JSONResponse(content={"error": "File not found"}, status_code=404)

        if file_path.is_dir():
            return JSONResponse(content={"error": "Path is a directory"}, status_code=400)

        try:
            content = file_path.read_text(encoding="utf-8")
            return JSONResponse(content={
                "path": path,
                "content": content,
                "name": file_path.name
            })
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @fastapi_app.get("/api/conversations")
    async def get_conversations():
        """Get list of conversation files."""
        bot_workspace = state.storage_path / "bot" / "workspace" / "werewolf"

        if not bot_workspace.exists():
            return JSONResponse(content={"files": []})

        conversation_files = sorted(
            bot_workspace.glob("CONVERSATION_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        files = []
        for f in conversation_files:
            session_id = f.stem.replace("CONVERSATION_", "")
            files.append({
                "session_id": session_id,
                "filename": f.name,
                "modified": f.stat().st_mtime,
                "size": f.stat().st_size
            })

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
            return JSONResponse(content={
                "session_id": session_id,
                "content": content,
                "filename": file_path.name
            })
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @fastapi_app.get("/api/bot-sessions")
    async def get_bot_sessions():
        """Get list of bot session files."""
        sessions_path = state.storage_path / "bot" / "sessions"

        if not sessions_path.exists():
            return JSONResponse(content={"files": [], "current_session_id": state.session_id})

        session_files = sorted(
            sessions_path.glob("bot_api__*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
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

            files.append({
                "channel_id": channel_id,
                "session_id": file_session_id,
                "filename": f.name,
                "modified": f.stat().st_mtime,
                "size": f.stat().st_size
            })

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
                            formatted_lines.append(f"# 会话元数据\n")
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
                                formatted_lines.append(f"*Token使用: prompt={token_usage.get('prompt_tokens', 0)}, completion={token_usage.get('completion_tokens', 0)}, total={token_usage.get('total_tokens', 0)}*")

                            formatted_lines.append("")
                    except json.JSONDecodeError:
                        formatted_lines.append(line)

            content = "\n".join(formatted_lines)
            return JSONResponse(content={
                "filename": filename,
                "content": content,
                "raw_content": file_path.read_text(encoding="utf-8")
            })
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

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
            return JSONResponse(content={
                "channel_id": channel_id,
                "filename": filename,
                "content": content
            })
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    def get_leaderboard_path() -> Path:
        """Get the path to the leaderboard file."""
        leaderboard_dir = state.storage_path / "bot" / "workspace" / "werewolf"
        leaderboard_dir.mkdir(parents=True, exist_ok=True)
        return leaderboard_dir / "LEADERBOARD.json"

    def load_leaderboard() -> Dict[str, Any]:
        """Load leaderboard data from file."""
        leaderboard_path = get_leaderboard_path()
        if leaderboard_path.exists():
            try:
                return json.loads(leaderboard_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"games": [], "players": {}}

    def save_leaderboard(data: Dict[str, Any]):
        """Save leaderboard data to file."""
        leaderboard_path = get_leaderboard_path()
        leaderboard_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @fastapi_app.get("/api/leaderboard")
    async def get_leaderboard():
        """Get leaderboard data."""
        return JSONResponse(content=load_leaderboard())

    @fastapi_app.post("/api/leaderboard/save")
    async def save_game_to_leaderboard(game_data: Dict[str, Any]):
        """Save a completed game to leaderboard."""
        leaderboard = load_leaderboard()

        # Add game record
        game_record = {
            "session_id": game_data.get("session_id", state.session_id),
            "timestamp": time.time(),
            "winner": game_data.get("winner", ""),
            "players": game_data.get("players", [])
        }
        leaderboard["games"].append(game_record)

        # Update player stats
        players_data = game_data.get("players", [])
        for player in players_data:
            player_id = player.get("id", "")
            if not player_id:
                continue

            if player_id not in leaderboard["players"]:
                leaderboard["players"][player_id] = {
                    "id": player_id,
                    "games_played": 0,
                    "games_won": 0,
                    "total_score": 0,
                    "roles": {}
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

        save_leaderboard(leaderboard)
        return JSONResponse(content={"success": True, "leaderboard": leaderboard})

    def scan_directory_tree(root_path: Path, relative_path: str) -> List[Dict[str, Any]]:
        """Recursively scan a directory and build a tree structure."""
        result = []

        try:
            for item in sorted(root_path.iterdir()):
                if item.name.startswith('.') and item.is_file():
                    # Skip hidden files but include .abstract.md and .overview.md
                    if item.name not in ['.abstract.md', '.overview.md']:
                        continue

                item_rel_path = f"{relative_path}/{item.name}" if relative_path else item.name

                if item.is_dir():
                    result.append({
                        "name": item.name,
                        "path": item_rel_path,
                        "type": "directory",
                        "children": scan_directory_tree(item, item_rel_path)
                    })
                else:
                    result.append({
                        "name": item.name,
                        "path": item_rel_path,
                        "type": "file",
                        "size": item.stat().st_size
                    })
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
        bot_workspace = state.storage_path / "bot" / "workspace"

        # Special case: root path - list bot/workspace
        if path == "" or path == "/":
            return await list_directory(bot_workspace, "")

        # Special handling for werewolf/GAME_RECORD.md - map to god's GAME_RECORD.md
        if path == "werewolf/GAME_RECORD.md":
            god_record_path = bot_workspace / "bot_api__god" / "GAME_RECORD.md"
            if god_record_path.exists():
                return FileResponse(god_record_path)

        # First try: bot/workspace/{path}
        workspace_path = bot_workspace / path
        if workspace_path.exists():
            if workspace_path.is_file():
                return FileResponse(workspace_path)
            elif workspace_path.is_dir():
                return await list_directory(workspace_path, path)

        # Second try: storage_path/{path}
        file_path = state.storage_path / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)

        dir_path = state.storage_path / path
        if dir_path.exists() and dir_path.is_dir():
            return await list_directory(dir_path, path)

        return HTMLResponse(content="", media_type="text/markdown", status_code=404)

    async def list_directory(dir_path: Path, url_path: str):
        """Generate a simple HTML directory listing."""
        entries = []
        for item in sorted(dir_path.iterdir()):
            name = item.name
            if item.is_dir():
                name += "/"
            entries.append(f'<a href="{url_path.rstrip("/")}/{name}">{name}</a><br>')

        html = f"""
        <!DOCTYPE html>
        <html><body>
        <h1>/{url_path}</h1>
        {'<a href="../">../</a><br>' if url_path else ''}
        {''.join(entries)}
        </body></html>
        """
        return HTMLResponse(content=html)

    return fastapi_app


# ============================================================================
# Main Command
# ============================================================================


def main(
    port: int = typer.Option(1995, "--port", "-p", help="UI port"),
    vikingbot_url: str = typer.Option(
        "http://localhost:18790",
        "--vikingbot-url",
        help="Vikingbot API URL"
    ),
    config_path: str = typer.Option(
        "~/.openviking/ov-multi.conf",
        "--config",
        "-c",
        help="Config file path"
    ),
    game_id: str = typer.Option("default", "--game-id", help="Game ID"),
    smart_buttons: bool = typer.Option(False, "--smart-buttons", "-s", help="Enable smart button visibility control"),
    game_mode: str = typer.Option("all_agents", "--game-mode", "-m", help="Game mode: all_agents or human_player"),
):
    """Start the werewolf game server."""
    import uvicorn

    config_path_resolved = Path(config_path).expanduser()

    logger.info(f"Loading config from {config_path_resolved}")
    config = load_config(config_path_resolved)
    channels = get_bot_channels(config)
    logger.info(f"Loaded channels: {channels}")

    storage_path = get_storage_path(config)
    logger.info(f"Storage path: {storage_path}")

    has_human_player = game_mode == "human_player"
    human_channel = "human"
    if has_human_player:
        # Remove last bot player and add human
        if len(channels) > 1 and "god" in channels:
            # Keep god and remove the last player
            non_god_channels = [ch for ch in channels if ch != "god"]
            if len(non_god_channels) > 0:
                channels = ["god"] + non_god_channels[:-1]
            channels.append(human_channel)
        logger.info(f"Human player mode enabled. Channels: {channels}")

        # Create human player GAME.md
        human_workspace = storage_path / "bot" / "workspace" / "human"
        human_workspace.mkdir(parents=True, exist_ok=True)
        human_game_md = human_workspace / "GAME.md"
        if not human_game_md.exists():
            human_game_md.write_text("# 真实玩家游戏文件\n\n请编辑此文件来设置你的角色和状态。\n", encoding="utf-8")
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
    )

    # Store smart_buttons in config for easy access
    state.config["smart_buttons"] = smart_buttons
    state.has_human_player = has_human_player
    state.human_player_channel = human_channel

    # Load messages if available
    if previous_messages:
        state.messages.extend(previous_messages)
        logger.info(f"Loaded {len(previous_messages)} messages from previous session")

    fastapi_app = create_fastapi_app(state)

    logger.info(f"Starting Werewolf Server on port {port}")
    logger.info(f"UI will be available at http://localhost:{port}")
    logger.info(f"Storage path: {storage_path}")
    logger.info(f"Bot workspace: {storage_path / 'bot' / 'workspace'}")
    logger.info(f"Checking for GAME_RECORD.md: {storage_path / 'bot' / 'workspace' / 'bot_api__god' / 'GAME_RECORD.md'}")

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    typer.run(main)
