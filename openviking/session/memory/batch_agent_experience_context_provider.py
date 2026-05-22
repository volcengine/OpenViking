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
        output_language = self._output_language
        return f"""You are a memory extraction agent. Your job is to distill experience memories from agent execution trajectories.

You are given:
- Multiple new trajectories from the latest committed session
- Up to {SEARCH_TOP_K} candidate existing experiences (retrieved by relevance). Top candidates also include their source trajectories as grounding material.

The source trajectories are for reference only — do NOT include or modify them in your output.

## What to output

For each distinct behavioral pattern across the new trajectories, output an experience entry with:
- `experience_name`: the name of the experience (new or existing)
- `content`: the full experience content (rewrite holistically, incorporating old + relevant new trajectories)
- `source_trajectory_ids`: comma-separated new trajectory ids that directly support this experience
- `supersedes`: the `experience_name` of an older experience this one replaces — set ONLY when the new name is genuinely different and broader. Leave empty otherwise.

The system handles create vs update automatically:
- Same `experience_name` as an existing one → updates it in place
- New `experience_name` → creates a new experience
- `supersedes` set → old experience is deleted and its history is inherited

## Rules

- **One experience per distinct pattern.** Multiple experiences are only valid for genuinely independent behavioral patterns with different triggers and action sequences.
- **No near-duplicates.** Merge experiences that share the same trigger or approach into one.
- **Only incorporate relevant trajectories.** If a new trajectory does not improve any durable experience, skip it.
- **Precise source attribution.** `source_trajectory_ids` MUST include only ids from the provided
  `new_trajectory` reads (for example `T1,T3`). Do not include a trajectory id unless its content
  directly supports that specific experience.
- **Consistent naming language.** All `experience_name` values in one output must use the same language.
- **Do NOT use `delete_uris`** for experience operations — use `supersedes` instead.
- Follow field descriptions in the schema.
- Output JSON only. Do not call any tools.

All memory content must be written in {output_language}.
"""

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
