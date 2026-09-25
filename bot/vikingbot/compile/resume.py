"""Local checkpoint recovery after all default-pipeline Reduce groups finish."""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path

from vikingbot.compile import file_ops
from vikingbot.compile.ops import finalize as finalize_op
from vikingbot.compile.ops import reduce as reduce_op
from vikingbot.compile.pipeline_io import ROOT
from vikingbot.compile.plan import Contract, Record, content_hash


def read_checkpoint(workspace: Path) -> dict:
    """Validate local shards without writes or model calls; reject incomplete Reduce.

    Recovery supports the four-stage resource pipeline before publication. Returned
    candidates exclude resolved artifacts; resolution is replayed through the validated model cache.
    """
    root = workspace / ROOT

    def read(name):
        return json.loads((root / f"{name}.json").read_text())

    def shards(name):
        return {p.stem: json.loads(p.read_text()) for p in sorted((root / name).glob("*.json"))}

    plan = read("plan")
    if [n["op"] for n in plan["nodes"]] != ["map", "shuffle", "reduce", "finalize"]:
        raise ValueError("Recovery requires the four-stage pipeline")
    if (root / "finalize.json").exists():
        raise ValueError("Recovery requires an unpublished candidate-merge checkpoint")
    groups = shards("groups")
    if not groups:
        raise ValueError("No completed Reduce groups")
    output_counts = {}
    for key in groups:
        job = read(f"jobs/{plan['nodes'][2]['name']}-{key}")
        if job["status"] != "completed":
            raise ValueError(f"Reduce group is incomplete: {key}")
        output_counts[key] = job["output_count"]
    sources, records = shards("sources"), shards("records")
    for key, source in sources.items():
        if content_hash(source["text"]) != source["hash"]:
            raise ValueError(f"Source hash mismatch: {key}")
    candidates, completed = defaultdict(list), set()
    artifact_counts = Counter()
    for key, artifact in shards("artifacts").items():
        if content_hash(artifact["content"]) != artifact["sha256"]:
            raise ValueError(f"Artifact hash mismatch: {key}")
        file_ops.validate_relative_file_path(artifact["path"])
        if not artifact["inputs"] or not set(artifact["inputs"]) <= records.keys():
            raise ValueError(f"Artifact inputs are unavailable: {key}")
        expected = {ref for i in artifact["inputs"] for ref in records[i]["source_refs"]}
        if set(artifact["source_refs"]) != expected or not expected <= sources.keys():
            raise ValueError(f"Artifact evidence is unavailable: {key}")
        reference = f"artifacts/{key}"
        if artifact["owner"].startswith("merge-"):
            completed.add(artifact["owner"])
            continue  # Re-resolve original candidates together so renamed paths stay unique.
        group = groups.get(artifact["owner"])
        if group is None or not set(artifact["inputs"]) <= set(group["records"]):
            raise ValueError(f"Artifact does not belong to a completed group: {key}")
        candidates[artifact["path"]].append((reference, artifact))
        artifact_counts[artifact["owner"]] += 1
    if any(artifact_counts[key] != count for key, count in output_counts.items()):
        raise ValueError("Reduce artifact count does not match completed jobs")
    if not candidates:
        raise ValueError("No Reduce candidates")
    return {
        "contract": read("contract"),
        "runtime": read("runtime"),
        "sources": sources,
        "records": records,
        "candidates": dict(candidates),
        "completed": sorted(completed),
        "summary": read("summary") if (root / "summary.json").exists() else None,
    }


