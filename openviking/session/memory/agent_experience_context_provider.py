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
from typing import Any, Dict, List

from openviking.core.namespace import to_user_space, to_agent_space
from openviking.server.identity import RequestContext, ToolContext
from openviking.session.memory.dataclass import MemoryFileContent
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.tools import get_tool
from openviking.session.memory.utils import parse_memory_file_with_fields
from openviking.session.memory.utils.content import deserialize_content, deserialize_metadata
from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


EXPERIENCE_MEMORY_TYPE = "experiences"
SEARCH_TOP_K = 5
SOURCE_TRAJ_TOP_K = 3   # only attach source_trajectories for the top-3 candidates
MAX_SOURCE_TRAJS = 3    # max trajectories to load per experience


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
                results.append({"uri": uri, "content": deserialize_content(raw)})
            except Exception as e:
                logger.warning(f"Failed to read source trajectory {uri}: {e}")
        return results

    async def prefetch(self) -> List[Dict]:
        if not isinstance(self.messages, list):
            logger.warning(f"Expected List[Message], got {type(self.messages)}")
            return []

        ctx = self._ctx
        viking_fs = self._viking_fs
        transaction_handle = self._transaction_handle

        experience_dir = self._render_experience_dir(ctx)
        search_tool = get_tool("search")

        candidate_uris: List[str] = []
        if experience_dir and viking_fs:
            if search_tool:
                tool_ctx_search = ToolContext(
                    viking_fs=viking_fs,
                    request_ctx=ctx,
                    transaction_handle=transaction_handle,
                    default_search_uris=[experience_dir],
                )
                try:
                    search_result = await search_tool.execute(
                        viking_fs=viking_fs,
                        ctx=tool_ctx_search,
                        query=self.trajectory_summary[:500] or "experience",
                        limit=SEARCH_TOP_K,
                    )
                    if isinstance(search_result, list):
                        candidate_uris = [m.get("uri", "") for m in search_result if m.get("uri")]
                    elif isinstance(search_result, dict) and "memories" in search_result:
                        candidate_uris = [
                            m.get("uri", "")
                            for m in search_result.get("memories", [])
                            if m.get("uri")
                        ]
                except Exception as e:
                    logger.warning(f"Failed to search experiences in {experience_dir}: {e}")

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
                    logger.warning(f"Failed to list experiences in {experience_dir}: {e}")

        # Build candidate experiences section
        exp_sections: List[str] = []
        for idx, exp_uri in enumerate(candidate_uris):
            try:
                exp_raw = await viking_fs.read_file(exp_uri, ctx=ctx)
            except Exception as e:
                logger.warning(f"Failed to read experience {exp_uri}: {e}")
                continue

            self.prefetched_uris.append(exp_uri)
            # Populate read_file_contents so that:
            # 1. Update path: _check_unread_existing_files skips refetch (saves 1 LLM call)
            # 2. Replace path: resolve_operations can build delete_file_contents, enabling
            #    old file deletion and source_trajectories inheritance.
            parsed_fields = parse_memory_file_with_fields(exp_raw)
            self._read_file_contents[exp_uri] = MemoryFileContent(
                uri=exp_uri,
                plain_content=parsed_fields.get("content", ""),
                memory_fields=parsed_fields,
            )
            body = deserialize_content(exp_raw)
            meta = deserialize_metadata(exp_raw) or {}
            exp_name = meta.get("experience_name", "")

            section = f"### Experience {idx + 1}: `{exp_name}`\nURI: `{exp_uri}`\n\n{body}"

            # Attach source trajectories for top-3 only
            if idx < SOURCE_TRAJ_TOP_K and viking_fs:
                source_trajs = await self._load_source_trajectories(exp_uri, meta, viking_fs, ctx)
                if source_trajs:
                    traj_lines = ["\n#### Source Trajectories (for reference only)"]
                    for i, t in enumerate(source_trajs, 1):
                        traj_lines.append(f"\n**Trajectory {i}** (`{t['uri']}`):\n{t['content']}")
                    section += "\n" + "\n".join(traj_lines)

            exp_sections.append(section)

        # Assemble single user message
        lines = [
            "## New Trajectory",
            f"URI: `{self.trajectory_uri}`",
            "",
            self.trajectory_summary,
        ]

        if exp_sections:
            lines += [
                "",
                "---",
                "",
                "## Candidate Existing Experiences",
                "",
                "\n\n---\n\n".join(exp_sections),
            ]
        else:
            lines += [
                "",
                "---",
                "",
                "## Candidate Existing Experiences",
                "",
                "No existing experiences found.",
            ]

        lines += [
            "",
            "---",
            "",
            "Based on the above, decide whether to **Update**, **Replace**, **Create**, or **Skip**. Output JSON only.",
        ]

        return [{"role": "user", "content": "\n".join(lines)}]
