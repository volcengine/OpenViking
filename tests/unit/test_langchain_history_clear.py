from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openviking")

from langchain_core.messages import HumanMessage
from langchain_openviking import InMemoryOpenVikingClient, OpenVikingChatMessageHistory
from openviking_sdk.errors import UnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("failure", [None, "create-unavailable", "create-denied", "delete"])
async def test_history_clear_reports_reset_failures_before_acknowledging_context(
    async_mode, failure
):
    error = (
        UnavailableError("recreation unavailable")
        if failure == "create-unavailable"
        else PermissionError("reset denied")
    )
    calls = []
    acknowledged = []

    class ResetClient(InMemoryOpenVikingClient):
        def delete_session(self, session_id):
            calls.append("delete")
            if failure == "delete":
                raise error
            return super().delete_session(session_id)

        def create_session(self, session_id=None):
            calls.append("create")
            if failure and failure.startswith("create"):
                raise error
            return super().create_session(session_id)

    class AsyncResetClient(ResetClient):
        async def delete_session(self, session_id):
            return super().delete_session(session_id)

        async def create_session(self, session_id=None):
            return super().create_session(session_id)

    client = AsyncResetClient() if async_mode else ResetClient()
    history = OpenVikingChatMessageHistory(
        "reset-session",
        **({"async_client": client} if async_mode else {"client": client}),
        context_parts_provider=lambda _: [{"type": "context", "uri": "viking://resources/a"}],
        context_parts_acknowledger=acknowledged.append,
    )
    if async_mode:
        await history.aadd_messages([HumanMessage("old turn")])
    else:
        history.add_messages([HumanMessage("old turn")])

    async def clear():
        if async_mode:
            await history.aclear()
        else:
            history.clear()

    if failure:
        with pytest.raises(type(error)) as caught:
            await clear()
        assert caught.value is error
        assert acknowledged == []
        assert ("reset-session" in client.sessions) == (failure == "delete")
    else:
        await clear()
        assert client.sessions["reset-session"] == []
        assert acknowledged == ["reset-session"]
        if async_mode:
            await history.aadd_messages([HumanMessage("new turn")])
        else:
            history.add_messages([HumanMessage("new turn")])
        assert len(client.sessions["reset-session"]) == 1
    assert calls == (["delete"] if failure == "delete" else ["delete", "create"])
    await history.aclose()