async def run(pipeline):
    """Resume only candidate synthesis and final Finalize in a copied local workspace.

    The caller owns task status and publication. Source errors survive recovery;
    cancellation writes a resumable summary and never triggers publication here.
    """
    checkpoint = await asyncio.to_thread(read_checkpoint, pipeline.files.sandbox.workspace)
    summary = checkpoint["summary"]
    if summary is None or summary.get("committed"):
        raise ValueError("A stopped, unpublished task summary is required")
    contract, runtime = checkpoint["contract"], checkpoint["runtime"]
    if runtime.get("wiki_links", False) != pipeline.request.wiki_links:
        raise ValueError("Wiki link setting differs from the checkpoint")
    if pipeline.skill_target:
        raise ValueError("Local recovery supports resource outputs only")
    if pipeline.skill != contract["skill"] or pipeline.request.skill != runtime["skill"]:
        raise ValueError("Skill differs from the checkpoint")
    if pipeline.model.identity != runtime["model_settings"]:
        raise ValueError("Model settings differ from the checkpoint")
    dependencies = await pipeline.files.get("skill-dependencies") or contract["dependencies"]
    if not await pipeline.resources.valid(dependencies):
        raise ValueError("Skill dependencies differ from the checkpoint")
    pipeline.contract = Contract.model_validate(contract["contract"])
    prompt_runtime = {key: value for key, value in runtime.items() if key in {"time", "skill"}}
    pipeline.system = (
        "Original Skill (authoritative):\n"
        + pipeline.skill
        + "\nInstruction:\n"
        + pipeline.request.instruction
        + "\nShared requirements:\n"
        + pipeline.contract.model_dump_json(include={"preserve", "required_paths"})
        + "\n"
        + pipeline.output_instructions
        + "\nRuntime: "
        + json.dumps(prompt_runtime)
    )
    for key, source in checkpoint["sources"].items():
        pipeline.evidence[key] = {k: v for k, v in source.items() if k not in {"text", "context"}}
        pipeline.register(Record(key, f"sources/{key}", [key], source["uri"], {}, []))
    for record in checkpoint["records"].values():
        pipeline.register(Record(**record))
    if not pipeline.records.keys() <= summary["states"].keys():
        raise ValueError("Checkpoint has incomplete record states")
    pipeline.status.update(summary["states"])
    pipeline.failures.extend(e for e in summary["errors"] if e != "CancelledError")
    pipeline.warnings.extend(summary["warnings"])
    pipeline.metrics.update(summary["metrics"])
    for field, suffix in [
        ("prompt_tokens", "_prompt_tokens"),
        ("completion_tokens", "_completion_tokens"),
        ("cache_read_input_tokens", "_cache_read_input_tokens"),
    ]:
        pipeline.model.usage[field] = sum(
            v for k, v in pipeline.metrics.items() if k.endswith(suffix)
        )
    pipeline.model.usage["total_tokens"] = (
        pipeline.model.usage["prompt_tokens"] + pipeline.model.usage["completion_tokens"]
    )
    candidates = checkpoint["candidates"]
    for items in candidates.values():
        for _, artifact in items:
            if artifact["base_hash"]:
                old = await file_ops.load_old(pipeline, artifact["path"])
                if old is None or content_hash(old) != artifact["base_hash"]:
                    raise ValueError(f"Historical revision changed: {artifact['path']}")
    prepared = False
    try:
        refs = await reduce_op.resolve_files(
            pipeline, [reference for items in candidates.values() for reference, _ in items]
        )
        if pipeline.failures:
            pipeline.warnings.append(
                "Partial output; source-stage failures retained during recovery."
            )
        result = await finalize_op.run(pipeline, refs, partial=bool(pipeline.failures))
        prepared = not pipeline.failures
        return result
    finally:
        await pipeline.write_coverage()
        await pipeline.files.put(
            "summary",
            {
                **summary,
                "prepared": prepared,
                "committed": False,
                "states": pipeline.status,
                "metrics": dict(pipeline.metrics),
                "errors": pipeline.failures,
                "warnings": pipeline.warnings,
            },
        )


