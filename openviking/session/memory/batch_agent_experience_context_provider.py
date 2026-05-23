# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Batch provider for phase-2 agent experience consolidation."""

from typing import Any, Dict, List

from openviking.session.memory.agent_experience_context_provider import (
    SEARCH_TOP_K,
    SOURCE_TRAJ_TOP_K,
    AgentExperienceContextProvider,
)
from openviking.session.memory.dataclass import MemoryField
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.telemetry import tracer

SOURCE_TRAJECTORY_IDS_FIELD = "source_trajectory_ids"


class BatchAgentExperienceContextProvider(AgentExperienceContextProvider):
    """Consolidate multiple new trajectories into experience memories in one pass."""

    def __init__(
        self,
        messages: Any,
        trajectory_items: List[Dict[str, str]],
        latest_archive_overview: str = "",
    ):
        if not trajectory_items:
            raise ValueError("trajectory_items must not be empty")
        self.trajectory_items = [
            {
                "id": f"T{idx + 1}",
                "uri": str(item.get("uri", "")),
                "content": str(item.get("content", "")),
            }
            for idx, item in enumerate(trajectory_items)
            if str(item.get("uri", "")).strip()
        ]
        if not self.trajectory_items:
            raise ValueError("trajectory_items must include at least one URI")
        self._trajectory_uri_by_id = {item["id"]: item["uri"] for item in self.trajectory_items}

        combined_summary = "\n\n".join(item["content"] for item in self.trajectory_items).strip()
        super().__init__(
            messages=messages,
            trajectory_summary=combined_summary,
            trajectory_uri=self.trajectory_items[0]["uri"],
            latest_archive_overview=latest_archive_overview,
        )

    def instruction(self) -> str:
        instruction = super().instruction()

        def replace_required(options: List[str], new: Any) -> str:
            nonlocal instruction
            for old in options:
                if old in instruction:
                    replacement = new(old) if callable(new) else new
                    instruction = instruction.replace(old, replacement)
                    return old
            raise ValueError(
                "AgentExperienceContextProvider instruction changed; "
                "update BatchAgentExperienceContextProvider prompt adapter."
            )

        def replace_optional(options: List[str], new: str) -> None:
            nonlocal instruction
            for old in options:
                if old in instruction:
                    instruction = instruction.replace(old, new)
                    return

        replace_required(
            ["- A new trajectory (the latest agent execution to incorporate)"],
            "- Multiple new trajectories from the latest committed session",
        )
        replace_optional(
            [
                "The new trajectory includes an `outcome` field. Read it before writing:",
            ],
            "Each new trajectory may include an `outcome` field. Read it before writing:",
        )
        replace_required(
            [
                "For each distinct behavioral pattern in the trajectory, output an experience entry with:",
                "For each distinct user intent in the trajectory, output a SEPARATE experience entry.",
            ],
            lambda old: (
                "For each distinct user intent across the new trajectories, "
                "output a SEPARATE experience entry."
                if "user intent" in old
                else "For each distinct behavioral pattern across the new trajectories, "
                "output an experience entry with:"
            ),
        )
        replace_optional(
            [
                "A single trajectory may contain multiple user intents — you MUST produce one entry per intent,\n"
                "not one entry for the whole trajectory.",
            ],
            (
                "The provided trajectories may contain multiple user intents — you MUST produce "
                "one entry per intent,\nnot one entry for the whole batch."
            ),
        )
        replace_required(
            [
                "- `content`: the full experience content (rewrite holistically, incorporating old + new)"
            ],
            (
                "- `content`: the full experience content (rewrite holistically, "
                "incorporating old + relevant new trajectories)\n"
                "- `source_trajectory_ids`: comma-separated new trajectory ids that directly "
                "support this experience"
            ),
        )
        replace_optional(
            [
                "- **One experience per distinct user intent.** If a trajectory covers N different user goals\n"
                "  (e.g., cancel + modify + add baggage), output N separate entries — never merge them into one.",
            ],
            (
                "- **One experience per distinct user intent.** If the provided trajectories cover N different "
                "user goals\n  (e.g., cancel + modify + add baggage), output N separate entries — never merge "
                "them into one."
            ),
        )
        replace_required(
            [
                "- **Consistent naming language.** All `experience_name` values in one output must use the same language.",
            ],
            (
                "- **Precise source attribution.** `source_trajectory_ids` MUST include only ids "
                "from the provided `new_trajectory` reads (for example `T1,T3`). Do not include "
                "a trajectory id unless its content directly supports that specific experience.\n"
                "- **Consistent naming language.** All `experience_name` values in one output must use the same language."
            ),
        )
        return instruction

    def get_memory_schemas(self, ctx):
        schemas = super().get_memory_schemas(ctx)
        if not schemas:
            return schemas
        schema = schemas[0].model_copy(deep=True)
        if all(field.name != SOURCE_TRAJECTORY_IDS_FIELD for field in schema.fields):
            schema.fields.append(
                MemoryField(
                    name=SOURCE_TRAJECTORY_IDS_FIELD,
                    field_type=FieldType.STRING,
                    description=(
                        "Batch-only attribution field. Provide comma-separated ids of the "
                        "new trajectories that directly support this experience, such as "
                        "`T1,T3`. The system consumes this field for source attribution and "
                        "does not persist it."
                    ),
                    merge_op=MergeOp.REPLACE,
                )
            )
        return [schema]

    def resolve_source_attribution(self, operations, ctx=None) -> Dict[str, List[str]]:
        """Return experience URI -> source trajectory URI mapping for this batch.

        The temporary `source_trajectory_ids` field is removed before memory apply so it
        does not become part of the persisted experience. Missing attribution is treated
        as unsafe and therefore skipped by the caller.
        """
        attribution: Dict[str, List[str]] = {}
        for op in getattr(operations, "upsert_operations", []) or []:
            if getattr(op, "memory_type", "") != "experiences":
                continue
            raw_ids = op.memory_fields.pop(SOURCE_TRAJECTORY_IDS_FIELD, "")
            if isinstance(raw_ids, list):
                ids = [str(item).strip() for item in raw_ids]
            else:
                ids = [
                    part.strip()
                    for part in str(raw_ids).replace("\n", ",").split(",")
                    if part.strip()
                ]
            source_uris = [
                self._trajectory_uri_by_id[item_id]
                for item_id in ids
                if item_id in self._trajectory_uri_by_id
            ]
            if not source_uris:
                continue
            for uri in getattr(op, "uris", []) or []:
                attribution[uri] = list(dict.fromkeys(source_uris))
        return attribution

    async def prefetch(self) -> List[Dict]:
        if not isinstance(self.messages, list):
            tracer.error(f"Expected List[Message], got {type(self.messages)}")
            return []

        ctx = self._ctx
        viking_fs = self._viking_fs

        experience_dir = self._render_experience_dir(ctx)
        candidate_uris: List[str] = []
        if experience_dir and viking_fs:
            candidate_uris = await self.search_files(
                query=(self.trajectory_summary[:1000] or "experience"),
                search_uris=[experience_dir],
                limit=SEARCH_TOP_K,
            )

            if not candidate_uris:
                try:
                    entries = await viking_fs.ls(experience_dir, output="original", ctx=ctx)
                    fallback_uris: List[str] = []
                    for entry in entries or []:
                        uri = str(entry.get("uri", "")) if isinstance(entry, dict) else ""
                        name = str(entry.get("name", "")) if isinstance(entry, dict) else ""
                        if not uri.endswith(".md"):
                            continue
                        if name in {".overview.md", ".abstract.md"}:
                            continue
                        if uri.endswith("/.overview.md") or uri.endswith("/.abstract.md"):
                            continue
                        fallback_uris.append(uri)
                    candidate_uris = fallback_uris[:SEARCH_TOP_K]
                except Exception as e:
                    tracer.error(f"Failed to list experiences in {experience_dir}: {e}")

        prefetch_messages: List[Dict[str, Any]] = [self._build_conversation_message()]
        for idx, item in enumerate(self.trajectory_items):
            add_tool_call_pair_to_messages(
                messages=prefetch_messages,
                call_id=f"new-trajectory-{idx}",
                tool_name="read",
                params={"uri": item["uri"]},
                result=self._build_context_result(
                    uri=item["uri"],
                    context_role="new_trajectory",
                    result={
                        "memory_type": "trajectories",
                        "source_trajectory_id": item["id"],
                        "content": item["content"],
                    },
                ),
            )

        call_id_seq = 0
        for idx, exp_uri in enumerate(candidate_uris):
            result = await self.read_file(exp_uri)
            if result is None:
                continue

            self.prefetched_uris.append(exp_uri)
            mf = self._read_file_contents.get(exp_uri)
            if not mf:
                continue

            add_tool_call_pair_to_messages(
                messages=prefetch_messages,
                call_id=call_id_seq,
                tool_name="read",
                params={"uri": exp_uri},
                result=self._build_context_result(
                    uri=exp_uri,
                    context_role="candidate_experience",
                    result=result,
                ),
            )
            call_id_seq += 1

            if idx < SOURCE_TRAJ_TOP_K and viking_fs:
                source_trajs = await self._load_source_trajectories(
                    exp_uri, mf.extra_fields, viking_fs, ctx
                )
                for source_idx, source_result in enumerate(source_trajs):
                    source_uri = source_result["uri"]
                    add_tool_call_pair_to_messages(
                        messages=prefetch_messages,
                        call_id=f"source-{idx}-{source_idx}",
                        tool_name="read",
                        params={"uri": source_uri},
                        result=self._build_context_result(
                            uri=source_uri,
                            context_role="candidate_source_trajectory",
                            result=source_result,
                        ),
                    )

        prefetch_messages.append(
            {
                "role": "user",
                "content": "\n".join(
                    [
                        "You have already read the conversation, multiple `new_trajectory` items, candidate experience memories, and optional `candidate_source_trajectory` references.",
                        "Treat each `new_trajectory` as a new execution to incorporate if it improves a durable experience.",
                        "Treat `candidate_experience` as existing memories you may update, replace, or skip.",
                        "Treat `candidate_source_trajectory` as reference-only context for understanding a candidate experience; do not modify it directly.",
                        "Based on the above, decide whether to **Update**, **Replace**, **Create**, or **Skip**. Output JSON only.",
                    ]
                ),
            }
        )
        return prefetch_messages
