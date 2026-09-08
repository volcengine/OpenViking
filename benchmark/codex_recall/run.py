#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Measure Codex auto-recall latency, precision, recall, and injection size."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_HOOK = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "codex-memory-plugin"
    / "scripts"
    / "auto-recall.mjs"
)
_TOKEN_ENCODER: Any = None
_TOKENIZER_NAME = "conservative-cjk"


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON") from exc
        case_id = str(value.get("id", "")).strip()
        query = str(value.get("query", "")).strip()
        expected = str(value.get("expected", "")).strip().lower()
        if not case_id or not query or expected not in {"accept", "abstain"}:
            raise ValueError(
                f"line {line_number}: id, query, and expected=accept|abstain are required"
            )
        if case_id in seen_ids:
            raise ValueError(f"line {line_number}: duplicate id: {case_id}")
        seen_ids.add(case_id)
        raw_gold = value.get("gold_uris", value.get("gold_uri", []))
        if isinstance(raw_gold, str):
            raw_gold = [raw_gold]
        if not isinstance(raw_gold, list) or not all(isinstance(uri, str) for uri in raw_gold):
            raise ValueError(f"line {line_number}: gold_uri(s) must be strings")
        gold_uris = [uri.strip() for uri in raw_gold if uri.strip()]
        cases.append(
            {
                "id": case_id,
                "query": query,
                "expected": expected,
                "gold_uris": gold_uris,
            }
        )
    if not cases:
        raise ValueError("input contains no cases")
    return cases


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[max(0, index)], 2)


def count_tokens(text: str) -> tuple[int, str]:
    global _TOKEN_ENCODER, _TOKENIZER_NAME
    if _TOKEN_ENCODER is None:
        try:
            import tiktoken

            _TOKEN_ENCODER = tiktoken.get_encoding("o200k_base")
            _TOKENIZER_NAME = "o200k_base"
        except (ImportError, ValueError):
            _TOKEN_ENCODER = False
    if _TOKEN_ENCODER:
        return len(_TOKEN_ENCODER.encode(text)), _TOKENIZER_NAME

    tokens = 0
    latin_run = 0
    for char in text:
        if ord(char) > 255:
            if latin_run:
                tokens += math.ceil(latin_run / 4)
                latin_run = 0
            tokens += 1
        else:
            latin_run += 1
    if latin_run:
        tokens += math.ceil(latin_run / 4)
    return tokens, _TOKENIZER_NAME


def extract_context(stdout: str) -> str:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("hook returned invalid JSON") from exc
    context = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
    return context if isinstance(context, str) else ""


def parse_variant_env(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected OPENVIKING_NAME=VALUE")
    name, raw_value = value.split("=", 1)
    name = name.strip()
    allowed = (
        name.startswith("OPENVIKING_RECALL_"),
        name.startswith("OPENVIKING_FAST_RECALL_"),
        name in {"OPENVIKING_SCORE_THRESHOLD", "OPENVIKING_MIN_QUERY_LENGTH"},
    )
    if not any(allowed) or not name.replace("_", "").isalnum():
        raise argparse.ArgumentTypeError("variant env must be a recall tuning setting")
    return name, raw_value


def build_environment(args: argparse.Namespace, config_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "OPENVIKING_AUTO_RECALL": "1",
            "OPENVIKING_CREDENTIAL_SOURCE": "env",
            "OPENVIKING_URL": args.url,
            "OPENVIKING_CONFIG_FILE": str(config_dir / "missing-ov.conf"),
            "OPENVIKING_CLI_CONFIG_FILE": str(config_dir / "missing-ovcli.conf"),
            "OPENVIKING_RECALL_COMPRESS": "1" if args.compression == "on" else "0",
            "OPENVIKING_RECALL_QUERY_EXPANSION": args.query_expansion,
            "OPENVIKING_RECALL_MAX_TOKENS": str(args.max_tokens),
            "OPENVIKING_RECALL_DEDUP_TURNS": "0",
            "OPENVIKING_RECALL_TIMEOUT_MS": str(round(args.timeout * 1000)),
            "OPENVIKING_TIMEOUT_MS": str(round(args.timeout * 1000)),
            "OPENVIKING_WORKSPACE_PEER": "0",
            "OPENVIKING_DEBUG": "0",
        }
    )
    optional = {
        "OPENVIKING_API_KEY": args.api_key,
        "OPENVIKING_ACCOUNT": args.account,
        "OPENVIKING_USER": args.user,
        "OPENVIKING_PEER_ID": args.actor_peer,
    }
    for name, value in optional.items():
        if value:
            env[name] = value
        else:
            env.pop(name, None)
    env.update(dict(args.variant_env))
    return env


def run_hook(
    *,
    node: str,
    hook: Path,
    query: str,
    session_id: str,
    env: dict[str, str],
    timeout: float,
) -> tuple[str, float]:
    payload = json.dumps({"prompt": query, "session_id": session_id}, ensure_ascii=False)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [node, str(hook)],
            input=payload,
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout + 2.0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("hook process timed out") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    if completed.returncode != 0:
        raise ValueError(f"hook process exited with status {completed.returncode}")
    return extract_context(completed.stdout), elapsed_ms


