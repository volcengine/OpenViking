"""Single-instance background host for the LoopX generic CLI contract."""

from __future__ import annotations

import asyncio
import fcntl
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from vikingbot.agent.tools.base import ToolContext
from vikingbot.bus.events import OutboundEventType, OutboundMessage
from vikingbot.config.schema import SandboxMode, SessionKey
from vikingbot.longtask.host_store import HostStore
from vikingbot.longtask.loopx_client import LoopXClient, LoopXError, validate_installation


@dataclass
class Turn:
    service: LongTaskService
    task_id: str
    turn_id: str
    tool_count: int = 0
    proposal: dict[str, Any] | None = None
    successful_tools: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def before_model(self, iteration: int) -> str:
        self.service.store.assert_authorized(self.task_id)
        return (
            f"Round budget: model call {iteration}/{self.service.config.longtask.model_calls_per_round}; "
            f"tools {self.tool_count}/{self.service.config.longtask.tools_per_round}. "
            "Reserve your last model call for submit_long_task_turn."
        )


class LongTaskService:
    def __init__(self, agent_loop: Any):
        self.agent = agent_loop
        self.config = agent_loop.config
        self.root = self.config.bot_data_path / "longtasks"
        self.store: HostStore | None = None
        self.workflows: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._instance_lock = None
        self._wake = asyncio.Event()

    async def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._instance_lock = (self.root / "host.lock").open("a+")
        try:
            fcntl.flock(self._instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._instance_lock.close()
            self._instance_lock = None
            raise RuntimeError("Another long-task host is already using this Bot data directory")
        self.workflows = await validate_installation(
            self.root,
            timeout=self.config.longtask.cli_timeout_seconds,
        )
        self.store = HostStore(self.root / "host.db")
        self.store.recover()
        from vikingbot.longtask.tools import LongTaskTool, StartLongTaskTool

        self.agent.tools.register(StartLongTaskTool(self))
        self.agent.tools.register(LongTaskTool(self))

    def _lock(self, task_id: str) -> asyncio.Lock:
        return self._locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def _origin(context: ToolContext) -> tuple[str, str]:
        if not context.sender_id or context.session_key is None:
            raise ValueError("Long tasks require an authenticated user and conversation")
        return context.sender_id, context.session_key.model_dump_json()

    def _task_config(self):
        # Root VLM configuration carries shared runtime clients and thread locks.
        # Copy only the configuration branches overridden by the task; runtime
        # resources keep their identity and the main agent's settings stay intact.
        return self.config.model_copy(
            update={
                "sandbox": self.config.sandbox.model_copy(
                    deep=True, update={"mode": SandboxMode.PER_SESSION}
                ),
                "agents": self.config.agents.model_copy(
                    deep=True, update={"subagent_enabled": False}
                ),
            }
        )

    def _sandbox(self, task_id: str):
        from vikingbot.sandbox.manager import SandboxManager

        return SandboxManager(
            self._task_config(), self.root / "workspaces", self.config.workspace_path
        )

    @staticmethod
    def _session_key(task_id: str) -> SessionKey:
        return SessionKey(type="longtask", channel_id=task_id, chat_id=task_id)

    def _client(self, task_id: str) -> LoopXClient:
        workspace = self._sandbox(task_id).get_workspace_path(self._session_key(task_id))
        return LoopXClient(
            workspace, self.root / "runtime", timeout=self.config.longtask.cli_timeout_seconds
        )

    async def create(self, context: ToolContext, objective: str, request_id: str) -> dict[str, Any]:
        owner, origin = self._origin(context)
        if not objective.strip() or not 1 <= len(objective) <= 4000:
            raise ValueError("objective must contain 1–4000 characters")
        if not request_id.strip() or not 1 <= len(request_id) <= 128:
            raise ValueError("request_id must contain 1–128 characters")
        row, created = self.store.create(
            owner, origin, request_id, objective, context.channel_metadata or {}
        )
        if created:
            self._wake.set()
        return {
            "task_id": row["task_id"],
            "accepted": True,
            "new": created,
            "note": "Queued for LoopX initialization; use long_task to inspect progress.",
        }

    async def control(
        self, context: ToolContext, task_id: str, action: str, message: str = ""
    ) -> dict[str, Any]:
        owner, origin = self._origin(context)
        row = self.store.get(task_id)
        if (row["owner"], row["origin"]) != (owner, origin):
            raise ValueError("Unknown long task in this conversation")
        if action == "status":
            return {
                "task_id": task_id,
                "authorized": bool(row["authorized"]),
                "cancelled": bool(row["cancelled"]),
                "reason": row["reason"],
                "rounds": row["rounds"],
                "result": row["last_result"],
                "loopx_decision": json.loads(row["last_decision"])
                if row["last_decision"]
                else None,
                "unresolved_operations": self.store.unresolved(task_id),
            }
        if action not in {"pause", "cancel", "resume"}:
            raise ValueError("Unknown long-task action")
        if row["cancelled"] or row["terminal"]:
            raise ValueError("This task is cancelled or finished; start a new task")
        if action != "resume":
            # Revoke BEFORE waiting for the running turn or issuing a remote stop.
            self.store.update(
                task_id, authorized=0, cancelled=int(action == "cancel"), reason=action
            )
        async with self._lock(task_id):
            row = self.store.get(task_id)
            if action == "resume":
                if row["cancelled"] or row["terminal"]:
                    raise ValueError("This task is cancelled or finished; start a new task")
                if not row["initialized"] and row["inflight"] is not None:
                    raise ValueError(
                        "Creation was interrupted; inspect LoopX before creating a new task"
                    )
                if self.store.unresolved(task_id):
                    raise ValueError(
                        "Interrupted execution needs operation-journal and external-state reconciliation"
                    )
                if row["rounds"] >= self.config.longtask.max_rounds:
                    raise ValueError(
                        "Task round budget exhausted; start a new explicitly scoped task"
                    )
                # Resume the original admitted LoopX turn until it has a legal
                # closeout. Ending a model slice does not settle that turn.
            if not row["initialized"] and row["inflight"] is None:
                if action == "resume":
                    self.store.update(
                        task_id,
                        authorized=1,
                        reason="creating",
                        next_wake=0,
                        latest_input=message or row["latest_input"],
                    )
                    self._wake.set()
                return {"task_id": task_id, "action": action, "confirmed": True}
            if action == "resume":
                await self._resume_waiting_todo(task_id, owner, origin)
            result = await self._client(task_id).call(
                "goal-lifecycle",
                "--goal-id",
                task_id,
                "--operation",
                "resume" if action == "resume" else "stop",
                "--reason",
                f"Bot owner requested {action}",
                "--execute",
            )
            if result.get("ok") is not True:
                raise LoopXError("LoopX lifecycle write was not confirmed")
            if action == "resume":
                self.store.update(
                    task_id,
                    authorized=1,
                    reason="resumed",
                    next_wake=0,
                    no_progress=0,
                    latest_input=message or row["latest_input"],
                )
                self._wake.set()
        return {"task_id": task_id, "action": action, "confirmed": True}

    async def _resume_waiting_todo(self, task_id: str, owner: str, origin: str) -> None:
        todo_id = self.store.latest_waiting_todo(task_id)
        if todo_id is None:
            return
        client = self._client(task_id)
        readback = await client.call("todo", "list", "--goal-id", task_id, "--todo-id", todo_id)
        todo = readback.get("todo", {})
        if todo.get("todo_id") != todo_id:
            raise LoopXError("Waiting Todo readback identity mismatch")
        # An earlier successful owner request may already have replaced it.
        if todo.get("status") == "done" and todo.get("superseded_by"):
            return
        if todo.get("status") != "blocked":
            raise LoopXError("Waiting Todo is not blocked; inspect its lifecycle before resuming")
        args = (
            "todo",
            "supersede",
            "--goal-id",
            task_id,
            "--agent-id",
            task_id,
            "--todo-id",
            todo_id,
            "--reason",
            "Owner requested continuation after a blocked round",
            "--next-agent-todo",
            "Resume unfinished work within the original objective using the latest owner input. "
            "Inspect existing artifacts before further changes; verify all acceptance criteria.",
            "--next-action-kind",
            "deliver_artifact",
            "--next-claimed-by",
            task_id,
        )
        operation = self.store.begin_control_operation(task_id, owner, origin, list(args))
        result = await client.call(*args)
        if result.get("todo_id") != todo_id or not result.get("superseded_by"):
            raise LoopXError("Waiting Todo successor was not confirmed")
        self.store.end_operation(operation, result)

    async def run(self) -> None:
        while True:
            self._wake.clear()
            for row in self.store.due():
                task_id = row["task_id"]
                async with self._lock(task_id):
                    if not self.store.get(task_id)["authorized"]:
                        continue
                    try:
                        await self._tick(task_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        current = self.store.get(task_id)
                        if not current["authorized"] and not self.store.unresolved(task_id):
                            continue
                        self.store.update(task_id, authorized=0, reason=str(exc))
                        self.store.notify(task_id, f"长任务 {task_id} 已暂停：{exc}")
            await self._deliver()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _deliver(self) -> None:
        for delivery in self.store.pending_deliveries():
            row = self.store.get(delivery["task_id"])
            metadata = json.loads(row["metadata"])
            metadata["long_task_delivery_id"] = delivery["id"]
            await self.agent.bus.publish_outbound(
                OutboundMessage(
                    session_key=SessionKey.model_validate_json(row["origin"]),
                    content=delivery["content"],
                    event_type=OutboundEventType.NOTIFICATION,
                    metadata=metadata,
                )
            )
            self.store.mark_enqueued(delivery["id"])

    async def _tick(self, task_id: str) -> None:
        row = self.store.assert_authorized(task_id)
        client = self._client(task_id)
        if self.store.unresolved(task_id):
            raise RuntimeError("Unresolved operations require reconciliation before execution")
        turn_id = row["inflight"] or uuid.uuid4().hex
        if not row["initialized"]:
            self.store.update(task_id, inflight=turn_id)
            sandbox = self._sandbox(task_id)
            try:
                await sandbox.get_sandbox(self._session_key(task_id))
                await client.bootstrap(task_id, task_id, row["objective"])
            finally:
                await sandbox.cleanup_all()
            self.store.update(task_id, inflight=None, initialized=1, reason="ready")
        decision = await client.decision(task_id, task_id, turn_id)
        self.store.update(task_id, last_decision=json.dumps(decision, ensure_ascii=False))
        if decision.get("effective_action") == "terminal_no_followup":
            self.store.update(
                task_id, authorized=0, terminal=1, reason="LoopX verified terminal_no_followup"
            )
            self.store.notify(task_id, f"长任务 {task_id} 已完成。\n{row['last_result'] or ''}")
            return
        if not decision["should_run"]:
            if decision.get("requires_user_action"):
                question = decision.get("user_todo_summary", {}).get("first_open_items", [])
                self.store.update(task_id, authorized=0, reason=decision["reason"])
                self.store.notify(
                    task_id,
                    f"长任务 {task_id} 需要你处理：\n" + json.dumps(question, ensure_ascii=False),
                )
                return
            scheduler = decision["scheduler_hint"]["cold_path_detail"]["local_scheduler"]
            interval = float(scheduler["recommended_interval_minutes"]) * 60
            if interval <= 0:
                raise LoopXError("Invalid LoopX wake interval")
            self.store.update(task_id, next_wake=time.time() + interval, reason=decision["reason"])
            return
        if decision.get("effective_action") not in {"normal_run", "autonomous_replan_required"}:
            raise LoopXError(
                f"Host action requires operator handling: {decision.get('effective_action')}"
            )
        if row["rounds"] >= self.config.longtask.max_rounds:
            raise RuntimeError("Long-task total round budget reached")
        self.store.update(task_id, rounds=row["rounds"] + 1, inflight=turn_id)
        turn = Turn(self, task_id, turn_id)
        async with asyncio.timeout(self.config.longtask.round_timeout_seconds):
            selected = decision.get("selected_todo")
            if selected and selected.get("claimed_by") != task_id:
                self.store.assert_authorized(task_id)
                await client.call(
                    "todo",
                    "claim",
                    "--goal-id",
                    task_id,
                    "--agent-id",
                    task_id,
                    "--todo-id",
                    selected["todo_id"],
                    "--claimed-by",
                    task_id,
                )
            result = await self._execute(turn, row, decision)
            if turn.proposal is None:
                no_progress = row["no_progress"] + 1
                self.store.update(
                    task_id,
                    no_progress=no_progress,
                    last_result=result["text"],
                    next_wake=time.time() + 30,
                )
                if no_progress >= self.config.longtask.max_no_progress_rounds:
                    raise RuntimeError("Consecutive no-progress round limit reached")
                return
            await self._settle(turn, row, decision, result)
        self.store.update(
            task_id, inflight=None, no_progress=0, next_wake=0, last_result=turn.proposal["summary"]
        )

    async def _execute(self, turn: Turn, row: dict, decision: dict) -> dict:
        from vikingbot.agent.loop import AgentLoop
        from vikingbot.agent.tools.factory import register_default_tools
        from vikingbot.longtask.provider import BudgetedProvider
        from vikingbot.longtask.tools import (
            JournaledToolRegistry,
            LoopXReferenceTool,
            SubmitLongTaskTurnTool,
        )

        sandbox = self._sandbox(turn.task_id)
        key = self._session_key(turn.task_id)
        workspace = sandbox.get_workspace_path(key)
        loop = AgentLoop(
            bus=self.agent.bus,
            provider=BudgetedProvider(self.agent.provider, turn),
            workspace=workspace,
            model=self.agent.model,
            temperature=self.agent.temperature,
            max_iterations=self.config.longtask.model_calls_per_round,
            memory_window=self.agent.memory_window,
            config=self._task_config(),
            sandbox_manager=sandbox,
            session_manager=self.agent.sessions,
        )
        registry = JournaledToolRegistry(self.config, turn)
        register_default_tools(
            registry,
            self.config,
            include_message_tool=False,
            include_spawn_tool=False,
            include_cron_tool=False,
            include_image_tool=False,
            include_viking_tools=self.config.ov_server.is_available(),
        )
        # Personal peer memory writes require a caller identity. Background
        # history uses the Bot-managed session sync instead, never a user peer.
        registry.unregister("openviking_memory_commit")
        registry.register(LoopXReferenceTool(self.workflows))
        registry.register(SubmitLongTaskTurnTool(turn))
        instructions = (
            "You are the bounded worker of a VikingBot long task. The host owns LoopX CLI writes, "
            "quota settlement, scheduling and user notifications. Do not run LoopX through exec, "
            "edit its state files, start other agents or expand the original objective. "
            "Use the task tools for work and submit_long_task_turn for the result. "
            "Original scope and user approvals still apply. Tool success alone is not goal acceptance. "
            "The entry and project workflows are loaded below; other workflow instructions "
            "are available by their skill IDs. An ordinary text reply does not complete "
            "the task. For a replan, inspect actual evidence and either propose bounded remaining "
            "work, report a real blocker, or verify complete coverage of the original goal.\n"
            + "Available matching-release workflows: "
            + ", ".join(self.workflows)
            + "\n"
            + self.workflows["loopx"]
            + "\n\n"
            + self.workflows["loopx-project"]
        )
        prompt = (
            f"Original objective (preserve its complete scope):\n{row['objective']}\n\n"
            f"Latest explicit owner input:\n{row['latest_input'] or '(none)'}\n\n"
            f"Current LoopX decision, authoritative for this round:\n{json.dumps(decision, ensure_ascii=False)}"
        )
        try:
            await sandbox.get_sandbox(key)
            return await loop.run_background_turn(
                session_key=key,
                prompt=prompt,
                instructions=instructions,
                tool_registry=registry,
                before_model=turn.before_model,
            )
        finally:
            try:
                await registry.close()
            finally:
                try:
                    await loop.close_mcp()
                finally:
                    await sandbox.cleanup_all()

    async def _settle(self, turn: Turn, row: dict, decision: dict, result: dict) -> None:
        proposal = turn.proposal
        self.store.assert_authorized(turn.task_id)
        client = self._client(turn.task_id)
        operation = self.store.begin_operation(turn.task_id, turn.turn_id, "settlement", proposal)

        async def checked_call(*args):
            self.store.assert_authorized(turn.task_id)
            call_id = self.store.begin_operation(
                turn.task_id, turn.turn_id, "loopx_write", list(args)
            )
            response = await client.call(*args)
            self.store.end_operation(call_id, response)
            return response

        selected = decision.get("selected_todo")
        terminal_completion_args = None
        if proposal.get("blocked_reason"):
            if not selected:
                raise LoopXError("A blocked replan without a bound Todo requires operator handling")
            # A typed lifecycle transition closes the admitted turn without
            # pretending work is complete or spending a delivery quota slot.
            blocked = await checked_call(
                "todo",
                "update",
                "--goal-id",
                turn.task_id,
                "--agent-id",
                turn.task_id,
                "--todo-id",
                selected["todo_id"],
                "--status",
                "blocked",
                "--reason",
                proposal["blocked_reason"],
                "--evidence",
                proposal["evidence"],
            )
            if blocked.get("todo_id") != selected["todo_id"] or blocked.get("status") != "blocked":
                raise LoopXError("LoopX blocked Todo transition was not confirmed")
            self.store.update(turn.task_id, authorized=0, reason=proposal["blocked_reason"])
            self.store.end_operation(
                operation, {"blocked": proposal["blocked_reason"], "todo_id": selected["todo_id"]}
            )
            self.store.notify(
                turn.task_id, f"长任务 {turn.task_id} 需要你处理：{proposal['blocked_reason']}"
            )
            return
        if selected:
            args = [
                "todo",
                "complete",
                "--goal-id",
                turn.task_id,
                "--agent-id",
                turn.task_id,
                "--todo-id",
                selected["todo_id"],
                "--evidence",
                proposal["evidence"],
                "--turn-instance-id",
                turn.turn_id,
            ]
            if proposal["goal_complete"]:
                args += ["--no-follow-up", "--reason", proposal["summary"]]
            else:
                args += [
                    "--next-agent-todo",
                    proposal["next_todo"],
                    "--next-action-kind",
                    "deliver_artifact",
                ]
            if proposal["goal_complete"]:
                # LoopX terminal closeout commits only after this turn's verified
                # writeback and quota settlement; successors use the normal order.
                terminal_completion_args = args
            else:
                await checked_call(*args)
        elif not proposal["goal_complete"]:
            obligation_id = decision.get("autonomous_replan_obligation", {}).get("obligation_id")
            if not obligation_id:
                raise LoopXError("The replan decision did not supply an obligation binding")
            await checked_call(
                "todo",
                "add",
                "--goal-id",
                turn.task_id,
                "--role",
                "agent",
                "--text",
                proposal["next_todo"],
                "--task-class",
                "advancement_task",
                "--action-kind",
                "deliver_artifact",
                "--claimed-by",
                turn.task_id,
                "--replan-obligation-id",
                obligation_id,
                "--target-key",
                turn.turn_id,
            )
        # This is a documented public CLI input packet, not a private state edit.
        packet = {
            "schema_version": "goal_vision_replan_contract_v0",
            "state": "no_followup" if proposal["goal_complete"] else "vision_patch_proposed",
            "vision_patch": {
                "acceptance_summary": proposal["evidence"],
                "last_patch_summary": proposal["summary"],
            },
            "path_delta": {
                "outcome": "stop" if proposal["goal_complete"] else "continue",
                "prior_assumption": (selected["text"] if selected else row["objective"])[:320],
                "observed_reality": proposal["summary"],
                "stopped" if proposal["goal_complete"] else "retained": [
                    "Further work: all original acceptance criteria verified"
                    if proposal["goal_complete"]
                    else "Continue within the original user objective and scope"
                ],
                "evidence_refs": [proposal["validation_operation_id"]],
            },
        }
        packet_path = self.root / "submissions" / f"{turn.turn_id}.json"
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(json.dumps(packet, ensure_ascii=False))
        args = [
            "refresh-state",
            "--goal-id",
            turn.task_id,
            "--agent-id",
            turn.task_id,
            "--classification",
            "bot_validated_progress",
            "--agent-vision-json",
            str(packet_path),
        ]
        if selected:
            args += [
                "--turn-instance-id",
                turn.turn_id,
                "--todo-id",
                selected["todo_id"],
                "--delivery-outcome",
                "primary_goal_outcome" if proposal["goal_complete"] else "outcome_progress",
            ]
        if (
            decision["effective_action"] == "autonomous_replan_required"
            and proposal["goal_complete"]
        ):
            args += [
                "--autonomous-replan-recorded",
                "--progress-result-class",
                "no_followup",
                "--progress-coverage-scope-id",
                turn.task_id,
                "--progress-coverage-complete",
                "--progress-evidence-id",
                proposal["validation_operation_id"],
            ]
        await checked_call(*args)
        if selected:
            await checked_call(
                "quota",
                "spend-slot",
                "--goal-id",
                turn.task_id,
                "--agent-id",
                turn.task_id,
                "--slots",
                "1",
                "--source",
                "heartbeat",
                "--execute",
                "--turn-instance-id",
                turn.turn_id,
                "--todo-id",
                selected["todo_id"],
            )
        if terminal_completion_args is not None:
            await checked_call(*terminal_completion_args)
        self.store.end_operation(operation, {"settled": True, "usage": result["usage"]})

    async def close(self) -> None:
        self.agent.tools.unregister("start_long_task")
        self.agent.tools.unregister("long_task")
        if self.store is not None:
            self.store.close()
            self.store = None
        if self._instance_lock is not None:
            self._instance_lock.close()
            self._instance_lock = None
