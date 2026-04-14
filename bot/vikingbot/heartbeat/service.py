"""Heartbeat service - periodic agent wake-up to check for tasks."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from vikingbot.config.schema import SessionKey


from vikingbot.session.manager import SessionManager

# Default interval: 30 minutes
DEFAULT_HEARTBEAT_INTERVAL_S = 30 * 60

# The prompt sent to agent during heartbeat
HEARTBEAT_PROMPT = """Read HEARTBEAT.md in your workspace (if it exists).
Follow any instructions or tasks listed there.
IMPORTANT: Use the 'message' tool to send any results or updates to the user.
If nothing needs attention, reply with just: HEARTBEAT_OK"""

# Token that indicates "nothing to do"
HEARTBEAT_OK_TOKEN = "HEARTBEAT_OK"
HEARTBEAT_METADATA_KEY = "heartbeat"
STALE_SESSION_THRESHOLD = timedelta(days=2)


def _is_heartbeat_empty(content: str | None) -> bool:
    """Check if HEARTBEAT.md has no actionable content."""
    if not content:
        return True

    # Lines to skip: empty, headers, HTML comments, empty checkboxes
    skip_patterns = {"- [ ]", "* [ ]", "- [x]", "* [x]"}

    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("<!--") or line in skip_patterns:
            continue
        return False  # Found actionable content

    return True


def _read_heartbeat_file(workspace: Path) -> str | None:
    """Read HEARTBEAT.md content from a specific workspace."""
    heartbeat_file = workspace / "HEARTBEAT.md"
    if heartbeat_file.exists():
        try:
            return heartbeat_file.read_text()
        except Exception:
            return None
    return None


def normalize_heartbeat_response(content: str | None) -> str:
    """Normalize a heartbeat response for no-op matching."""
    if not content:
        return ""
    return content.strip().upper().replace("_", "").replace(" ", "")


def is_heartbeat_noop_response(content: str | None) -> bool:
    """Return True when the agent response means heartbeat has nothing to do."""
    response_clean = normalize_heartbeat_response(content)
    heartbeat_ok_clean = HEARTBEAT_OK_TOKEN.replace("_", "")
    return response_clean == heartbeat_ok_clean


def _parse_session_timestamp(value: str | None) -> datetime | None:
    """Parse persisted session timestamps."""
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    The agent reads HEARTBEAT.md from each session workspace and executes any
    tasks listed there. If nothing needs attention, it replies HEARTBEAT_OK.
    """

    def __init__(
        self,
        workspace: Path,
        on_heartbeat: Callable[[str, str | None, dict[str, Any] | None], Coroutine[Any, Any, str]] | None = None,
        interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S,
        enabled: bool = True,
        sandbox_mode: str = "shared",
        session_manager: "SessionManager | None" = None,
    ):
        self.workspace = workspace
        self.on_heartbeat = on_heartbeat
        self.interval_s = interval_s
        self.enabled = enabled
        self.sandbox_mode = sandbox_mode
        self.session_manager = session_manager
        self._running = False
        self._task: asyncio.Task | None = None

    def _is_session_stale(self, session_info: dict[str, Any]) -> bool:
        """Check whether a session has been inactive for too long."""
        reference = _parse_session_timestamp(session_info.get("updated_at")) or _parse_session_timestamp(
            session_info.get("created_at")
        )
        if reference is None:
            return False

        now = datetime.now(reference.tzinfo) if reference.tzinfo else datetime.now()
        return now - reference > STALE_SESSION_THRESHOLD

    def _get_all_workspaces(self) -> dict[Path, list[SessionKey]] | None:
        workspaces: dict[Path, list[SessionKey]] = {}
        for session_info in self.session_manager.list_sessions():
            session_key: SessionKey = session_info.get("key")

            metadata = session_info.get("metadata", {})
            if metadata.get("skip_heartbeat"):
                logger.debug(
                    f"Heartbeat: skipping session {session_key} (marked as skip_heartbeat)"
                )
                continue

            if self._is_session_stale(session_info):
                logger.debug(f"Heartbeat: skipping session {session_key} (inactive > 2 days)")
                continue

            if self.sandbox_mode == "shared":
                sandbox_workspace = self.workspace / "shared"
            else:
                sandbox_workspace = self.workspace / session_key.safe_name()
            workspaces.setdefault(sandbox_workspace, []).append(session_key)
        return workspaces

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Heartbeat started (every {self.interval_s}s)")

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Heartbeat error: {e}")

    async def _tick(self) -> None:
        """Execute a single heartbeat tick for all workspaces."""
        workspaces: dict[Path, list[SessionKey]] = self._get_all_workspaces()

        if not workspaces:
            logger.debug("Heartbeat: no workspaces found")
            return

        active_workspaces = 0

        for workspace_path, session_key_list in workspaces.items():
            logger.debug(f"Heartbeat: checking workspace {workspace_path}...")

            content = _read_heartbeat_file(workspace_path)

            # Skip if HEARTBEAT.md is empty or doesn't exist
            if _is_heartbeat_empty(content):
                continue

            active_workspaces += 1
            logger.info(f"Heartbeat: processing session {workspace_path}")
            logger.info(f"Heartbeat: checking tasks for {workspace_path}...")

            if self.on_heartbeat:
                try:
                    logger.debug(
                        f"Heartbeat: calling on_heartbeat for {workspace_path} with prompt: {HEARTBEAT_PROMPT[:100]}..."
                    )
                    for session_key in session_key_list:
                        response = await self.on_heartbeat(
                            HEARTBEAT_PROMPT,
                            session_key,
                            {HEARTBEAT_METADATA_KEY: True},
                        )
                        response_preview = (response or "")[:200]
                        logger.debug(
                            f"Heartbeat: received response from agent: {response_preview}..."
                        )

                        if is_heartbeat_noop_response(response):
                            logger.info(f"Heartbeat: {workspace_path} OK (no action needed)")
                        else:
                            logger.info(f"Heartbeat: {workspace_path} completed task")

                except Exception as e:
                    logger.exception(f"Heartbeat execution failed for {workspace_path}: {e}")

        if active_workspaces == 0:
            logger.debug("Heartbeat: no tasks in any workspace")

    async def trigger_now(self, session_key: SessionKey | None = None) -> str | None:
        """Manually trigger a heartbeat."""
        if self.on_heartbeat:
            return await self.on_heartbeat(
                HEARTBEAT_PROMPT,
                session_key,
                {HEARTBEAT_METADATA_KEY: True},
            )
        return None