async def main():
    """Check or copy a stopped local task and publish its remaining merges as a new task.

    Uses the local CLI identity and existing service publisher. The source stays
    immutable; active tasks for the same target block recovery. No service restart
    or automatic cancellation occurs. The new task ID and progress go to stdout.
    """
    import argparse
    import fcntl
    import hashlib
    import shutil
    import uuid
    from types import SimpleNamespace

    from vikingbot.cli.commands import _make_provider
    from vikingbot.compile.models import CompileLimits, CompileTask, utc_now
    from vikingbot.compile.service import BotCompileService
    from vikingbot.compile.store import CompileTaskStore
    from vikingbot.config.loader import load_config
    from vikingbot.config.schema import SessionKey
    from vikingbot.openviking_mount.ov_server import VikingClient
    from vikingbot.sandbox.backends.direct import DirectBackend

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--concurrency", type=int, help="Override bot.compile.reduce_concurrency for recovery"
    )
    parser.add_argument(
        "--connection-config", type=Path, default=Path.home() / ".openviking/ovcli.conf"
    )
    args = parser.parse_args()
    config = load_config()
    store = CompileTaskStore(config.bot_data_path)
    source = await store.get(args.task_id)
    if source is None:
        raise ValueError("Source task not found")
    auth = json.loads(args.connection_config.read_text())
    account, user = auth.get("account"), auth.get("user")
    scope = hashlib.sha256(f"openviking:{account}:{user}".encode()).hexdigest()[:24]
    if not account or not user or scope != source.principal_scope:
        raise ValueError("CLI identity does not own the source task")
    if auth["url"].rstrip("/") != config.ov_server.server_url.rstrip("/"):
        raise ValueError("CLI and Bot server URLs differ")
    workspace = config.bot_data_path / "compile_workspaces" / source.task_id
    checkpoint = await asyncio.to_thread(read_checkpoint, workspace)
    counts = {
        "total": sum(len(v) > 1 for v in checkpoint["candidates"].values()),
        "completed": len(checkpoint["completed"]),
        "paths": len(checkpoint["candidates"]),
    }
    print(json.dumps({"source": source.task_id, **counts}, ensure_ascii=False), flush=True)
    if args.check:
        return
    if source.status not in {"cancelled", "failed"} or checkpoint["summary"] is None:
        raise ValueError("Stop the source task and wait for its summary before recovery")
    lock = (workspace / ".resume.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for path in store.root.glob("cmp_*.json"):
        task = CompileTask.model_validate_json(path.read_text())
        if (
            task.status not in {"completed", "failed", "cancelled"}
            and task.principal_scope == scope
            and task.sanitized_request.to == source.sanitized_request.to
        ):
            raise ValueError(f"Target has an active task: {task.task_id}")
    limits = (
        CompileLimits(merge_concurrency=args.concurrency) if args.concurrency is not None else None
    )
    task_id = "cmp_" + uuid.uuid4().hex
    destination = workspace.parent / task_id
    # Large model caches and call traces remain in the immutable source workspace.
    await asyncio.to_thread(
        shutil.copytree,
        workspace / ROOT,
        destination / ROOT,
        ignore=shutil.ignore_patterns("cache", "calls", "embeddings", "routes"),
    )
    (destination / "resume-source-task.json").write_text(source.model_dump_json())
    provider = _make_provider(config)
    loop = SimpleNamespace(
        config=config,
        provider=provider,
        model=config.agents.model,
        temperature=config.agents.temperature,
    )
    service = BotCompileService(agent_loop=loop, limits=limits)
    task = source.model_copy(
        update={
            "task_id": task_id,
            "status": "running",
            "stage": "resuming",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "error": None,
            "result": None,
            "meta": {
                "resumed_from": source.task_id,
                "merge_concurrency": service.limits.merge_concurrency,
            },
        }
    )
    await store.create(task)
    print(json.dumps({"task_id": task_id, "workspace": str(destination), **counts}), flush=True)
    connection = {"api_key": auth.get("api_key"), "account_id": account, "user_id": user}
    client = None
    sandbox = DirectBackend(
        config.sandbox, SessionKey(type="compile", channel_id=task_id, chat_id=task_id), destination
    )
    await sandbox.start()
    try:
        client = await VikingClient.create(connection=connection, config=config)
        await service._run_pipeline(
            task_id=task_id,
            request=source.sanitized_request,
            client=client,
            sandbox=sandbox,
            skill_text=await client.read_raw(source.sanitized_request.skill + "/SKILL.md"),
            source_files=[],
            usage={},
            resume=True,
        )
    except BaseException as exc:
        from vikingbot.compile.models import CompileFailure

        await service._fail(
            task_id,
            CompileFailure("RESUME_FAILED", str(exc) or type(exc).__name__, stage="resuming"),
        )
        raise
    finally:
        if client is not None:
            await client.close()
        await sandbox.stop()
        lock.close()


if __name__ == "__main__":
    asyncio.run(main())
