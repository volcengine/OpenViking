#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from benchmark.memory_organization.grader import grade_result
from benchmark.memory_organization.models import OrganizationCase, load_cases, topic_from_uri

ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ROOT / "cases" / "organization_cases.json"


class FixtureProvider:
    def __init__(self, case: OrganizationCase, schema: Any, memory_files: dict[str, Any]) -> None:
        from openviking.session.memory.memory_updater import ExtractContext
        from openviking.session.memory.page_id_map import PageIdMap

        self.case = case
        self.schema = schema
        self.read_file_contents = memory_files
        self._extract_context = ExtractContext([])
        self._extract_context.page_id_map = PageIdMap()

    def instruction(self) -> str:
        return (
            "Maintain the existing memory collection according to its schema. Decide whether any "
            "files should remain unchanged, be merged, or be split; the number and names of final "
            "files are your decision. Preserve every complete [Fxx] fact line exactly once and do "
            "not invent facts. Delete superseded source files. When multiple files merge into one "
            "unambiguous successor, use that survivor as the replacement so links can be inherited; "
            "a source split across multiple successors has no replacement. Choose concise lowercase "
            "snake_case topics. An empty operation plan is valid when the collection already satisfies "
            "the schema. Output only the required final operation protocol."
        )

    def get_memory_schemas(self, ctx: Any) -> list[Any]:
        del ctx
        return [self.schema]

    def get_output_language(self) -> str:
        return "en"

    def get_tools(self) -> list[str]:
        return []

    def get_extract_context(self) -> Any:
        return self._extract_context

    async def prefetch(self) -> list[dict[str, Any]]:
        from openviking.session.memory.tools import add_tool_call_pair_to_messages

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Memory maintenance case {self.case.case_id}. Review all files below as one "
                    "atomic collection. Fact markers [Fxx] are immutable grading anchors."
                ),
            }
        ]
        for call_id, (uri, memory_file) in enumerate(self.read_file_contents.items(), start=1):
            page_id = self._extract_context.page_id_map.get_page_id(uri)
            result = memory_file.to_metadata()
            result["page_id"] = page_id
            add_tool_call_pair_to_messages(
                messages,
                call_id=call_id,
                tool_name="read",
                params={"uri": uri},
                result=result,
            )
        return messages

    async def execute_tool(self, tool_call: Any) -> dict[str, str]:
        return {"error": f"File not found: {tool_call.arguments.get('uri', '')}"}


class FixtureIsolationHandler:
    def get_read_scope(self) -> Any:
        from openviking.session.memory.memory_isolation_handler import RoleScope

        return RoleScope(user_ids=[], peer_ids=[])

    def fill_identity_fields(self, item_dict: dict[str, Any], **kwargs: Any) -> None:
        del kwargs
        item_dict.pop("peer_id", None)

    def calculate_memory_uris(
        self,
        *,
        memory_type_schema: Any,
        operation: Any,
        extract_context: Any,
    ) -> list[str]:
        del extract_context
        topic = str(operation.memory_fields["topic"])
        return [f"{memory_type_schema.directory}/{topic}.md"]


def build_schema(case: OrganizationCase) -> Any:
    from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
    from openviking.session.memory.merge_op import FieldType, MergeOp

    size_rule = (
        f" A file may contain at most {case.max_fact_lines_per_file} [Fxx] fact lines. "
        "If it exceeds that limit, reorganize it into coherent files that each fit the limit."
        if case.max_fact_lines_per_file is not None
        else ""
    )
    return MemoryTypeSchema(
        memory_type="records",
        description=(
            "A collection of topic-focused memory files. Each file must cover exactly one "
            "coherent topic. Merge aliases and split mixed files. Preserve [Fxx] markers."
            + size_rule
        ),
        directory="viking://user/default/memories/records",
        filename_template="{{ topic }}.md",
        peer_enabled=False,
        fields=[
            MemoryField(
                name="topic",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
                description="Canonical lowercase snake_case topic used as the filename.",
            ),
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.REPLACE,
                description=(
                    "Complete content for exactly one topic. Preserve every source [Fxx] marker "
                    "verbatim and include each marker once."
                ),
            ),
        ],
    )


def build_memory_files(case: OrganizationCase, schema: Any) -> dict[str, Any]:
    from openviking.session.memory.dataclass import MemoryFile

    return {
        f"{schema.directory}/{topic}.md": MemoryFile(
            uri=f"{schema.directory}/{topic}.md",
            memory_type=schema.memory_type,
            content=content,
            extra_fields={"topic": topic},
        )
        for topic, content in case.initial_files.items()
    }


