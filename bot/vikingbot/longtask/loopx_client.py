"""Versioned public CLI boundary; never edits LoopX's private state."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

LOOPX_VERSION = "1.0.5"
WORKFLOW_IDS = (
    "loopx",
    "loopx-project",
    "loopx-pr-program",
    "loopx-pr-review",
    "loopx-doc-registry",
    "loopx-benchmark",
    "loopx-self-repair",
)


class LoopXError(RuntimeError):
    """The CLI failed or returned a contract the host cannot execute."""


def installed_cli() -> Path:
    """Resolve the official console script owned by this Python installation."""
    try:
        distribution = importlib.metadata.distribution("loopx")
    except importlib.metadata.PackageNotFoundError as exc:
        raise LoopXError("Install the optional dependency: openviking[bot,longtask]") from exc
    scripts = [
        Path(distribution.locate_file(item)).resolve()
        for item in distribution.files or ()
        if item.name == "loopx"
    ]
    if len(scripts) != 1 or not scripts[0].is_file():
        raise LoopXError("LoopX installation is missing its official console script; reinstall it")
    return scripts[0]


class LoopXClient:
    def __init__(self, project: Path, runtime: Path, *, timeout: int = 60):
        self.project = project.resolve()
        self.runtime = runtime.resolve()
        self.registry = self.project / ".loopx" / "registry.json"
        self.timeout = timeout

    async def call(self, *args: str) -> dict[str, Any]:
        """No shell, dependency installation, retries, or alternate backends."""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(installed_cli()),
            "--registry",
            str(self.registry),
            "--runtime-root",
            str(self.runtime),
            "--format",
            "json",
            *args,
            cwd=self.project,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except BaseException:
            # A timed-out write has an unknown outcome. The caller must reconcile,
            # never retry a mutation just because it did not receive a response.
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise
        try:
            payload = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LoopXError(f"LoopX returned invalid JSON (exit={process.returncode})") from exc
        if not isinstance(payload, dict):
            raise LoopXError("LoopX response must be a JSON object")
        if process.returncode or payload.get("ok") is False:
            # Avoid exposing stderr (potentially credentials) in a chat response.
            reason = payload.get("error_code") or payload.get("error") or payload.get("reason")
            if args[0] == "doctor":
                failed = [
                    check["id"]
                    for check in payload.get("checks", [])
                    if check.get("required") and not check.get("ok")
                ]
                reason = ", ".join(failed) or reason
            raise LoopXError(f"LoopX {args[0]} failed: {reason or process.returncode}")
        return payload

    async def bootstrap(self, goal_id: str, agent_id: str, objective: str) -> None:
        result = await self.call(
            "bootstrap",
            "--project",
            str(self.project),
            "--goal-id",
            goal_id,
            "--objective",
            objective,
            "--adapter-kind",
            "generic_project_goal_v0",
        )
        if result.get("goal_id") != goal_id or result.get("state_action") != "created":
            raise LoopXError("Goal creation was not confirmed")
        registered = await self.call(
            "register-agent",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--require-new",
            "--execute",
        )
        if not registered.get("registration_readback", {}).get("verified"):
            raise LoopXError("Agent registration readback failed")
        await self.call(
            "todo",
            "add",
            "--goal-id",
            goal_id,
            "--role",
            "agent",
            "--text",
            objective,
            "--task-class",
            "advancement_task",
            "--action-kind",
            "deliver_artifact",
            "--claimed-by",
            agent_id,
        )

    async def decision(self, goal_id: str, agent_id: str, turn_id: str) -> dict[str, Any]:
        result = await self.call(
            "quota",
            "should-run",
            "--goal-id",
            goal_id,
            "--agent-id",
            agent_id,
            "--runtime-profile",
            "generic_cli",
            "--turn-instance-id",
            turn_id,
            "--include-detail",
            "scheduler",
            "--available-capability",
            "shell",
        )
        if result.get("goal_id") != goal_id or not isinstance(result.get("should_run"), bool):
            raise LoopXError("Invalid quota decision identity or should_run field")
        identity = result.get("agent_identity", {})
        if identity.get("agent_id") != agent_id or not identity.get("registered"):
            raise LoopXError("Quota decision is not bound to the registered task agent")
        return result


def load_workflows(skills_root: Path) -> dict[str, str]:
    """Validate prepared instructions without replacing existing files."""
    instructions = {}
    for skill_id in WORKFLOW_IDS:
        directory = skills_root / skill_id
        try:
            manifest = json.loads((directory / ".loopx-skill-version.json").read_text())
            content = (directory / "SKILL.md").read_text()
        except (OSError, ValueError) as exc:
            raise LoopXError(
                f"Missing LoopX instructions. Run: loopx workflow-skills --install "
                f"--skills-dir {skills_root}"
            ) from exc
        if manifest.get("loopx_version") != LOOPX_VERSION or manifest.get("skill_id") != skill_id:
            raise LoopXError(f"LoopX instruction version mismatch: {skill_id}")
        if not content.strip():
            raise LoopXError(f"Empty LoopX instructions: {skill_id}")
        instructions[skill_id] = content
    return instructions


async def validate_installation(root: Path, *, timeout: int = 60) -> dict[str, str]:
    if sys.version_info < (3, 11):
        raise LoopXError("Long tasks require Python 3.11 or newer")
    try:
        version = importlib.metadata.version("loopx")
    except importlib.metadata.PackageNotFoundError as exc:
        raise LoopXError(
            'Install the optional dependency: pip install "openviking[bot,longtask]"'
        ) from exc
    if version != LOOPX_VERSION:
        raise LoopXError(f"Expected LoopX {LOOPX_VERSION}, found {version}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    client = LoopXClient(root, root / "runtime", timeout=timeout)
    # installation-only deliberately avoids doctor inspecting personal Codex projects.
    doctor = await client.call("doctor", "--installation-only")
    if not doctor.get("typescript_control_plane", {}).get("ready"):
        raise LoopXError("LoopX requires qualified Node.js >=22.18.0; use Node 24 LTS")
    skills_root = root / "skills"
    if not skills_root.exists() and not skills_root.is_symlink():
        # Expand bundled resources through the official CLI, only on first use.
        # Existing or partially prepared directories must pass validation as-is;
        # never overwrite user edits or silently repair a failed initialization.
        prepared = await client.call(
            "workflow-skills", "--install", "--skills-dir", str(skills_root)
        )
        if prepared.get("ok") is not True:
            raise LoopXError("LoopX workflow preparation was not confirmed")
    return load_workflows(skills_root)
