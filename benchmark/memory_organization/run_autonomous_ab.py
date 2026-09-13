#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from benchmark.memory_organization.autonomous_cases import AutonomousCase, build_cases
from benchmark.memory_organization.autonomous_grader import (
    autonomous_metrics,
    grade_autonomous_result,
)
from benchmark.memory_organization.run_ab import _call_count
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)

ROOT = Path(__file__).resolve().parent
Job = tuple[int, str, str, int]


class AutonomousProvider(SessionExtractContextProvider):
    def __init__(self, case: AutonomousCase, *, output_language: str = "") -> None:
        super().__init__(messages=list(case.messages), viking_fs=object())
        self.case = case
        self._benchmark_output_language = output_language.strip()
        self._read_file_contents = dict(case.initial_files)

    def get_output_language(self) -> str:
        return self._benchmark_output_language or super().get_output_language()

    async def prefetch(self) -> list[dict[str, Any]]:
        from openviking.session.memory.tools import (
            add_tool_call_pair_to_messages,
            memory_maintenance_notice,
        )

        # Keep the production conversation wrapper and add only the read results
        # that the real prefetch/search path would have selected. No benchmark
        # maintenance instruction or expected organization is shown to the model.
        messages: list[dict[str, Any]] = [self._build_conversation_message()]
        for call_id, (uri, memory_file) in enumerate(self.read_file_contents.items(), start=1):
            page_id = self._extract_context.page_id_map.get_page_id(uri)
            result = memory_file.to_metadata()
            result["page_id"] = page_id
            maintenance_notice = memory_maintenance_notice(
                memory_file.plain_content() or "",
                review_after_tokens=self.case.maintenance_review_tokens,
            )
            if maintenance_notice is not None:
                result["memory_maintenance_notice"] = maintenance_notice
            add_tool_call_pair_to_messages(
                messages,
                call_id=call_id,
                tool_name="read",
                params={"uri": uri},
                result=result,
            )
        return messages

    async def execute_tool(self, tool_call: Any) -> dict[str, str]:
        from openviking.session.memory.tools import memory_maintenance_notice

        uri = str(tool_call.arguments.get("uri", ""))
        memory_file = self.read_file_contents.get(uri)
        if memory_file is None:
            return {"error": f"File not found: {uri}"}
        result = memory_file.to_metadata()
        result["page_id"] = self.get_extract_context().page_id_map.get_page_id(uri)
        maintenance_notice = memory_maintenance_notice(
            memory_file.plain_content() or "",
            review_after_tokens=self.case.maintenance_review_tokens,
        )
        if maintenance_notice is not None:
            result["memory_maintenance_notice"] = maintenance_notice
        return result


class AutonomousIsolationHandler:
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
        from openviking.session.memory.utils.template_utils import TemplateUtils

        path = f"{memory_type_schema.directory}/{memory_type_schema.filename_template}"
        fields = {"user_space": "default", **operation.memory_fields}
        return [TemplateUtils.render(path, fields, extract_context)]


async def materialize(
    case: AutonomousCase, operations: Any
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    from openviking.session.memory.merge_op import FieldType, PatchOp

    files = {
        uri: (str(memory_file.memory_type), memory_file.content)
        for uri, memory_file in case.initial_files.items()
    }
    for memory_file in operations.delete_file_contents:
        if memory_file.uri:
            files.pop(memory_file.uri, None)
    patch_op = PatchOp(FieldType.STRING)
    for operation in operations.upsert_operations:
        if not operation.uris:
            continue
        uri = operation.uris[0]
        value = operation.memory_fields.get("content")
        if operation.old_memory_file_content is not None:
            value = await patch_op.apply(operation.old_memory_file_content.content, value)
        files[uri] = (operation.memory_type, str(value or ""))
    return files, dict(operations.delete_replacements)


async def run_one(
    case: AutonomousCase,
    protocol: str,
    repeat_index: int,
    config: Any,
    *,
    output_language: str = "",
) -> dict:
    from openviking.session.memory.extract_loop import ExtractLoop

    provider = AutonomousProvider(case, output_language=output_language)
    config.memory.extraction_output_format = protocol
    config.memory.link_enabled = False
    vlm = config.vlm.get_vlm_instance()
    vlm.reset_token_usage()
    raw_responses: list[dict[str, Any]] = []
    original_get_completion_async = vlm.get_completion_async

    async def recording_get_completion_async(*args: Any, **kwargs: Any) -> Any:
        response = await original_get_completion_async(*args, **kwargs)
        if isinstance(response, str):
            raw_responses.append({"content": response, "tool_calls": []})
        else:
            raw_responses.append(
                {
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        }
                        for tool_call in response.tool_calls
                    ],
                }
            )
        return response

    vlm.get_completion_async = recording_get_completion_async
    loop = ExtractLoop(
        vlm=vlm,
        viking_fs=object(),
        context_provider=provider,
        isolation_handler=AutonomousIsolationHandler(),
        max_iterations=3,
        thinking=False,
    )
    started = time.perf_counter()
    try:
        try:
            operations, _ = await loop.run()
            files, replacements = await materialize(case, operations)
            grade = grade_autonomous_result(case, files, replacements)
            decision_metrics = autonomous_metrics(case, files, replacements, grade)
            terminal_error = "; ".join(operations.errors)
        except Exception as exc:
            files = {
                uri: (str(memory_file.memory_type), memory_file.content)
                for uri, memory_file in case.initial_files.items()
            }
            replacements = {}
            grade = grade_autonomous_result(case, files, replacements)
            decision_metrics = autonomous_metrics(case, files, replacements, grade)
            terminal_error = f"{type(exc).__name__}: {exc}"
    finally:
        vlm.get_completion_async = original_get_completion_async
    usage_report = vlm.get_token_usage()
    usage = usage_report.get("total_usage", {})
    calls = _call_count(usage_report)
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
        "autonomous_metrics": decision_metrics,
        "actual_files": {
            uri: {"memory_type": memory_type, "content": content}
            for uri, (memory_type, content) in files.items()
        },
        "actual_replacements": replacements,
        "raw_responses": raw_responses,
        "calls": calls,
        "retries": max(0, calls - 1),
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "terminal_error": terminal_error,
    }