def materialize(
    case: OrganizationCase,
    operations: Any,
) -> tuple[dict[str, str], dict[str, str]]:
    files = dict(case.initial_files)
    for memory_file in operations.delete_file_contents:
        if memory_file.uri:
            files.pop(topic_from_uri(memory_file.uri), None)
    for operation in operations.upsert_operations:
        topic = str(operation.memory_fields["topic"])
        files[topic] = str(operation.memory_fields.get("content") or "")
    replacements = {
        topic_from_uri(source): topic_from_uri(target)
        for source, target in operations.delete_replacements.items()
    }
    return files, replacements


def _call_count(usage: dict[str, Any]) -> int:
    """Sum model call counts because TokenUsageTracker.total_usage omits call_count."""
    return sum(
        int(model.get("total_usage", {}).get("call_count", 0))
        for model in usage.get("usage_by_model", {}).values()
    )


async def run_one(case: OrganizationCase, protocol: str, repeat_index: int, config: Any) -> dict:
    from openviking.session.memory.extract_loop import ExtractLoop

    schema = build_schema(case)
    memory_files = build_memory_files(case, schema)
    provider = FixtureProvider(case, schema, memory_files)
    isolation = FixtureIsolationHandler()
    config.memory.extraction_output_format = protocol
    config.memory.link_enabled = False
    vlm = config.vlm.get_vlm_instance()
    vlm.reset_token_usage()
    loop = ExtractLoop(
        vlm=vlm,
        viking_fs=object(),
        context_provider=provider,
        isolation_handler=isolation,
        max_iterations=3,
        thinking=False,
    )
    started = time.perf_counter()
    terminal_error = ""
    try:
        operations, _ = await loop.run()
        files, replacements = materialize(case, operations)
        grade = grade_result(case, files, replacements)
        terminal_error = "; ".join(operations.errors)
    except Exception as exc:
        files, replacements = dict(case.initial_files), {}
        grade = grade_result(case, files, replacements)
        terminal_error = f"{type(exc).__name__}: {exc}"
    duration = time.perf_counter() - started
    usage_report = vlm.get_token_usage()
    usage = usage_report.get("total_usage", {})
    return {
        "case_id": case.case_id,
        "category": case.category,
        "protocol": protocol,
        "repeat_index": repeat_index,
        "model": {
            "provider": vlm.provider,
            "name": vlm.model,
            "temperature": vlm.temperature,
            "thinking": vlm.thinking,
            "max_tokens": vlm.max_tokens,
        },
        "grade": grade.to_dict(),
        "actual_files": files,
        "actual_replacements": replacements,
        "calls": _call_count(usage_report),
        "retries": max(0, _call_count(usage_report) - 1),
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
        "duration_seconds": round(duration, 3),
        "terminal_error": terminal_error,
    }


async def main_async(args: argparse.Namespace) -> None:
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    config = OpenVikingConfigSingleton.initialize(config_path=args.config)
    cases = load_cases(Path(args.cases))
    selected = set(args.case or [])
    if selected:
        cases = [case for case in cases if case.case_id in selected]
    protocols = tuple(dict.fromkeys(args.protocol))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("")
    total = len(cases) * args.repeat * len(protocols)
    done = 0
    for repeat_index in range(args.repeat):
        for case_index, case in enumerate(cases):
            # Counterbalance temporal/provider drift instead of always giving one
            # protocol the first call in every pair.
            pair_protocols = (
                protocols if (repeat_index + case_index) % 2 == 0 else tuple(reversed(protocols))
            )
            for protocol in pair_protocols:
                row = await run_one(case, protocol, repeat_index, config)
                with output.open("a") as fp:
                    fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                done += 1
                status = "PASS" if row["grade"]["organization_success"] else "FAIL"
                print(f"[{done}/{total}] {protocol} {case.case_id}: {status}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.path.expanduser("~/.openviking/ov.conf"))
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--case", action="append", help="Run one case id (repeatable)")
    parser.add_argument("--protocol", action="append", choices=("json", "python"), default=[])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", default=str(ROOT / "result" / "ab_results.jsonl"))
    args = parser.parse_args()
    args.protocol = args.protocol or ["json", "python"]
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
