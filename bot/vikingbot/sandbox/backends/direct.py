"""Direct backend implementation - executes commands directly on host without sandboxing."""

import asyncio
import os
import re
import shlex
import signal
from pathlib import Path
from typing import Any

from loguru import logger

from vikingbot.config.schema import SandboxConfig, SessionKey
from vikingbot.sandbox.backends import register_backend
from vikingbot.sandbox.base import SandboxBackend

# On Windows the shell path uses COMSPEC (cmd.exe), which cannot parse the POSIX
# commands Compile runs: `mkdir -m 700 --`, `chmod 600`, and the
# `unset ...; export OPENVIKING_CLI_CONFIG_FILE=...;` prefix. The prefix is moved
# into a per-invocation child environment for every command; only the fixed
# mkdir/chmod forms are executed natively. Every other command still runs through
# the shell with that child environment.
_ENV_PREFIX = re.compile(
    r"^unset\s+([^;]*);\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*?);\s*(.+)$",
    re.DOTALL,
)
_CHMOD_MODE = "600"
_MKDIR_RESTRICTED_MODE = "700"


def _decode_posix_value(raw: str) -> str:
    """Decode a single POSIX-quoted shell value emitted by ``shlex.quote``."""
    try:
        parts = shlex.split(raw, posix=True)
    except ValueError:
        return raw.strip()
    return parts[0] if len(parts) == 1 else raw.strip()


def _prepare_windows_shell(command: str) -> tuple[str, dict[str, str] | None]:
    """Move the Compile env prefix into a child env; return (command, child_env)."""
    match = _ENV_PREFIX.match(command)
    if match is None:
        return command, None
    unset_names, name, raw_value, remaining = match.groups()
    child_env = dict(os.environ)
    for key in unset_names.split():
        child_env.pop(key, None)
    child_env[name] = _decode_posix_value(raw_value)
    return remaining.strip(), child_env


def _has_unquoted_shell_control(command: str) -> bool:
    """Return True when a shell control character appears outside quotes."""
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if not quote:
            if char in "'\"":
                quote = char
            elif char in ";&|<>`":
                return True
            elif char == "$" and index + 1 < len(command) and command[index + 1] == "(":
                return True
        elif quote == "'":
            if char == "'":
                quote = ""
        elif char == "\\":
            index += 2
            continue
        elif char == '"':
            quote = ""
        index += 1
    return False


def _split_posix_command(command: str) -> list[str] | None:
    """Split a POSIX-quoted command while rejecting unquoted shell operators."""
    if not command or _has_unquoted_shell_control(command):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    return tokens or None


def _resolve_windows_path(target: str, base: str) -> str:
    """Resolve a sandbox path against the command working directory."""
    return target if os.path.isabs(target) else os.path.join(base, target)


async def _restrict_windows_acl(path: str, *, directory: bool) -> str | None:
    """Approximate POSIX 0700/0600 with an owner/SYSTEM/Administrators NTFS ACL.

    Inherited entries are removed first (for example sandbox helper groups), then
    explicit grants are applied. Returns error text on failure, None on success.
    """
    user = os.environ.get("USERNAME") or ""
    domain = os.environ.get("USERDOMAIN") or ""
    principal = f"{domain}\\{user}" if domain and user else user
    if not principal:
        return "STDERR:\nUSERNAME is not set\nExit code: 1"
    suffix = ":(OI)(CI)F" if directory else ":F"
    grants = [principal + suffix, "*S-1-5-18" + suffix, "*S-1-5-32-544" + suffix]
    arguments = ["icacls", path, "/inheritance:r"]
    for grant in grants:
        arguments += ["/grant:r", grant]
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")
        return f"STDERR:\n{detail}Exit code: {process.returncode}"
    return None


def _mkdir_operands(tokens: list[str]) -> tuple[list[str], bool] | None:
    """Validate ``mkdir`` flags and return (paths, restricted); None if unsupported."""
    paths: list[str] = []
    restricted = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            paths.extend(tokens[index + 1 :])
            break
        if token == "-p":
            index += 1
            continue
        if token == "-m":
            if index + 1 >= len(tokens) or tokens[index + 1] != _MKDIR_RESTRICTED_MODE:
                return None
            restricted = True
            index += 2
            continue
        if token.startswith("-"):
            return None
        paths.append(token)
        index += 1
    if not paths:
        return None
    return paths, restricted


