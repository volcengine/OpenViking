# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared agent-facing tool handlers."""

import asyncio
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from openviking.message.part import TextPart, part_from_dict
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import get_task_tracker


class RememberMessage(BaseModel):
    """Message accepted by the agent-facing remember API."""

    role: Literal["user", "assistant"]
    content: Optional[str] = None
    parts: Optional[List[Dict[str, Any]]] = None
    role_id: Optional[str] = None
    created_at: Optional[str] = None

    @model_validator(mode="after")
    def validate_content_or_parts(self) -> "RememberMessage":
        if self.content is None and self.parts is None:
            raise ValueError("Either 'content' or 'parts' must be provided")
        return self


class RememberRequest(BaseModel):
    """Request body for remember."""

    text: Optional[str] = None
    messages: Optional[List[RememberMessage]] = None
    session_id: Optional[str] = None
    role: Literal["user", "assistant"] = "user"
    wait: bool = False
    cleanup_session: bool = False
    keep_recent_count: int = Field(default=0, ge=0, le=10_000)
    timeout_ms: int = Field(default=30_000, ge=1_000, le=600_000)

    @model_validator(mode="after")
    def validate_payload(self) -> "RememberRequest":
        has_text = self.text is not None and self.text.strip() != ""
        has_messages = self.messages is not None and len(self.messages) > 0
        if has_text == has_messages:
            raise ValueError("Provide exactly one of 'text' or 'messages'")
        if self.cleanup_session and not self.wait:
            raise ValueError("cleanup_session=true requires wait=true")
        return self


def _default_role_id(role: str, ctx: RequestContext) -> Optional[str]:
    if role == "user":
        return ctx.user.user_id
    if role == "assistant":
        return ctx.user.agent_id
    return None


async def remember(
    service: Any,
    ctx: RequestContext,
    request: RememberRequest,
) -> Dict[str, Any]:
    """Write explicit memory source content and commit it through sessions."""

    session_id = (request.session_id or "").strip()
    used_temp_session = False
    if not session_id:
        session_id = f"agent-remember-{uuid.uuid4().hex[:12]}"
        used_temp_session = True

    messages = request.messages
    if messages is None:
        messages = [RememberMessage(role=request.role, content=request.text)]

    session = await service.sessions.get(session_id, ctx, auto_create=True)
    for msg in messages:
        if msg.parts is not None:
            parts = [part_from_dict(part) for part in msg.parts]
        else:
            parts = [TextPart(text=msg.content or "")]
        session.add_message(
            msg.role,
            parts,
            role_id=msg.role_id or _default_role_id(msg.role, ctx),
            created_at=msg.created_at,
        )

    commit_result = await service.sessions.commit_async(
        session_id,
        ctx,
        keep_recent_count=request.keep_recent_count,
    )
    result: Dict[str, Any] = {
        **commit_result,
        "session_id": session_id,
        "message_count": len(messages),
        "used_temp_session": used_temp_session,
    }

    task_id = commit_result.get("task_id")
    if request.wait and task_id:
        deadline = asyncio.get_running_loop().time() + request.timeout_ms / 1000
        tracker = get_task_tracker()
        while asyncio.get_running_loop().time() < deadline:
            task = tracker.get(
                str(task_id),
                owner_account_id=ctx.account_id,
                owner_user_id=ctx.user.user_id,
            )
            if task is not None and task.status in {"completed", "failed"}:
                task_dict = task.to_dict()
                result["status"] = task.status
                if task.status == "completed":
                    task_result = task_dict.get("result") or {}
                    if isinstance(task_result, dict):
                        result.update(task_result)
                else:
                    result["error"] = task_dict.get("error")
                break
            await asyncio.sleep(0.5)
        else:
            result["status"] = "timeout"

    if request.cleanup_session:
        try:
            await service.sessions.delete(session_id, ctx)
            result["cleaned_up"] = True
        except Exception as exc:
            result["cleaned_up"] = False
            result["cleanup_error"] = str(exc)

    return result
