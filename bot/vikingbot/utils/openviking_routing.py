"""Helpers for routing vikingbot eval traffic to the intended OpenViking space."""

from __future__ import annotations

from vikingbot.config.schema import SessionKey
from vikingbot.sandbox.manager import SandboxManager


def resolve_openviking_workspace_id(
    session_key: SessionKey | None,
    sandbox_manager: SandboxManager | None,
    eval_mode: bool = False,
) -> str | None:
    """Resolve the OpenViking agent/workspace ID used for retrieval tools.

    In LongMemEval eval mode, the CLI session chat_id is repurposed to carry the
    per-sample OpenViking agent_id. This lets evaluation traffic hit the same
    agent/user namespace used during import instead of the shared CLI workspace.
    """

    if (
        eval_mode
        and session_key is not None
        and session_key.type == "cli"
        and session_key.chat_id
        and session_key.chat_id.startswith("lm_")
    ):
        return session_key.chat_id

    if sandbox_manager is None or session_key is None:
        return None
    return sandbox_manager.to_workspace_id(session_key)