async def _translate_windows_posix(command: str, cwd: str) -> str | None:
    """Run the fixed POSIX forms Compile needs; return None to fall back to the shell."""
    tokens = _split_posix_command(command)
    if tokens is None:
        return None
    try:
        if tokens[0] == "chmod":
            # Only the exact `chmod 600 <file>` form is implemented.
            if len(tokens) != 3 or tokens[1] != _CHMOD_MODE:
                return None
            target = _resolve_windows_path(tokens[2], cwd)
            if not os.path.isfile(target):
                return (
                    f"STDERR:\nchmod: cannot access '{target}': "
                    "No such file or directory\nExit code: 1"
                )
            error = await _restrict_windows_acl(target, directory=False)
            return error if error else "(no output)"
        if tokens[0] == "mkdir":
            operands = _mkdir_operands(tokens)
            if operands is None:
                return None
            paths, restricted = operands
            for path in paths:
                absolute = _resolve_windows_path(path, cwd)
                os.makedirs(absolute, exist_ok=True)
                if restricted:
                    error = await _restrict_windows_acl(absolute, directory=True)
                    if error:
                        return error
            return "(no output)"
    except OSError as exc:
        return f"STDERR:\n{exc}\nExit code: 1"
    return None


@register_backend("direct")
class DirectBackend(SandboxBackend):
    """Direct backend that executes commands directly on the host."""

    def __init__(self, config: "SandboxConfig", session_key: SessionKey, workspace: Path):
        super().__init__()
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._running = False
        self.restrict_workspaces = config.restrict_workspaces

    async def start(self) -> None:
        """Start the backend (no-op for direct backend)."""
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._running = True
        # logger.info("Direct backend started")

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Execute a noninteractive host command with stdin closed and a timeout."""
        if not self._running:
            raise RuntimeError("Direct backend not started")

        logger.info("[Direct] Executing: {}", repr(command))

        cwd = kwargs.get("working_dir", str(self._workspace))

        child_env = None
        if os.name == "nt":
            command, child_env = _prepare_windows_shell(command)
            translated = await _translate_windows_posix(command, cwd)
            if translated is not None:
                logger.info("[Direct] Windows POSIX command translated: {}", repr(command))
                return translated

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=child_env,
                start_new_session=os.name == "posix",
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                # Stop the command and its children before task workspace cleanup.
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return f"Error: Command timed out after {timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            log_result = result[:2000] + ("... (truncated)" if len(result) > 2000 else "")
            logger.info(f"[Direct] Output:\n{log_result}")

            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            return result

        except Exception as e:
            logger.error(f"[Direct] Error: {e}")
            import traceback

            logger.error(f"[Direct] Traceback:\n{traceback.format_exc()}")
            raise

    async def stop(self) -> None:
        """Stop the backend (no-op for direct backend)."""
        self._running = False
        logger.info("Direct backend stopped")

    def is_running(self) -> bool:
        """Check if backend is running."""
        return self._running

    @property
    def workspace(self) -> Path:
        """Get the workspace directory."""
        return self._workspace

    @property
    def sandbox_cwd(self) -> str:
        """Get the current working directory (uses actual host cwd)."""
        return str(self._workspace)

    def local_file_path(self, path: str) -> Path | None:
        sandbox_path = Path(path)
        if not sandbox_path.is_absolute():
            sandbox_path = self._workspace / path
        self._check_path_restriction(sandbox_path)
        if not sandbox_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not sandbox_path.is_file():
            raise IOError(f"Not a file: {path}")
        return sandbox_path

    async def read_file_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        self._validate_max_bytes(max_bytes)
        sandbox_path = self.local_file_path(path)
        if sandbox_path is None:
            raise IOError("Sandbox file is not accessible from the host")
        return await asyncio.to_thread(self._read_local_bytes, sandbox_path, path, max_bytes)

    async def read_file(self, path: str) -> str:
        data = await self.read_file_bytes(path)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

    async def write_file(self, path: str, content: str) -> None:
        sandbox_path = Path(path)
        if not sandbox_path.is_absolute():
            sandbox_path = self._workspace / path

        self._check_path_restriction(sandbox_path)
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_path.write_text(content, encoding="utf-8")

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        sandbox_path = Path(path)
        if not sandbox_path.is_absolute():
            sandbox_path = self._workspace / path

        self._check_path_restriction(sandbox_path)
        if not sandbox_path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")
        if not sandbox_path.is_dir():
            raise IOError(f"Not a directory: {path}")

        items = []
        for item in sorted(sandbox_path.iterdir()):
            items.append((item.name, item.is_dir()))
        return items

    def _check_path_restriction(self, path: Path) -> None:
        """Check if path is within workspace (if restricted).

        Args:
            path: Path to check

        Raises:
            PermissionError: If path outside workspace and restriction is enabled
        """

        workspace = self.workspace.resolve()
        resolved = path.resolve()

        allowed_roots = [workspace]
        if self.restrict_workspaces and self.session_key in self.restrict_workspaces:
            allowed_roots.append(Path(self.restrict_workspaces[self.session_key]).resolve())

        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise PermissionError(f"Path outside allowed workspaces: {path}")
