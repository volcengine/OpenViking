"""Task-owned shell credentials shared by Compile agents and Skill scripts."""

from __future__ import annotations

import json
import posixpath
import shlex
import uuid
from pathlib import Path
from typing import Any, Mapping

from vikingbot.config.schema import Config
from vikingbot.sandbox.manager import SandboxManager

# SDK environment overrides must not replace the authenticated task identity.
_IDENTITY_ENV = (
    "OPENVIKING_URL OPENVIKING_API_KEY OPENVIKING_ACCOUNT OPENVIKING_USER "
    "OPENVIKING_ACTOR_PEER_ID OPENVIKING_AUTH_MODE OPENVIKING_USERNAME "
    "OPENVIKING_PASSWORD OPENVIKING_OIDC_TOKEN"
)


class CompileSandboxManager(SandboxManager):
    """Create task sandboxes whose shell processes use one authenticated identity.

    Each manager belongs to one Compile task. Credentials are written through the
    backend file API, never into shell commands, and are removed before teardown.
    The server URL comes from the same trusted configuration as VikingClient.
    """

    def __init__(
        self,
        config: Config,
        sandbox_parent_path: Path,
        source_workspace_path: Path,
        *,
        connection: Mapping[str, Any],
    ):
        """Snapshot verified connection fields; reject missing non-dev tenant/user identity."""
        super().__init__(config, sandbox_parent_path, source_workspace_path)
        dev = config.ov_server.effective_auth_mode == "dev"
        if not dev and (not connection.get("account_id") or not connection.get("user_id")):
            raise ValueError("Compile shell requires the authenticated account and user")
        self._cli_config = {
            "url": config.ov_server.server_url,
            "account": "default" if dev else connection["account_id"],
            "user": "default" if dev else connection["user_id"],
            "api_key": None if dev else connection.get("api_key"),
            "actor_peer_id": None if dev else connection.get("actor_peer_id"),
        }

    async def _create_sandbox(self, workspace_id, workspace):
        """Start a backend and bind its CLI identity before exposing it to any agent."""
        backend = await super()._create_sandbox(workspace_id, workspace)
        sandbox = _CompileSandbox(backend)
        try:
            await sandbox.initialize(self._cli_config)
        except BaseException:
            await sandbox.stop()
            raise
        return sandbox

    async def clear_credentials(self) -> None:
        """Remove credentials even when recovery artifacts must retain their sandbox."""
        for sandbox in self._sandboxes.values():
            await sandbox.clear_credentials()


class _CompileSandbox:
    """Delegate file operations and bind every shell invocation to a private config.

    The absolute config path survives child working-directory changes. Shell
    exports affect only that invocation and its descendants, never host os.environ.
    """

    def __init__(self, backend):
        self._backend = backend
        self._directory = f".compile-auth-{uuid.uuid4().hex}"
        self._config_path = posixpath.join(backend.sandbox_cwd, self._directory, "ovcli.conf")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    async def initialize(self, config: Mapping[str, Any]) -> None:
        """Write credentials inside a private directory; fail before any task command runs."""
        directory = shlex.quote(posixpath.dirname(self._config_path))
        output = await self._backend.execute(f"mkdir -m 700 -- {directory}")
        self._backend._ensure_command_succeeded(output, "Compile credentials directory")
        await self._backend.write_file(f"{self._directory}/ovcli.conf", json.dumps(config))
        output = await self._backend.execute(f"chmod 600 {shlex.quote(self._config_path)}")
        self._backend._ensure_command_succeeded(output, "Compile credentials permissions")

    async def execute(self, command: str, timeout: int = 60, **kwargs: Any) -> str:
        """Run a command with the task config, preserving backend timeout and output semantics."""
        prefix = (
            f"unset {_IDENTITY_ENV}; "
            f"export OPENVIKING_CLI_CONFIG_FILE={shlex.quote(self._config_path)}; "
        )
        return await self._backend.execute(prefix + command, timeout=timeout, **kwargs)

    async def clear_credentials(self) -> None:
        """Remove only this sandbox's credential directory; repeated removal is harmless."""
        await self._backend.remove_tree(self._directory)

    async def stop(self) -> None:
        """Remove credentials before stopping the backend, including on task failure."""
        try:
            await self.clear_credentials()
        finally:
            await self._backend.stop()