def build_jobs(cases: list[AutonomousCase], repeat: int) -> list[Job]:
    """Build counterbalanced JSON/Python jobs with stable unique indices."""
    protocols = ("json", "python")
    jobs: list[Job] = []
    for repeat_index in range(repeat):
        for case_index, case in enumerate(cases):
            ordered = (
                protocols if (repeat_index + case_index) % 2 == 0 else tuple(reversed(protocols))
            )
            for protocol in ordered:
                jobs.append((len(jobs), case.case_id, protocol, repeat_index))
    return jobs


def _run_job_in_process(
    config_path: str,
    case_id: str,
    protocol: str,
    repeat_index: int,
    output_language: str,
) -> dict[str, Any]:
    """Run one job in a process-isolated config/VLM environment."""
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    config = OpenVikingConfigSingleton.initialize(config_path=config_path)
    case = next(case for case in build_cases() if case.case_id == case_id)

    async def execute() -> dict[str, Any]:
        try:
            return await run_one(
                case,
                protocol,
                repeat_index,
                config,
                output_language=output_language,
            )
        finally:
            await _close_vlm_async_clients(config.vlm._vlm_instance)

    return asyncio.run(execute())


async def _close_vlm_async_clients(vlm: Any) -> None:
    """Close loop-bound clients before a process worker's event loop exits."""
    if vlm is None:
        return
    cache = getattr(vlm, "_async_client_cache", None)
    if cache is not None:
        for client in cache.pop_all():
            close = getattr(client, "close", None) or getattr(client, "aclose", None)
            if close is None:
                continue
            result = close()
            if inspect.isawaitable(result):
                await result
    for child_name in ("primary", "backup", "vlm_instances", "_vlm_instances"):
        children = getattr(vlm, child_name, None)
        if children is None:
            continue
        if not isinstance(children, (list, tuple)):
            children = [children]
        for child in children:
            await _close_vlm_async_clients(child)


def _append_result(output: Path, row: dict[str, Any], done: int, total: int) -> None:
    with output.open("a") as fp:
        fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    metrics = row["autonomous_metrics"]
    action = "PASS" if metrics["organization_action_success"] else "FAIL"
    integrity = "PASS" if metrics["information_integrity"] else "FAIL"
    print(
        f"[{done}/{total}] {row['protocol']} {row['case_id']}: "
        f"action={action} integrity={integrity}",
        flush=True,
    )


async def main_async(args: argparse.Namespace) -> None:
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    config = OpenVikingConfigSingleton.initialize(config_path=args.config)
    cases = build_cases()
    selected = set(args.case or [])
    if selected:
        cases = [case for case in cases if case.case_id in selected]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("")
    jobs = build_jobs(cases, args.repeat)
    total = len(jobs)
    if args.parallel == 1:
        for done, (_index, case_id, protocol, repeat_index) in enumerate(jobs, start=1):
            case = next(case for case in cases if case.case_id == case_id)
            row = await run_one(
                case,
                protocol,
                repeat_index,
                config,
                output_language=args.output_language,
            )
            _append_result(output, row, done, total)
        return

    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=args.parallel) as executor:
        futures = [
            loop.run_in_executor(
                executor,
                _run_job_in_process,
                args.config,
                case_id,
                protocol,
                repeat_index,
                args.output_language,
            )
            for _index, case_id, protocol, repeat_index in jobs
        ]
        for done, future in enumerate(asyncio.as_completed(futures), start=1):
            _append_result(output, await future, done, total)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.expanduser("~/.openviking/ov.conf"))
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Maximum process-isolated jobs to run concurrently (default: 1)",
    )
    parser.add_argument(
        "--output-language",
        default="",
        help="Optional production output-language override for this benchmark run",
    )
    parser.add_argument("--output", default=str(ROOT / "result" / "autonomous_results.jsonl"))
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")
    if args.parallel < 1:
        parser.error("--parallel must be >= 1")
    return args


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
