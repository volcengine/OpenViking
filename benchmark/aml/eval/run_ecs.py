#!/usr/bin/env python3
"""Run local AML smoke/full datasets against a public Add/Search provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
if __package__ in {None, ""}:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.aml.eval import run_public as runner  # noqa: E402

DEFAULT_BASE_URL = runner.DEFAULT_BASE_URL
DEFAULT_DATA_ROOT = REPO_ROOT / "result" / "aml_data"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "result" / "aml_ecs"
DEFAULT_KEY_FILE = REPO_ROOT / ".codex" / "ops" / "aml-api-key"

# Every preset selects the named unit, even when a smoke file contains others.
DATASETS = {
    "longmemeval_s": "lme_locomo_script.json",
    "locomo_refined": "lme_locomo_script.json",
    "personamem_v2_32k": "persona_cl.json",
    "clbench_0_4k": "persona_cl.json",
    "clbench_16_32k": "persona_cl.json",
    "beam_100k": "beam.json",
    "beam_1m": "beam.json",
}
UNAVAILABLE = {
    "longmemeval_refined": "AML has not published the transformed production data",
    "angry": "ScriptMem has no published script history",
    "enemy": "ScriptMem has no published script history",
    "friends": "ScriptMem has no published script history",
    "man_earth": "ScriptMem has no published script history",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _select_datasets(names: Sequence[str]) -> list[str]:
    if not names:
        raise ValueError("specify a dataset or 'all'; use 'list' to see every command")
    if "all" in names:
        if len(names) != 1:
            raise ValueError("'all' cannot be combined with individual datasets")
        return list(DATASETS)
    for name in names:
        if name in UNAVAILABLE:
            raise ValueError(f"{name} is unavailable: {UNAVAILABLE[name]}")
        if name not in DATASETS:
            raise ValueError(f"unknown dataset {name!r}; use 'list' to see supported datasets")
    return list(dict.fromkeys(names))


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an http:// or https:// provider address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, a query, or a fragment")
    if parsed.port == 0:
        raise ValueError("base URL port must be at least 1")
    return value.rstrip("/")


def _api_key(path: Path) -> str:
    value = os.getenv("AML_API_KEY", "").strip()
    if not value and path.is_file():
        value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("set AML_API_KEY or provide --api-key-file; see benchmark/aml/README.md")
    if any(character.isspace() for character in value):
        raise ValueError("AML API key must not contain whitespace")
    return value


def _write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    units = _select_datasets(args.datasets)
    base_url = _base_url(args.base_url)
    run_id = f"{args.mode}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}"
    output = (args.output_dir or DEFAULT_OUTPUT_ROOT / run_id).expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"output directory must be new or empty: {output}")

    sources: dict[Path, list[str]] = {}
    for unit in units:
        relative = Path("full") / f"{unit}.json" if args.mode == "full" else Path(DATASETS[unit])
        source = (args.data_root.expanduser() / "prepared" / relative).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"prepared data is missing: {source}; check --data-root")
        sources.setdefault(source, []).append(unit)

    counts = {}
    for source, selected in sources.items():
        # Load one input at a time for the offline plan, including validation of
        # history references and exactly the same message splitting as execution.
        loaded = runner._load_cases([source], selected)
        for unit, cases in loaded.items():
            groups, searches = runner._prepare_unit(unit, cases, f"ecs-{run_id}")
            counts[unit] = {
                "unit": unit,
                "prepared": str(source),
                "histories": len({case.user_id for case in searches}),
                "sessions": len(groups),
                "add_requests": sum(len(group) for group in groups),
                "search_requests": len(searches),
            }
            del groups, searches
        del loaded

    default_search_concurrency = 4 if args.mode == "smoke" else 16
    return {
        "status": "planned",
        "scope": "local public-source Add/Search; no Answer/Judge or official AML score",
        "run_id": run_id,
        "namespace": f"ecs-{run_id}",
        "mode": args.mode,
        "base_url": base_url,
        "output_dir": str(output),
        "prepared": [str(path) for path in sources],
        "units": [counts[unit] for unit in units],
        # Add holds the request open through commit extraction, including queue wait.
        "add_concurrency": args.add_concurrency or 16,
        "search_concurrency": args.search_concurrency or default_search_concurrency,
        "timeout_seconds": args.timeout_seconds,
    }


async def execute(plan: dict[str, Any], api_key: str) -> dict[str, Any]:
    output = Path(plan["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    report = dict(plan, status="running", started_at=_now())
    report_path = output / "run.json"
    # Reserve this destination before sending any requests; never overwrite an
    # earlier invocation, including one that stopped after a partial import.
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    start = time.monotonic()
    totals = {
        "/add": sum(unit["add_requests"] for unit in plan["units"]),
        "/search": sum(unit["search_requests"] for unit in plan["units"]),
    }
    completed = dict.fromkeys(totals, 0)
    last_progress = dict.fromkeys(totals, start)

    async def progress(response: httpx.Response) -> None:
        route = "/" + response.request.url.path.rsplit("/", 1)[-1]
        if route not in totals or response.status_code != 200:
            return
        completed[route] += 1
        now = time.monotonic()
        count = completed[route]
        if count in {1, totals[route]} or count % 25 == 0 or now - last_progress[route] >= 30:
            print(f"  {route} HTTP 200 responses: {count}/{totals[route]}", flush=True)
            last_progress[route] = now

    try:
        async with httpx.AsyncClient(
            timeout=plan["timeout_seconds"], event_hooks={"response": [progress]}
        ) as client:
            response = await client.get(plan["base_url"] + "/health", timeout=15)
            if response.status_code != 200:
                raise RuntimeError(f"provider /health returned HTTP {response.status_code}")
            print("Provider /health: HTTP 200", flush=True)
            result = await runner.run_public(
                plan["prepared"],
                output_dir=output / "retrieval",
                namespace=plan["namespace"],
                units=[item["unit"] for item in plan["units"]],
                base_url=plan["base_url"],
                api_key=api_key,
                add_concurrency=plan["add_concurrency"],
                search_concurrency=plan["search_concurrency"],
                timeout_seconds=plan["timeout_seconds"],
                client=client,
            )
        report.update(status="completed", success=True, result=result)
    except (Exception, asyncio.CancelledError) as exc:
        report.update(
            status="interrupted" if isinstance(exc, asyncio.CancelledError) else "failed",
            success=False,
            error=f"{type(exc).__name__}: {exc}".replace(api_key, "[REDACTED]"),
        )
        raise
    finally:
        report.update(
            finished_at=_now(),
            elapsed_seconds=round(time.monotonic() - start, 3),
            http_200_responses=completed,
        )
        _write_report(report_path, report)
    return report


def _print_commands() -> None:
    script = str(Path(__file__).resolve())
    print("Local public-source presets (Add/Search only):")
    for unit in DATASETS:
        for mode in ("smoke", "full"):
            print(shlex.join([sys.executable, script, mode, unit]))
    print("\nAll available datasets:")
    for mode in ("smoke", "full"):
        print(shlex.join([sys.executable, script, mode, "all"]))
    print("\nUnavailable data:")
    for unit, reason in UNAVAILABLE.items():
        print(f"  {unit}: {reason}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("list", "smoke", "full"))
    parser.add_argument("datasets", nargs="*", help="dataset names, or all")
    parser.add_argument("--base-url", default=os.getenv("AML_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--api-key-file", type=Path, default=DEFAULT_KEY_FILE)
    parser.add_argument(
        "--add-concurrency",
        type=runner._positive_int,
        help="parallel histories/users; sessions within each history run in prepared order",
    )
    parser.add_argument("--search-concurrency", type=runner._positive_int)
    parser.add_argument(
        "--timeout-seconds", type=runner._positive_float, default=runner.DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and show plan, no network")
    args = parser.parse_args(argv)
    if args.mode == "list":
        if args.datasets:
            parser.error("list does not accept dataset names")
        _print_commands()
        return 0

    api_key = ""
    try:
        plan = build_plan(args)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        api_key = _api_key(args.api_key_file.expanduser())
        print(f"Run: {plan['run_id']} -> {plan['base_url']}", flush=True)
        for unit in plan["units"]:
            print(
                f"  {unit['unit']}: {unit['add_requests']} Add / {unit['search_requests']} Search",
                flush=True,
            )
        print(f"Output: {plan['output_dir']}", flush=True)
        result = asyncio.run(execute(plan, api_key))
    except KeyboardInterrupt:
        print(
            "Interrupted. Remote writes may already exist; the next run uses new users.",
            file=sys.stderr,
        )
        return 130
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        message = f"{type(exc).__name__}: {exc}"
        parser.exit(1, (message.replace(api_key, "[REDACTED]") if api_key else message) + "\n")
    print(f"Completed in {result['elapsed_seconds']}s: {plan['output_dir']}/run.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
