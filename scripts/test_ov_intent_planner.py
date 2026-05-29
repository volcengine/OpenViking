#!/usr/bin/env python3
"""Smoke-test OpenViking intent analysis through the configured query_planner.

This calls OpenViking's IntentAnalyzer directly. It does not call Ollama with a
hand-written payload, so it verifies the same model path used by search()
intent analysis.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from openviking_cli.retrieve.types import ContextType
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

DEFAULT_CONFIG = Path.home() / ".openviking" / "ov.conf"
EXPECTED_MODEL = "ollama/guoxuter/ov_intent_analysis_sft:v1_q8"


CASES: dict[str, dict[str, Any]] = {
    "rfc": {
        "name": "operational RFC task",
        "compression_summary": "",
        "recent_messages": [
            ("user", "帮我写一份 RFC 文档"),
            ("assistant", "好的，标题是什么？"),
        ],
        "current_message": "标题就叫《支付链路重构》，按公司标准模板来",
        "context_type": None,
        "target_abstract": "",
    },
    "format": {
        "name": "informational format question",
        "compression_summary": "",
        "recent_messages": [
            ("user", "我在做新项目的技术选型"),
            ("assistant", "好的，需要参考什么资料？"),
        ],
        "current_message": "RFC 文档的标准格式是什么？",
        "context_type": None,
        "target_abstract": "",
    },
    "chat": {
        "name": "conversational small talk",
        "compression_summary": "",
        "recent_messages": [
            ("user", "你好"),
            ("assistant", "你好，有什么可以帮你？"),
        ],
        "current_message": "今天天气挺好的",
        "context_type": None,
        "target_abstract": "",
    },
    "memory": {
        "name": "restricted memory query",
        "compression_summary": "",
        "recent_messages": [
            ("user", "帮我对比下这次活动和上月会员日的 ROI"),
        ],
        "current_message": "活动周期 7 天，预计 10 万用户参与",
        "context_type": ContextType.MEMORY,
        "target_abstract": "User's long-term memory; preferences / events / historical KPIs",
    },
}


def _message(i: int, role: str, text: str):
    from openviking.message import Message, TextPart

    return Message(id=f"case-msg-{i}", role=role, parts=[TextPart(text=text)])


def _messages(raw_messages: list[tuple[str, str]]) -> list[Any]:
    return [_message(i, role, text) for i, (role, text) in enumerate(raw_messages, start=1)]


def _load_config(config_path: Path, *, preserve_log_output: bool = False):
    os.environ[OPENVIKING_CONFIG_ENV] = str(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not preserve_log_output:
        data.setdefault("log", {})
        data["log"]["output"] = "stdout"
    return OpenVikingConfigSingleton.initialize(config_dict=data)


def _load_intent_analyzer_class():
    module_path = (
        Path(__file__).resolve().parents[1] / "openviking" / "retrieve" / "intent_analyzer.py"
    )
    spec = importlib.util.spec_from_file_location("_ov_intent_analyzer_direct", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load IntentAnalyzer from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.IntentAnalyzer


def _planner_summary(config: Any) -> dict[str, Any]:
    planner = config.get_query_planner()
    return {
        "provider": planner.provider,
        "model": planner.model,
        "api_base": planner.api_base,
        "max_tokens": planner.max_tokens,
        "timeout": planner.timeout,
        "extra_request_body": planner.extra_request_body,
        "uses_query_planner": planner is config.query_planner,
    }


def _print_plan(case_key: str, case_name: str, elapsed: float, plan: Any) -> None:
    print(f"\n=== {case_key}: {case_name} ===")
    print(f"elapsed_seconds: {elapsed:.3f}")
    print(f"reasoning: {plan.reasoning[:400]}")
    if not plan.queries:
        print("queries: []")
        return
    print("queries:")
    for idx, query in enumerate(plan.queries, start=1):
        context_type = query.context_type.value if query.context_type else None
        print(f"  {idx}. [{context_type}] p={query.priority} {query.query}")
        if query.intent:
            print(f"     intent: {query.intent}")


async def _run_case(
    intent_analyzer_class: Any,
    case_key: str,
    case: dict[str, Any],
) -> dict[str, Any]:
    analyzer = intent_analyzer_class(max_recent_messages=5)
    start = time.perf_counter()
    plan = await analyzer.analyze(
        compression_summary=case["compression_summary"],
        messages=_messages(case["recent_messages"]),
        current_message=case["current_message"],
        context_type=case["context_type"],
        target_abstract=case["target_abstract"],
    )
    elapsed = time.perf_counter() - start
    _print_plan(case_key, case["name"], elapsed, plan)
    return {
        "case": case_key,
        "elapsed_seconds": elapsed,
        "plan": {
            "reasoning": plan.reasoning,
            "queries": [asdict(query) for query in plan.queries],
        },
    }


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to ov.conf. Defaults to ~/.openviking/ov.conf.",
    )
    parser.add_argument(
        "--case",
        choices=["all", *CASES.keys()],
        default="all",
        help="Scenario to run.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable summary at the end.",
    )
    parser.add_argument(
        "--preserve-log-output",
        action="store_true",
        help="Use ov.conf log.output as-is. By default this smoke test logs to stdout.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    config = _load_config(config_path, preserve_log_output=args.preserve_log_output)
    intent_analyzer_class = _load_intent_analyzer_class()
    planner = _planner_summary(config)

    print(f"config: {config_path}")
    print("query_planner:")
    print(json.dumps(planner, ensure_ascii=False, indent=2))

    if not planner["uses_query_planner"]:
        print("ERROR: ov.conf did not load a dedicated query_planner.", file=sys.stderr)
        return 2
    if planner["model"] != EXPECTED_MODEL:
        print(
            f"ERROR: expected model {EXPECTED_MODEL!r}, got {planner['model']!r}.",
            file=sys.stderr,
        )
        return 2

    selected = CASES.keys() if args.case == "all" else [args.case]
    results = []
    for case_key in selected:
        results.append(await _run_case(intent_analyzer_class, case_key, CASES[case_key]))

    if args.json:
        print("\n=== json_summary ===")
        print(json.dumps({"query_planner": planner, "results": results}, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
