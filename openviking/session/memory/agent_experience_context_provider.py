# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Agent Experience Context Provider - Phase 2 of agent-scope memory extraction.

Given a new trajectory summary from Phase 1, search for candidate experiences and
let the LLM decide whether to update an existing one, create a new one, or do nothing.

No tool calls — all context is prefetched. Top-3 candidates also include their
source_trajectories as grounding material.
"""

import jinja2
from typing import Any, Dict, List, Optional

from openviking.core.namespace import to_agent_space, to_user_space
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import add_tool_call_pair_to_messages
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


EXPERIENCE_MEMORY_TYPE = "experiences"
SEARCH_TOP_K = 5
SOURCE_TRAJ_TOP_K = 3  # only attach source_trajectories for the top-3 candidates
MAX_SOURCE_TRAJS = 3  # max trajectories to load per experience


class AgentExperienceContextProvider(SessionExtractContextProvider):
    """Phase 2 provider: consolidate the new trajectory into experience memories."""

    def __init__(
        self,
        messages: Any,
        trajectory_summary: str,
        trajectory_uri: str,
        latest_archive_overview: str = "",
    ):
        super().__init__(messages=messages, latest_archive_overview=latest_archive_overview)
        self.trajectory_summary = trajectory_summary
        self.trajectory_uri = trajectory_uri
        self.prefetched_uris: List[str] = []

    def instruction(self) -> str:
        output_language = self._output_language
        return f"""You are a memory extraction agent. Your job is to distill experience memories from agent execution trajectories.

You are given:
- A new trajectory (the latest agent execution to incorporate)
- Up to {SEARCH_TOP_K} candidate existing experiences (retrieved by relevance). Top candidates also include their source trajectories as grounding material.

The source trajectories are for reference only — do NOT include or modify them in your output.

## What to output

For each distinct behavioral pattern in the trajectory, output an experience entry with:
- `experience_name`: the name of the experience (new or existing)
- `content`: the full experience content (rewrite holistically, incorporating old + new)
- `supersedes`: the `experience_name` of an older experience this one replaces — set ONLY when the new name is genuinely different and broader. Leave empty otherwise.

The system handles create vs update automatically:
- Same `experience_name` as an existing one → updates it in place
- New `experience_name` → creates a new experience
- `supersedes` set → old experience is deleted and its history is inherited

## Rules

- **One experience per distinct pattern.** Multiple experiences are only valid for genuinely independent behavioral patterns with different triggers and action sequences.
- **No near-duplicates.** Merge experiences that share the same trigger or approach into one.
- **Consistent naming language.** All `experience_name` values in one output must use the same language.
- **Do NOT use `delete_uris`** for experience operations — use `supersedes` instead.
- Follow field descriptions in the schema.
- Output JSON only. Do not call any tools.

All memory content must be written in {output_language}.
"""

    def get_memory_schemas(self, ctx: RequestContext) -> List[Any]:
        registry = self._get_registry()
        schema = registry.get(EXPERIENCE_MEMORY_TYPE)
        if schema is None or not schema.enabled:
            return []
        return [schema]

    def get_tools(self) -> List[str]:
        return []

    def _render_experience_dir(self, ctx: RequestContext) -> str:
        registry = self._get_registry()
        schema = registry.get(EXPERIENCE_MEMORY_TYPE)
        if schema is None or not schema.directory:
            return ""

        if ctx and ctx.user:
            user_space = to_user_space(ctx.namespace_policy, ctx.user.user_id, ctx.user.agent_id)
            agent_space = to_agent_space(ctx.namespace_policy, ctx.user.user_id, ctx.user.agent_id)
        else:
            user_space = "default"
            agent_space = "default"

        env = jinja2.Environment(autoescape=False)
        return env.from_string(schema.directory).render(
            user_space=user_space, agent_space=agent_space
        )

    async def _load_source_trajectories(
        self,
        exp_uri: str,
        exp_meta: Dict,
        viking_fs: VikingFS,
        ctx: RequestContext,
    ) -> List[Dict]:
        """Load the most recent source trajectories for a candidate experience."""
        raw = exp_meta.get("source_trajectories", [])
        if isinstance(raw, list):
            uris = [str(u).strip() for u in raw if str(u).strip()]
        elif isinstance(raw, str):
            uris = [line.strip() for line in raw.splitlines() if line.strip()]
        else:
            uris = []

        recent_uris = uris[-MAX_SOURCE_TRAJS:]
        results = []
        for uri in recent_uris:
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx) or ""
                mf = MemoryFileUtils.read(raw, uri=uri)
                result = mf.to_metadata()
                result["content"] = mf.content
                result["uri"] = uri
                results.append(result)
            except Exception as e:
                tracer.error(f"Failed to read source trajectory {uri}: {e}")
        return results

    def _build_context_result(
        self,
        *,
        uri: str,
        context_role: str,
        result: Optional[Dict[str, Any]] = None,
        memory_file: Optional[MemoryFile] = None,
    ) -> Dict[str, Any]:
        payload = dict(result or {})
        if memory_file is not None:
            payload = memory_file.to_metadata()
            payload["content"] = memory_file.content
        payload["uri"] = uri
        payload["context_role"] = context_role
        return payload

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
                query=self.trajectory_summary[:500] or "experience",
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
        add_tool_call_pair_to_messages(
            messages=prefetch_messages,
            call_id="new-trajectory",
            tool_name="read",
            params={"uri": self.trajectory_uri},
            result=self._build_context_result(
                uri=self.trajectory_uri,
                context_role="new_trajectory",
                result={
                    "memory_type": "trajectories",
                    "content": self.trajectory_summary,
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
                        "You have already read the conversation, one `new_trajectory`, candidate experience memories, and optional `candidate_source_trajectory` references.",
                        "Treat `new_trajectory` as the new execution to incorporate.",
                        "Treat `candidate_experience` as existing memories you may update, replace, or skip.",
                        "Treat `candidate_source_trajectory` as reference-only context for understanding a candidate experience; do not modify it directly.",
                        "Based on the above, decide whether to **Update**, **Replace**, **Create**, or **Skip**. Output JSON only.",
                    ]
                ),
            }
        )
        return prefetch_messages