def summarize(records: list[dict[str, Any]], tokenizer: str) -> dict[str, Any]:
    negative = [record for record in records if record["expected"] == "abstain"]
    positive = [record for record in records if record["expected"] == "accept"]
    latencies = [float(record["latency_ms"]) for record in records]
    injection_tokens = [int(record["injection_tokens"]) for record in records]
    return {
        "runs": len(records),
        "negative_runs": len(negative),
        "positive_runs": len(positive),
        "false_injection_rate": round(
            sum(record["injected"] for record in negative) / len(negative), 4
        )
        if negative
        else 0.0,
        "positive_recall_rate": round(sum(record["hit"] for record in positive) / len(positive), 4)
        if positive
        else 0.0,
        "latency_ms_p50": percentile(latencies, 0.5),
        "latency_ms_p95": percentile(latencies, 0.95),
        "injection_tokens_p50": percentile(injection_tokens, 0.5),
        "injection_tokens_p95": percentile(injection_tokens, 0.95),
        "tokenizer": tokenizer,
    }


def threshold_failures(summary: dict[str, Any], args: argparse.Namespace) -> list[str]:
    checks = (
        ("latency_ms_p50", args.max_p50_ms, lambda actual, limit: actual <= limit),
        ("latency_ms_p95", args.max_p95_ms, lambda actual, limit: actual <= limit),
        (
            "false_injection_rate",
            args.max_false_injection_rate,
            lambda actual, limit: actual <= limit,
        ),
        (
            "positive_recall_rate",
            args.min_positive_recall_rate,
            lambda actual, limit: actual >= limit,
        ),
        (
            "injection_tokens_p95",
            args.max_injection_p95_tokens,
            lambda actual, limit: actual <= limit,
        ),
    )
    return [
        name
        for name, limit, check in checks
        if limit is not None and not check(summary[name], limit)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL benchmark cases")
    parser.add_argument("--output", type=Path, help="write a privacy-safe JSON report")
    parser.add_argument("--label", default="candidate", help="non-sensitive variant label")
    parser.add_argument("--hook", type=Path, default=DEFAULT_HOOK)
    parser.add_argument("--node", default="node")
    parser.add_argument("--url", default="http://127.0.0.1:1933")
    parser.add_argument("--api-key", default="", help="optional API key; never printed")
    parser.add_argument("--account", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--actor-peer", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--compression", choices=("on", "off"), default="off")
    parser.add_argument("--query-expansion", choices=("auto", "off"), default="off")
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument(
        "--variant-env",
        action="append",
        default=[],
        type=parse_variant_env,
        metavar="OPENVIKING_NAME=VALUE",
        help="non-secret recall tuning recorded in the report",
    )
    parser.add_argument("--max-p50-ms", type=float)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--max-false-injection-rate", type=float)
    parser.add_argument("--min-positive-recall-rate", type=float)
    parser.add_argument("--max-injection-p95-tokens", type=float)
    parser.add_argument("--fail-on-case-mismatch", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.repeat <= 0 or args.warmup < 0:
        raise ValueError("--repeat must be positive and --warmup must be non-negative")
    if args.max_tokens <= 0:
        raise ValueError("--max-tokens must be positive")
    for name in ("max_false_injection_rate", "min_positive_recall_rate"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 1:
            raise ValueError(f"--{name.replace('_', '-')} must be between 0 and 1")
    if not args.hook.is_file():
        raise ValueError(f"hook does not exist: {args.hook}")


def main() -> int:
    args = parse_args()
    validate_args(args)
    cases = load_cases(args.input)
    fixture_sha256 = hashlib.sha256(args.input.read_bytes()).hexdigest()
    records: list[dict[str, Any]] = []
    tokenizer = "conservative-cjk"

    with tempfile.TemporaryDirectory(prefix="ov-codex-recall-bench-") as directory:
        env = build_environment(args, Path(directory))
        sequence = 0
        for case_index, case in enumerate(cases):
            for iteration in range(args.warmup + args.repeat):
                session_id = f"codex-benchmark-{fixture_sha256[:12]}-{case_index}-{iteration}"
                context, latency_ms = run_hook(
                    node=args.node,
                    hook=args.hook,
                    query=case["query"],
                    session_id=session_id,
                    env=env,
                    timeout=args.timeout,
                )
                if iteration < args.warmup:
                    continue
                injection_tokens, tokenizer = count_tokens(context)
                injected = bool(context.strip())
                hit = injected
                if case["expected"] == "accept" and case["gold_uris"]:
                    hit = any(uri in context for uri in case["gold_uris"])
                record = {
                    "sequence": sequence,
                    "case_id": case["id"],
                    "expected": case["expected"],
                    "injected": injected,
                    "hit": hit,
                    "passed": (not injected) if case["expected"] == "abstain" else hit,
                    "latency_ms": latency_ms,
                    "injection_tokens": injection_tokens,
                }
                sequence += 1
                records.append(record)
                print(json.dumps(record, ensure_ascii=False, sort_keys=True))

    summary = summarize(records, tokenizer)
    failures = threshold_failures(summary, args)
    report = {
        "schema_version": SCHEMA_VERSION,
        "label": args.label,
        "fixture_sha256": fixture_sha256,
        "protocol": {
            "timeout_ms": round(args.timeout * 1000),
            "repeat": args.repeat,
            "warmup": args.warmup,
        },
        "variant": {
            "compression": args.compression,
            "query_expansion": args.query_expansion,
            "max_tokens": args.max_tokens,
            "extra_env": dict(sorted(args.variant_env)),
        },
        "records": records,
        "summary": summary,
        "threshold_failures": failures,
    }
    print(json.dumps({"summary": summary, "threshold_failures": failures}, sort_keys=True))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if failures:
        return 1
    if args.fail_on_case_mismatch and not all(record["passed"] for record in records):
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"Codex recall benchmark failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
