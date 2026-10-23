"""Measure memory-type contributions with independent, type-scoped AML searches."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import runpy
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence
from urllib.parse import urlsplit

import httpx

from benchmark.aml.eval import evaluate as public_eval
from benchmark.aml.eval.run_public import _bounded_map

TYPES = ("event", "preference", "entity", "profile")
UNITS = ("locomo_refined", "longmemeval_s")
METRICS = ("shapley_pp", "removal_loss_pp", "standalone_gain_pp")
ALIASES = {
    **{name: name for name in TYPES},
    "events": "event",
    "preferences": "preference",
    "entities": "entity",
}


def _models_from_environment() -> dict[str, str]:
    models = {
        stage: os.getenv(stage.upper() + "_MODEL", "").strip() for stage in ("answer", "judge")
    }
    missing = [stage.upper() + "_MODEL" for stage, model in models.items() if not model]
    if missing:
        raise ValueError(f"missing model environment: {', '.join(missing)}")
    return models


def _temperature(value: str) -> float | None:
    if value == "omit":
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 2:
        raise argparse.ArgumentTypeError("temperature must be in [0, 2], or omit")
    return parsed


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def _lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".running"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            f"run is locked: {path}; check its PID before removing a stale lock"
        ) from exc
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def _load_pipeline(repo: Path, unit: str, commit: str) -> tuple[SimpleNamespace, dict]:
    relative_path, _ = public_eval.PIPELINES[unit]
    path, info = public_eval._verify_pipeline(repo, relative_path, commit)
    # The public modules import api_config from their checkout. Keep that import
    # local, including when both datasets are evaluated in one Python process.
    old_path = sys.path[:]
    previous = sys.modules.pop("api_config", None)
    try:
        sys.path.insert(0, str(repo))
        namespace = runpy.run_path(str(path))
    finally:
        sys.path[:] = old_path
        sys.modules.pop("api_config", None)
        if previous is not None:
            sys.modules["api_config"] = previous
    for name in ("render_answer_prompt", "render_accuracy_prompt", "parse_judge_label"):
        if not callable(namespace.get(name)):
            raise ValueError(f"public pipeline has no {name}")
    return SimpleNamespace(**namespace), info


def _type(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("memory types must be nonempty strings")
    return ALIASES.get(value.strip().lower(), "other")


def _memory_types(row: dict) -> list[str]:
    contexts = public_eval._contexts(row)
    declared = row.get("retrieved_memory_types")
    uris = row.get("retrieved_uris")
    for name, values in (("retrieved_memory_types", declared), ("retrieved_uris", uris)):
        if values is not None and (not isinstance(values, list) or len(values) != len(contexts)):
            raise ValueError(f"{name} must align with retrieved_context")
    result = []
    for index, text in enumerate(contexts):
        hints = []
        if declared is not None:
            hints.append(_type(declared[index]))
        if uris is not None:
            uri = uris[index]
            if not isinstance(uri, str) or "/memories/" not in uri:
                raise ValueError("retrieved_uris must contain memory file URIs")
            part = uri.split("/memories/", 1)[1].split("/", 1)[0]
            hints.append(_type(part.removesuffix(".md")))
        metadata = re.search(r"<!--\s*MEMORY_FIELDS\s*(\{.*?\})\s*-->", text, re.DOTALL)
        if metadata:
            fields = json.loads(metadata.group(1))
            if "memory_type" in fields:
                hints.append(_type(fields["memory_type"]))
        if not hints:
            raise ValueError(
                f"record {row['id']!r}, context {index}: no type provenance; provide "
                "retrieved_memory_types, retrieved_uris, or MEMORY_FIELDS.memory_type"
            )
        if len(set(hints)) != 1:
            raise ValueError(f"record {row['id']!r}, context {index}: conflicting memory types")
        result.append(hints[0])
    return result


def _cases(rows: list[dict], group_field: str | None) -> list[dict]:
    cases = []
    for source in rows:
        row = dict(source)
        row["id"] = public_eval._row_id(row)
        # These fields override retrieved_context in the public renderer. Reject
        # them rather than accidentally retaining removed memories or changing
        # a full-condition prompt while normalizing an existing experiment.
        if any(
            key in row
            for key in ("speaker_1_memories", "speaker_2_memories", "memories", "chat_history")
        ):
            raise ValueError(f"record {row['id']!r}: put all Answer memory in retrieved_context")
        if not isinstance(row.get("question"), str) or not row["question"].strip():
            raise ValueError(f"record {row['id']!r}: missing question")
        group = row.get(group_field) if group_field else row.get("user_id", row.get("history_key"))
        if not isinstance(group, (str, int)) or isinstance(group, bool) or not str(group):
            raise ValueError(f"record {row['id']!r}: supply user_id/history_key or --group-field")
        cases.append(
            {
                "row": row,
                "types": _memory_types(row),
                "group": str(group),
                "category": str(row.get("question_type", row.get("category", "unspecified"))),
            }
        )
    return cases


def subset_row(case: dict, mask: int) -> dict:
    """Only remove memory entries; never refill, reorder, or change their text."""
    row = dict(case["row"])
    indices = [
        index
        for index, kind in enumerate(case["types"])
        if kind == "other" or mask & (1 << TYPES.index(kind))
    ]
    for key in ("retrieved_context", "retrieved_uris", "retrieved_memory_types"):
        if key in row:
            row[key] = [row[key][index] for index in indices]
    return row


def _inventory(path: Path, cases: list[dict]) -> list[dict]:
    """Validate a complete, operator-frozen memory inventory, not an old TopK."""
    users = {case["row"].get("user_id") for case in cases}
    if any(not isinstance(user, str) or not re.fullmatch(r"[\w-]+", user) for user in users):
        raise ValueError("retrieval attribution requires explicit, safe user_id values")
    result = []
    seen = set()
    for item in public_eval._read_jsonl(path):
        user, uri, content = (item.get(key) for key in ("user_id", "uri", "content"))
        prefix = f"viking://user/{user}/memories/"
        if (
            user not in users
            or not isinstance(uri, str)
            or not uri.startswith(prefix)
            or not uri.endswith(".md")
            or any(
                part in {"", ".", ".."} or part.startswith(".")
                for part in uri[len(prefix) :].split("/")
            )
            or any(char in uri for char in "?#%")
            or not isinstance(content, str)
            or "<!-- MEMORY_FIELDS" in content
            or uri in seen
        ):
            raise ValueError(
                "inventory needs unique canonical user memory files and visible content"
            )
        kind = _memory_types(
            {
                "id": uri,
                "retrieved_context": [content],
                "retrieved_uris": [uri],
                "retrieved_memory_types": [item.get("memory_type")],
            }
        )[0]
        seen.add(uri)
        result.append({**item, "memory_type": kind})
    if {item["user_id"] for item in result} != users:
        raise ValueError("inventory must cover every case user")
    return result


def _targets(inventory: list[dict], user: str, mask: int, other_types: str) -> list[str]:
    targets = set()
    prefix = f"viking://user/{user}/memories/"
    for item in inventory:
        kind = item["memory_type"]
        if item["user_id"] != user or not (
            (kind == "other" and other_types == "fixed")
            or (kind in TYPES and mask & (1 << TYPES.index(kind)))
        ):
            continue
        # Restrict the vector candidate pool before TopK. Each first-level
        # directory is a type; profile/identity/soul are exact file scopes.
        targets.add(prefix + item["uri"][len(prefix) :].split("/", 1)[0])
    return sorted(targets)


def _retrieval_path(root: Path, mask: int, index: int) -> Path:
    return root / "retrieval" / f"subset-{mask:02d}" / f"{index:06d}.json"


def _checked(path: Path, expected: dict) -> dict:
    record = _json(path)
    checksum = record.pop("sha256", None)
    if checksum != _digest(record) or record.get("job") != expected:
        raise ValueError(f"retrieval integrity mismatch: {path}")
    return record


async def _native_call(client, path, job, method, route, payload, config, key, cooldown):
    request = {"method": method, "route": route, "payload": payload}
    if path.exists():
        record = _checked(path, job)
        if record["request"] != request:
            raise ValueError(f"native request changed: {path}")
        return record["response"]
    # Search/read are read-only. Keep the first HTTP success even if its
    # contents fail validation; a resume must not draw a different TopK.
    previous = sorted(path.parent.glob(path.stem + ".attempt-*.json"))
    for saved in previous:
        record = _checked(saved, job)
        if record["request"] != request:
            raise ValueError(f"native attempt changed: {saved}")
        if record["http_status"] == 200:
            _write(path, {**record, "sha256": _digest(record)})
            return record["response"]
        if record["http_status"] not in {0, 408, 429, 500, 502, 503, 504}:
            raise RuntimeError(f"non-retryable native response preserved: {saved}")
        remaining = record.get("retry_not_before_epoch", 0) - time.time()
        cooldown.until = max(cooldown.until, time.monotonic() + max(0, remaining))
    for attempt in range(len(previous) + 1, 4):
        await cooldown.wait()
        record = {"job": job, "request": request}
        try:
            response = await client.request(
                method,
                config["api_base"] + route,
                headers={
                    "X-API-Key": key,
                    "X-OpenViking-Account": config["account"],
                    "X-OpenViking-User": job["user_id"],
                },
                **({"json": payload} if method == "POST" else {"params": payload}),
            )
            record["http_status"] = response.status_code
            try:
                record["response"] = response.json()
            except ValueError:
                record["response"] = response.text
        except httpx.TransportError as exc:
            response = None
            record.update(http_status=0, error=type(exc).__name__)
        record["recorded_at"] = datetime.now(timezone.utc).isoformat()
        status = record["http_status"]
        if status in {0, 408, 429, 500, 502, 503, 504}:
            cooldown.defer(
                response.headers.get("Retry-After") if response is not None else None, attempt
            )
            record["retry_not_before_epoch"] = time.time() + max(
                0, cooldown.until - time.monotonic()
            )
        record["sha256"] = _digest(record)
        _write(path.with_name(f"{path.stem}.attempt-{attempt:03d}.json"), record)
        if status == 200:
            _write(path, record)
            return record["response"]
        if status not in {0, 408, 429, 500, 502, 503, 504}:
            raise RuntimeError(f"native HTTP {status}; evidence: {path}")
    raise RuntimeError(f"native technical attempts exhausted: {path}")


def _native_result(value):
    if not isinstance(value, dict) or value.get("status") != "ok" or "result" not in value:
        raise ValueError("invalid successful native response preserved; no new Search")
    return value["result"]


async def retrieve(root: Path, manifest: dict, cases: list[dict], client=None) -> None:
    """Freeze all 16 independent searches before spending any Answer calls."""
    config = manifest["retrieval"]
    if all(_retrieval_path(root, m, i).exists() for m in range(16) for i in range(len(cases))):
        for mask in range(16):
            _condition_rows(root, manifest, cases, mask)
        return
    inventory = _json(root / "inventory.json")
    lookup = {item["uri"]: item for item in inventory}
    key = os.getenv("OV_API_KEY", "")
    if not key:
        raise ValueError("set OV_API_KEY for the isolated frozen memory snapshot")
    cooldown = _Cooldown()
    locks = {}

    async def verify_inventory():
        for user in sorted({case["row"]["user_id"] for case in cases}):
            prefix = f"viking://user/{user}/memories/"
            found = set()
            pending = [prefix.rstrip("/")]
            visited = set()
            # Recursive ls has a server-side depth limit. Walk direct children
            # explicitly so dated Events and deeper entity paths are covered.
            while pending:
                directory = pending.pop()
                if directory in visited:
                    raise ValueError("duplicate directory in native inventory listing")
                visited.add(directory)
                offset = 0
                seen_entries = set()
                while True:
                    job = {
                        "manifest_sha256": _digest(manifest),
                        "user_id": user,
                        "listing_directory": directory,
                        "listing_offset": offset,
                    }
                    response = await _native_call(
                        client,
                        root / "inventory-listings" / f"{_digest(directory)}-{offset:08d}.json",
                        job,
                        "GET",
                        "/api/v1/fs/ls",
                        {
                            "uri": directory,
                            "output": "original",
                            "recursive": "false",
                            "include_abstract": "false",
                            "show_all_hidden": "false",
                            "node_limit": 1000,
                            "offset": offset,
                        },
                        config,
                        key,
                        cooldown,
                    )
                    entries = _native_result(response)
                    if not isinstance(entries, list) or type(response.get("has_more")) is not bool:
                        raise ValueError(
                            "native inventory listing must contain entries and has_more"
                        )
                    for entry in entries:
                        uri = entry.get("uri") if isinstance(entry, dict) else None
                        if (
                            not isinstance(uri, str)
                            or not uri.startswith(directory + "/")
                            or "/" in uri[len(directory) + 1 :]
                            or type(entry.get("isDir")) is not bool
                            or uri in seen_entries
                        ):
                            raise ValueError("invalid or repeated native inventory child")
                        seen_entries.add(uri)
                        name = uri[len(directory) + 1 :]
                        if name.startswith("."):
                            continue
                        if entry["isDir"]:
                            pending.append(uri)
                        elif name.endswith(".md"):
                            found.add(uri)
                    if not response["has_more"]:
                        break
                    if not entries:
                        raise ValueError("inventory pagination made no progress")
                    offset += len(entries)
            if found != {item["uri"] for item in inventory if item["user_id"] == user}:
                raise ValueError(
                    f"inventory is not the complete native user memory namespace: {user}"
                )

    async def cell(pair):
        mask, index = pair
        row = cases[index]["row"]
        user = row["user_id"]
        job = {
            "manifest_sha256": _digest(manifest),
            "subset": mask,
            "index": index,
            "user_id": user,
        }
        path = _retrieval_path(root, mask, index)
        if path.exists():
            _checked(path, job)
            return
        targets = _targets(inventory, user, mask, config["other_types"])
        request = {
            "query": row["question"],
            "target_uri": targets,
            "context_type": ["memory"],
            "level": 2,
            "limit": config["top_k"],
            "score_threshold": config["score_threshold"],
        }
        hits = []
        if targets:  # An empty target list expands to unrestricted search in OV.
            result = await _native_call(
                client,
                path.with_suffix(".find.json"),
                job,
                "POST",
                "/api/v1/search/find",
                request,
                config,
                key,
                cooldown,
            )
            result = _native_result(result)
            hits = result.get("memories")
            if not isinstance(hits, list) or len(hits) > config["top_k"]:
                raise ValueError("invalid native TopK preserved")
        if any(not isinstance(hit, dict) for hit in hits):
            raise ValueError("invalid native memory entries preserved")
        uris = [hit.get("uri") for hit in hits]
        allowed = {
            item["uri"]
            for item in inventory
            if item["user_id"] == user
            and (
                (item["memory_type"] == "other" and config["other_types"] == "fixed")
                or (item["memory_type"] in TYPES and mask & (1 << TYPES.index(item["memory_type"])))
            )
        }
        if len(set(uris)) != len(uris) or any(uri not in allowed for uri in uris):
            raise ValueError("native returned duplicate, cross-user, or excluded-type memory")
        for uri in uris:
            async with locks.setdefault(uri, asyncio.Lock()):
                body_job = {"manifest_sha256": _digest(manifest), "user_id": user, "uri": uri}
                body = await _native_call(
                    client,
                    root / "memory-reads" / (_digest(uri) + ".json"),
                    body_job,
                    "GET",
                    "/api/v1/content/read",
                    {"uri": uri, "raw": "false"},
                    config,
                    key,
                    cooldown,
                )
                if _native_result(body) != lookup[uri]["content"]:
                    raise ValueError(f"live body differs from frozen inventory: {uri}")
        result_row = {
            **row,
            "retrieved_uris": uris,
            "retrieved_context": [lookup[uri]["content"] for uri in uris],
            "retrieved_memory_types": [lookup[uri]["memory_type"] for uri in uris],
        }
        record = {
            "job": job,
            "request": request,
            "hits": hits,
            "row": result_row,
            "skipped_empty_scope": not targets,
        }
        _write(path, {**record, "sha256": _digest(record)})

    if client is None:
        async with httpx.AsyncClient(timeout=120) as owned:
            client = owned
            await verify_inventory()
            await _bounded_map(
                [(m, i) for m in range(16) for i in range(len(cases))], config["concurrency"], cell
            )
    else:
        await verify_inventory()
        await _bounded_map(
            [(m, i) for m in range(16) for i in range(len(cases))], config["concurrency"], cell
        )
    print(f"all {16 * len(cases)} scoped retrieval cells frozen", flush=True)


def _condition_rows(root, manifest, cases, mask):
    if manifest["attribution_scope"] == "context":
        return [subset_row(case, mask) for case in cases]
    lookup = {item["uri"]: item for item in _json(root / "inventory.json")}
    rows = []
    for index, case in enumerate(cases):
        user = case["row"]["user_id"]
        job = {
            "manifest_sha256": _digest(manifest),
            "subset": mask,
            "index": index,
            "user_id": user,
        }
        record = _checked(_retrieval_path(root, mask, index), job)
        row = record["row"]
        if len(row["retrieved_uris"]) > manifest["retrieval"]["top_k"]:
            raise ValueError("cached retrieval exceeds TopK")
        expected = []
        kinds = []
        for uri in row["retrieved_uris"]:
            item = lookup.get(uri)
            if item is None or item["user_id"] != user:
                raise ValueError("cached retrieval escaped frozen user inventory")
            kind = item["memory_type"]
            if (kind == "other" and manifest["retrieval"]["other_types"] != "fixed") or (
                kind in TYPES and not mask & (1 << TYPES.index(kind))
            ):
                raise ValueError("cached retrieval includes excluded type")
            expected.append(item["content"])
            kinds.append(kind)
        if len(set(row["retrieved_uris"])) != len(expected) or row != {
            **case["row"],
            "retrieved_uris": row["retrieved_uris"],
            "retrieved_context": expected,
            "retrieved_memory_types": kinds,
        }:
            raise ValueError("cached retrieval changed case fields or memory bodies")
        rows.append(row)
    return rows


def prepare(args: argparse.Namespace) -> dict:
    models = _models_from_environment()
    root = args.output_dir
    if any(path.name != ".running" for path in root.iterdir()):
        raise ValueError("prepare needs an empty output directory; use run to resume")
    rows = public_eval._read_jsonl(args.retrieval)
    unit = public_eval._resolve_unit(rows, args.unit)
    if unit not in UNITS:
        raise ValueError(f"memory contribution only supports {UNITS}")
    pipeline, pipeline_info = _load_pipeline(args.aml_repo.resolve(), unit, args.aml_commit)
    scope = args.attribution_scope
    if scope == "retrieval":
        # Old TopK is provenance only. It must never bound a fresh candidate pool.
        rows = [
            {**row, "retrieved_context": [], "retrieved_uris": [], "retrieved_memory_types": []}
            for row in rows
        ]
    cases = _cases(rows, args.group_field)
    inventory = None
    if scope == "retrieval":
        if args.memory_inventory is None:
            raise ValueError(
                "retrieval attribution needs --memory-inventory for the entire frozen user corpus"
            )
        inventory = _inventory(args.memory_inventory, cases)
        parsed = urlsplit(args.ov_api_base or "")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.query
            or parsed.fragment
            or not args.ov_account
        ):
            raise ValueError(
                "retrieval attribution needs a credential-free --ov-api-base and --ov-account"
            )
    for case in cases:
        # Validate both renderers before spending any model requests, including
        # gold availability. Gold is used by Judge, never by our Answer adapter.
        pipeline.render_answer_prompt(subset_row(case, 15))
        pipeline.render_accuracy_prompt(case["row"], "validation only")
    snapshot = root / "cases.json"
    _write(snapshot, cases)
    manifest = {
        "version": 2,
        "attribution_scope": scope,
        "unit": unit,
        "types": TYPES,
        "rounds": args.rounds,
        "questions": len(cases),
        "history_groups": len({case["group"] for case in cases}),
        "source_retrieval_sha256": _file_sha(args.retrieval),
        "cases_sha256": _file_sha(snapshot),
        "script_sha256": _file_sha(Path(__file__)),
        "pipeline": pipeline_info,
        "models": models,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "token_limit_parameter": args.token_limit_parameter,
        "reasoning_effort": args.reasoning_effort,
        "other_contexts_fixed": sum(case["types"].count("other") for case in cases),
        "memory_coverage": {
            kind: {
                "questions": sum(kind in case["types"] for case in cases),
                "contexts": sum(case["types"].count(kind) for case in cases),
            }
            for kind in (*TYPES, "other")
        },
        "planned_answer_requests": args.rounds * 16 * len(cases),
        "planned_judge_requests": args.rounds * 16 * len(cases),
        "scope": "frozen Answer context diagnostic; other memory types fixed"
        if scope == "context"
        else "independent type-scoped native Find before TopK on one frozen corpus",
    }
    if inventory is not None:
        _write(root / "inventory.json", inventory)
        manifest["inventory_sha256"] = _file_sha(root / "inventory.json")
        manifest["retrieval"] = {
            "api_base": args.ov_api_base.rstrip("/"),
            "account": args.ov_account,
            "top_k": args.top_k or (20 if unit == "locomo_refined" else 100),
            "score_threshold": args.score_threshold,
            "concurrency": args.search_concurrency,
            "other_types": args.other_types,
            "rerank": False,
            "planner": False,
            "links": False,
        }
        manifest["other_contexts_fixed"] = (
            sum(item["memory_type"] == "other" for item in inventory)
            if args.other_types == "fixed"
            else 0
        )
        manifest["memory_coverage"] = {
            kind: {
                "questions": sum(
                    any(
                        item["user_id"] == case["row"]["user_id"] and item["memory_type"] == kind
                        for item in inventory
                    )
                    for case in cases
                ),
                "documents": sum(item["memory_type"] == kind for item in inventory),
            }
            for kind in (*TYPES, "other")
        }
        manifest["planned_search_cells"] = 16 * len(cases)
    _write(root / "manifest.json", manifest)
    return manifest


def _load_plan(root: Path) -> tuple[dict, list[dict]]:
    manifest = _json(root / "manifest.json")
    if manifest.get("version") != 2 or manifest.get("types") != list(TYPES):
        raise ValueError("unsupported contribution manifest")
    if _file_sha(root / "cases.json") != manifest["cases_sha256"]:
        raise ValueError("frozen cases SHA mismatch")
    if _file_sha(Path(__file__)) != manifest["script_sha256"]:
        raise ValueError("script changed since prepare; use the frozen script revision")
    if (
        manifest["attribution_scope"] == "retrieval"
        and _file_sha(root / "inventory.json") != manifest["inventory_sha256"]
    ):
        raise ValueError("frozen memory inventory SHA mismatch")
    return manifest, _json(root / "cases.json")


def _receipt_path(root: Path, repeat: int, mask: int, index: int, stage: str) -> Path:
    return (
        root / f"round-{repeat + 1:02d}" / f"subset-{mask:02d}" / f"{index:06d}" / f"{stage}.json"
    )


def _job(manifest: dict, repeat: int, mask: int, index: int, stage: str) -> dict:
    return {
        "manifest_sha256": _digest(manifest),
        "round": repeat,
        "subset": mask,
        "index": index,
        "stage": stage,
    }


def _receipt(path: Path, job: dict) -> dict:
    record = _json(path)
    checksum = record.pop("sha256", None)
    if checksum != _digest(record) or record.get("job") != job:
        raise ValueError(f"receipt integrity mismatch: {path}")
    if record.get("request_sha256") != _digest(record.get("request")):
        raise ValueError(f"receipt request mismatch: {path}")
    if not isinstance(record.get("text"), str) or not record["text"].strip():
        raise ValueError(f"receipt has no valid text: {path}")
    if job["stage"] == "judge" and type(record.get("is_correct")) is not bool:
        raise ValueError(f"receipt has no valid verdict: {path}")
    return record


class _Cooldown:
    def __init__(self):
        self.until = 0.0

    async def wait(self):
        while self.until > time.monotonic():
            await asyncio.sleep(min(self.until - time.monotonic(), 30))

    def defer(self, header: str | None, attempt: int):
        try:
            delay = float(header) if header else 2**attempt
        except ValueError:
            try:
                delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError):
                delay = 2**attempt
        self.until = max(self.until, time.monotonic() + max(0, delay))


async def _complete(
    client: httpx.AsyncClient,
    path: Path,
    job: dict,
    request: dict,
    *,
    endpoint: str,
    key: str,
    pipeline: SimpleNamespace,
    cooldown: _Cooldown,
    attempts: int,
) -> dict:
    if path.exists():
        record = _receipt(path, job)
        if record["request_sha256"] != _digest(request):
            raise ValueError(f"cached request differs: {path}")
        return record

    def accept(record: dict) -> dict:
        if record["job"] != job or record["request_sha256"] != _digest(request):
            raise ValueError(f"attempt belongs to a different request: {path}")
        try:
            choice = record["response"]["choices"][0]
            text = choice["message"]["content"]
            if choice["finish_reason"] != "stop" or not isinstance(text, str) or not text.strip():
                raise ValueError("empty or incomplete model response")
            record["text"] = text.strip()
            if job["stage"] == "judge":
                record["is_correct"] = pipeline.parse_judge_label(text) == "CORRECT"
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid successful response preserved at {path.parent}; no resampling: {exc}"
            ) from exc
        record["sha256"] = _digest(record)
        _write(path, record)
        return _receipt(path, job)

    previous = sorted(path.parent.glob(f"{path.stem}.attempt-*.json"))
    for saved in previous:
        record = _json(saved)
        if record.get("job") != job or record.get("request_sha256") != _digest(request):
            raise ValueError(f"attempt request mismatch: {saved}")
        if 200 <= record.get("http_status", 0) < 300:
            return accept(record)
        if record.get("http_status", 0) not in {0, 408, 429, 500, 502, 503, 504}:
            raise RuntimeError(f"non-retryable response preserved at {saved}")
        remaining = record.get("retry_not_before_epoch", 0) - time.time()
        if remaining > 0:
            cooldown.until = max(cooldown.until, time.monotonic() + remaining)
    for attempt in range(len(previous) + 1, attempts + 1):
        await cooldown.wait()
        record = {"job": job, "request": request, "request_sha256": _digest(request)}
        try:
            response = await client.post(
                endpoint + "/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=request,
            )
            record["http_status"] = response.status_code
            try:
                record["response"] = response.json()
            except ValueError:
                record["response"] = response.text
        except httpx.TransportError as exc:
            record.update(http_status=0, error=type(exc).__name__)
            response = None
        record["recorded_at"] = datetime.now(timezone.utc).isoformat()
        status = record["http_status"]
        if status in {0, 408, 429, 500, 502, 503, 504}:
            cooldown.defer(
                response.headers.get("Retry-After") if response is not None else None, attempt
            )
            record["retry_not_before_epoch"] = time.time() + max(
                0, cooldown.until - time.monotonic()
            )
        attempt_path = path.with_name(f"{path.stem}.attempt-{attempt:03d}.json")
        _write(attempt_path, record)
        if 200 <= status < 300:
            return accept(record)
        if status not in {0, 408, 429, 500, 502, 503, 504}:
            raise RuntimeError(f"HTTP {status}; evidence: {attempt_path}")
    raise RuntimeError(f"technical attempts exhausted; evidence: {path.parent}")


async def run(
    root: Path,
    repo: Path,
    *,
    answer_concurrency: int = 48,
    judge_concurrency: int = 48,
    attempts: int = 3,
    timeout: float = 120,
    client: httpx.AsyncClient | None = None,
    retrieval_client: httpx.AsyncClient | None = None,
) -> None:
    manifest, cases = _load_plan(root)
    if _models_from_environment() != manifest["models"]:
        raise ValueError(
            "ANSWER_MODEL/JUDGE_MODEL differ from the frozen plan; prepare a new output directory to change models"
        )
    pipeline, info = _load_pipeline(
        repo.resolve(), manifest["unit"], manifest["pipeline"]["commit"]
    )
    if info != manifest["pipeline"]:
        raise ValueError("public pipeline changed since prepare")
    endpoints = {}
    keys = {}
    for stage in ("answer", "judge"):
        prefix = stage.upper()
        base = os.environ.get(prefix + "_API_BASE", "").rstrip("/")
        parsed = urlsplit(base)
        key = os.environ.get(prefix + "_API_KEY", "")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.query
            or parsed.fragment
            or not key
        ):
            raise ValueError(
                f"set {prefix}_API_BASE and {prefix}_API_KEY (credentials belong in the key environment variable)"
            )
        endpoints[stage] = base
        keys[stage] = key
    runtime = {
        "endpoints": endpoints,
        "answer_concurrency": answer_concurrency,
        "judge_concurrency": judge_concurrency,
        "attempts": attempts,
        "timeout": timeout,
    }
    runtime_path = root / "runtime.json"
    if runtime_path.exists() and _json(runtime_path) != runtime:
        raise ValueError(
            "runtime settings changed; resume with the original endpoints and settings"
        )
    _write(runtime_path, runtime)
    if manifest["attribution_scope"] == "retrieval":
        await retrieve(root, manifest, cases, retrieval_client)
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as owned:
            await _run_cells(root, manifest, cases, pipeline, owned, runtime, keys)
    else:
        await _run_cells(root, manifest, cases, pipeline, client, runtime, keys)


async def _run_cells(root, manifest, cases, pipeline, client, runtime, keys):
    cooldowns = {stage: _Cooldown() for stage in ("answer", "judge")}
    for repeat in range(manifest["rounds"]):
        masks = list(range(16))
        random.Random(repeat).shuffle(masks)
        for mask in masks:
            rows = _condition_rows(root, manifest, cases, mask)
            for stage in ("answer", "judge"):

                async def invoke(index, *, rows=rows, stage=stage, repeat=repeat, mask=mask):
                    row = rows[index]
                    prompt = pipeline.render_answer_prompt(row)
                    if stage == "judge":
                        answer = _receipt(
                            _receipt_path(root, repeat, mask, index, "answer"),
                            _job(manifest, repeat, mask, index, "answer"),
                        )
                        prompt = pipeline.render_accuracy_prompt(row, answer["text"])
                    request = {
                        "model": manifest["models"][stage],
                        "messages": [{"role": "user", "content": prompt}],
                        manifest["token_limit_parameter"]: manifest["max_tokens"],
                    }
                    if manifest["temperature"] is not None:
                        request["temperature"] = manifest["temperature"]
                    if manifest["reasoning_effort"] is not None:
                        request["reasoning_effort"] = manifest["reasoning_effort"]
                    return await _complete(
                        client,
                        _receipt_path(root, repeat, mask, index, stage),
                        _job(manifest, repeat, mask, index, stage),
                        request,
                        endpoint=runtime["endpoints"][stage],
                        key=keys[stage],
                        pipeline=pipeline,
                        cooldown=cooldowns[stage],
                        attempts=runtime["attempts"],
                    )

                await _bounded_map(list(range(len(cases))), runtime[stage + "_concurrency"], invoke)
            print(
                f"round {repeat + 1}/{manifest['rounds']} subset {mask:02d}: {len(cases)} Answer/Judge pairs complete",
                flush=True,
            )


def contributions(values: Sequence[float]) -> dict[str, dict[str, float]]:
    """Exact four-player Shapley values; all reported effects use percentage points."""
    if len(values) != 16 or any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in values
    ):
        raise ValueError("need 16 finite subset accuracies in [0, 1]")
    result = {}
    for index, kind in enumerate(TYPES):
        bit = 1 << index
        shapley = 0.0
        for mask in range(16):
            if mask & bit:
                continue
            size = mask.bit_count()
            weight = math.factorial(size) * math.factorial(3 - size) / math.factorial(4)
            shapley += weight * (values[mask | bit] - values[mask])
        result[kind] = {
            "shapley_pp": 100 * shapley,
            "removal_loss_pp": 100 * (values[15] - values[15 ^ bit]),
            "standalone_gain_pp": 100 * (values[bit] - values[0]),
        }
    return result


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = math.floor(position)
    right = math.ceil(position)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def _aggregate(records: list[dict], samples: int, seed: int) -> dict:
    repeats = len(records[0]["round_values"])
    mean_values = [
        sum(row["round_values"][repeat][mask] for row in records for repeat in range(repeats))
        / (len(records) * repeats)
        for mask in range(16)
    ]
    effects = contributions(mean_values)
    for bit, kind in enumerate(TYPES):
        removed = 15 ^ (1 << bit)
        effects[kind].update(
            helped_question_rounds=sum(
                v[15] == 1 and v[removed] == 0 for row in records for v in row["round_values"]
            ),
            harmed_question_rounds=sum(
                v[15] == 0 and v[removed] == 1 for row in records for v in row["round_values"]
            ),
            helped_questions=sum(
                any(v[15] == 1 and v[removed] == 0 for v in row["round_values"]) for row in records
            ),
            harmed_questions=sum(
                any(v[15] == 0 and v[removed] == 1 for v in row["round_values"]) for row in records
            ),
            standalone_correct_question_rounds=sum(
                v[1 << bit] for row in records for v in row["round_values"]
            ),
        )
    # Resample entire histories, retaining their unequal question counts, and
    # paired round indices. Never independently resample the 16 conditions.
    groups = {}
    for row in records:
        group = groups.setdefault(
            row["group_id"], {"count": 0, "effects": [[0.0] * 12 for _ in range(repeats)]}
        )
        group["count"] += 1
        for repeat, values in enumerate(row["round_values"]):
            effect = contributions(values)
            for index, (kind, metric) in enumerate(
                (kind, metric) for kind in TYPES for metric in METRICS
            ):
                group["effects"][repeat][index] += effect[kind][metric]
    population = list(groups.values())
    bootstrap = [[] for _ in range(12)]
    rng = random.Random(seed)
    for _ in range(samples):
        selected = rng.choices(population, k=len(population))
        selected_rounds = rng.choices(range(repeats), k=repeats)
        denominator = sum(group["count"] for group in selected) * repeats
        totals = [0.0] * 12
        for group in selected:
            for repeat in selected_rounds:
                for index, value in enumerate(group["effects"][repeat]):
                    totals[index] += value
        for index, value in enumerate(totals):
            bootstrap[index].append(value / denominator)
    for index, (kind, metric) in enumerate((kind, metric) for kind in TYPES for metric in METRICS):
        effects[kind][metric + "_ci95"] = [
            _quantile(bootstrap[index], 0.025),
            _quantile(bootstrap[index], 0.975),
        ]
    return {
        "questions": len(records),
        "history_groups": len(groups),
        "subset_accuracies": mean_values,
        "full_accuracy": mean_values[15],
        "without_four_types_accuracy": mean_values[0],
        "total_gain_pp": 100 * (mean_values[15] - mean_values[0]),
        "contributions": effects,
        "full_accuracy_by_round": [
            sum(row["round_values"][repeat][15] for row in records) / len(records)
            for repeat in range(repeats)
        ],
    }


def report(root: Path, *, bootstrap_samples: int = 1000, seed: int = 0) -> dict:
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")
    manifest, cases = _load_plan(root)
    condition_rows = [_condition_rows(root, manifest, cases, mask) for mask in range(16)]
    records = []
    for index, case in enumerate(cases):
        values = []
        for repeat in range(manifest["rounds"]):
            scores = []
            for mask in range(16):
                for stage in ("answer", "judge"):
                    path = _receipt_path(root, repeat, mask, index, stage)
                    if not path.exists():
                        raise ValueError(f"incomplete evaluation: missing {path}")
                    receipt = _receipt(path, _job(manifest, repeat, mask, index, stage))
                scores.append(int(receipt["is_correct"]))
            values.append(scores)
        effects = contributions(
            [sum(row[mask] for row in values) / len(values) for mask in range(16)]
        )
        records.append(
            {
                "id": case["row"]["id"],
                "question": case["row"]["question"],
                "gold": {
                    k: v
                    for k, v in case["row"].items()
                    if k in {"gold_answer", "golden_answer", "reference_answer", "correct_answer"}
                },
                "index": index,
                "group_id": case["group"],
                "category": case["category"],
                "round_values": values,
                "contributions": effects,
                "full_vs_removed": {
                    kind: {
                        "helped_rounds": sum(
                            row[15] == 1 and row[15 ^ (1 << bit)] == 0 for row in values
                        ),
                        "harmed_rounds": sum(
                            row[15] == 0 and row[15 ^ (1 << bit)] == 1 for row in values
                        ),
                    }
                    for bit, kind in enumerate(TYPES)
                },
            }
        )
    summary = {
        "unit": manifest["unit"],
        "rounds": manifest["rounds"],
        "types": TYPES,
        "attribution_scope": manifest["attribution_scope"],
        "scope": manifest["scope"],
        "models": manifest["models"],
        "retrieval": manifest.get("retrieval"),
        "manifest_sha256": _digest(manifest),
        "bootstrap_samples": bootstrap_samples,
        "seed": seed,
        "ci_method": "paired history-cluster and round percentile bootstrap",
        "other_contexts_fixed": manifest["other_contexts_fixed"],
        "memory_coverage": manifest["memory_coverage"],
        "overall": _aggregate(records, bootstrap_samples, seed),
        "by_category": {
            category: _aggregate(
                [row for row in records if row["category"] == category], bootstrap_samples, seed
            )
            for category in sorted({row["category"] for row in records})
        },
    }
    _write(root / "per-question.json", records)
    opportunities = []
    for index, record in enumerate(records):
        for bit, kind in enumerate(TYPES):
            without = 15 ^ (1 << bit)
            for repeat, values in enumerate(record["round_values"]):
                if values[15] == values[without]:
                    continue
                full, removed = (condition_rows[mask][index] for mask in (15, without))
                opportunities.append(
                    {
                        "id": record["id"],
                        "question": record["question"],
                        "type": kind,
                        "round": repeat + 1,
                        "transition": "helped" if values[15] else "harmed",
                        "full_answer": _receipt(
                            _receipt_path(root, repeat, 15, index, "answer"),
                            _job(manifest, repeat, 15, index, "answer"),
                        )["text"],
                        "removed_answer": _receipt(
                            _receipt_path(root, repeat, without, index, "answer"),
                            _job(manifest, repeat, without, index, "answer"),
                        )["text"],
                        "full_uris": full.get("retrieved_uris"),
                        "removed_uris": removed.get("retrieved_uris"),
                        "evidence": {
                            "cases": "cases.json",
                            "full_retrieval": str(_retrieval_path(root, 15, index))
                            if manifest["attribution_scope"] == "retrieval"
                            else None,
                            "removed_retrieval": str(_retrieval_path(root, without, index))
                            if manifest["attribution_scope"] == "retrieval"
                            else None,
                        },
                        "diagnosis": "unreviewed; inspect source, persistent memories, retrieval and answer before attributing a failure stage",
                    }
                )
    _write(root / "opportunities.json", opportunities)
    _write(root / "summary.json", summary)
    lines = [
        f"# {manifest['unit']} memory contribution",
        "",
        manifest["scope"]
        + ". Effects are percentage points; helpful/harmful counts are paired question-rounds.",
        "",
        "| Type | Helped | Harmed | Shapley pp (95% CI) | Removal loss pp | Standalone gain pp |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for kind, effect in summary["overall"]["contributions"].items():
        low, high = effect["shapley_pp_ci95"]
        lines.append(
            f"| {kind} | {effect['helped_question_rounds']} | {effect['harmed_question_rounds']} | {effect['shapley_pp']:.2f} [{low:.2f}, {high:.2f}] | {effect['removal_loss_pp']:.2f} | {effect['standalone_gain_pp']:.2f} |"
        )
    lines += [
        "",
        "## Cases to inspect",
        "",
        "Effects include candidate competition and context changes. A transition alone does not prove extraction or retrieval failure.",
        "",
    ]
    for item in opportunities:
        lines += [
            f"- {item['id']} / {item['type']} / round {item['round']} / {item['transition']}: {item['question']}",
            f"  Full: {item['full_answer']!r}; removed: {item['removed_answer']!r}. See opportunities.json and the linked retrieval/Answer receipts.",
        ]
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser(
        "prepare", help="freeze inputs and print request counts; no model calls"
    )
    prepare_parser.add_argument("--retrieval", type=Path, required=True)
    prepare_parser.add_argument(
        "--attribution-scope", choices=("retrieval", "context"), default="retrieval"
    )
    prepare_parser.add_argument(
        "--memory-inventory",
        type=Path,
        help="complete frozen user memory JSONL, id=URI, with user_id/uri/memory_type/content",
    )
    prepare_parser.add_argument(
        "--ov-api-base", help="native OV URL serving an isolated immutable snapshot"
    )
    prepare_parser.add_argument("--ov-account")
    prepare_parser.add_argument("--top-k", type=public_eval._positive_int)
    prepare_parser.add_argument("--score-threshold", type=float, default=0)
    prepare_parser.add_argument("--search-concurrency", type=public_eval._positive_int, default=4)
    prepare_parser.add_argument("--other-types", choices=("exclude", "fixed"), default="exclude")
    prepare_parser.add_argument("--unit", choices=UNITS)
    prepare_parser.add_argument("--aml-repo", type=Path, required=True)
    prepare_parser.add_argument("--aml-commit", default=public_eval.DEFAULT_AML_COMMIT)
    prepare_parser.add_argument(
        "--group-field", help="history-cluster field; defaults to user_id then history_key"
    )
    prepare_parser.add_argument("--rounds", type=public_eval._positive_int, default=5)
    prepare_parser.add_argument(
        "--temperature",
        type=_temperature,
        default=0,
        help="sampling temperature or 'omit' for the provider default",
    )
    prepare_parser.add_argument("--max-tokens", type=public_eval._positive_int, default=16384)
    prepare_parser.add_argument(
        "--token-limit-parameter",
        choices=("max_tokens", "max_completion_tokens"),
        default="max_tokens",
    )
    prepare_parser.add_argument(
        "--reasoning-effort", choices=("none", "minimal", "low", "medium", "high", "xhigh")
    )
    run_parser = commands.add_parser("run", help="run/resume the frozen 16 subsets")
    retrieve_parser = commands.add_parser(
        "retrieve", help="freeze/resume all scoped searches, without Answer/Judge"
    )
    run_parser.add_argument("--aml-repo", type=Path, required=True)
    run_parser.add_argument("--answer-concurrency", type=public_eval._positive_int, default=48)
    run_parser.add_argument("--judge-concurrency", type=public_eval._positive_int, default=48)
    run_parser.add_argument("--attempts", type=public_eval._positive_int, default=3)
    run_parser.add_argument("--timeout", type=public_eval._positive_int, default=120)
    report_parser = commands.add_parser(
        "report", help="verify complete receipts and compute contributions offline"
    )
    report_parser.add_argument("--bootstrap-samples", type=public_eval._positive_int, default=1000)
    report_parser.add_argument("--seed", type=int, default=0)
    for subparser in (prepare_parser, retrieve_parser, run_parser, report_parser):
        subparser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir = args.output_dir.expanduser().resolve()
    try:
        with _lock(args.output_dir):
            if args.command == "prepare":
                summary = prepare(args)
            elif args.command == "retrieve":
                manifest, cases = _load_plan(args.output_dir)
                if manifest["attribution_scope"] != "retrieval":
                    raise ValueError("retrieve requires retrieval attribution scope")
                asyncio.run(retrieve(args.output_dir, manifest, cases))
                summary = {"retrieval_complete": True, "cells": 16 * len(cases)}
            elif args.command == "run":
                asyncio.run(
                    run(
                        args.output_dir,
                        args.aml_repo,
                        answer_concurrency=args.answer_concurrency,
                        judge_concurrency=args.judge_concurrency,
                        attempts=args.attempts,
                        timeout=args.timeout,
                    )
                )
                summary = report(args.output_dir)
            else:
                summary = report(
                    args.output_dir, bootstrap_samples=args.bootstrap_samples, seed=args.seed
                )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
