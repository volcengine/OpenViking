"""Tests for ToolContext.workspace_id derivation in __post_init__."""

from types import SimpleNamespace

from vikingbot.agent.tools.base import ToolContext


class _FakeSandboxManager:
    """Minimal stand-in exposing to_workspace_id(session_key)."""

    def __init__(self, workspace_id: str):
        self._workspace_id = workspace_id
        self.calls = []

    def to_workspace_id(self, session_key):
        self.calls.append(session_key)
        return self._workspace_id


def test_workspace_id_computed_from_sandbox_manager():
    session_key = SimpleNamespace(name="telegram:12345")
    manager = _FakeSandboxManager("ws-telegram-12345")

    ctx = ToolContext(session_key=session_key, sandbox_manager=manager)

    assert ctx.workspace_id == "ws-telegram-12345"
    assert manager.calls == [session_key]


def test_workspace_id_none_without_sandbox_manager():
    session_key = SimpleNamespace(name="telegram:12345")

    ctx = ToolContext(session_key=session_key, sandbox_manager=None)

    assert ctx.workspace_id is None


def test_explicit_workspace_id_is_preserved():
    manager = _FakeSandboxManager("computed-id")

    ctx = ToolContext(
        session_key=SimpleNamespace(name="telegram:12345"),
        sandbox_manager=manager,
        workspace_id="explicit-id",
    )

    assert ctx.workspace_id == "explicit-id"
    assert manager.calls == []


def test_workspace_id_stays_none_when_session_key_missing():
    """workspace_id remains None when sandbox_manager is present but session_key is None."""
    manager = _FakeSandboxManager("should-not-be-used")

    ctx = ToolContext(session_key=None, sandbox_manager=manager)

    assert ctx.workspace_id is None
    assert manager.calls == []
