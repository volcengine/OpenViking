"""Cron tool for scheduling reminders and tasks."""

from typing import TYPE_CHECKING, Any

from vikingbot.agent.tools.base import Tool
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronSchedule

if TYPE_CHECKING:
    from vikingbot.agent.tools.base import ToolContext
    from vikingbot.config.schema import SessionKey


class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""

    DELIVERY_METADATA_KEYS = ("reply_to", "chat_type", "chat_mode", "root_id", "sender_id")

    def __init__(self, cron_service: CronService):
        self._cron = cron_service

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Schedule reminders and recurring tasks. Actions: add, list, remove."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform",
                },
                "name": {"type": "string", "description": "Job name (for add)"},
                "message": {"type": "string", "description": "Reminder message (for add)"},
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for scheduled tasks)",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                },
                "job_id": {"type": "string", "description": "Job ID (for remove)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        tool_context: "ToolContext",
        action: str,
        name: str = "",
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return self._add_job(
                name,
                message,
                every_seconds,
                cron_expr,
                at,
                tool_context.session_key,
                self._delivery_metadata(getattr(tool_context, "channel_metadata", None)),
            )
        elif action == "list":
            return self._list_jobs()
        elif action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _add_job(
        self,
        name: str,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        at: str | None,
        session_key: "SessionKey",
        channel_metadata: dict[str, Any] | None = None,
    ) -> str:
        if not message:
            return "Error: message is required for add"

        # Build schedule
        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr)
        elif at:
            from datetime import datetime

            dt = datetime.fromisoformat(at)
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        job = self._cron.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=True,
            session_key=session_key,
            channel_metadata=channel_metadata,
            delete_after_run=delete_after,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    def _delivery_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not metadata:
            return {}
        return {
            key: metadata[key]
            for key in self.DELIVERY_METADATA_KEYS
            if isinstance(metadata.get(key), str) and metadata[key]
        }

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = [f"- {j.name} (id: {j.id}, {j.schedule.kind})" for j in jobs]
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"
