import argparse
import asyncio
import contextvars
import functools
import hashlib
import importlib
import json
import platform
import socket
import subprocess
import threading
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from uuid import uuid4


class Profiler:
    def __init__(self):
        self.stage = contextvars.ContextVar("profile_stage", default="other")
        self.lock = threading.Lock()
        self.patches = []
        self.active = False
        self.events = []

    def begin(self, case):
        with self.lock:
            self.case = case
            self.events = []
            self.started = time.perf_counter()
            self.active = True

    @staticmethod
    def union_seconds(intervals):
        total = 0.0
        end = float("-inf")
        for start, stop in sorted(intervals):
            total += max(0.0, stop - max(start, end))
            end = max(end, stop)
        return total

    def patch(self, owner, name, label, io=False):
        original = getattr(owner, name)
        inherited = name not in vars(owner)
        descriptor = vars(owner).get(name, original)

        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            if not self.active:
                return await original(*args, **kwargs)
            stage = self.stage.get()
            key = f"{stage}:{args[1]}" if io else label
            token = None if io else self.stage.set(label)
            start = time.perf_counter()
            failed = False
            exception_type = None
            size = 0
            extra = None
            try:
                result = await original(*args, **kwargs)
                if io and isinstance(result, (bytes, str)):
                    size = len(result if isinstance(result, bytes) else result.encode())
                if label == "sync_tree":
                    extra = {k: len(v) for k, v in vars(result).items() if isinstance(v, list)}
                return result
            except BaseException as exc:
                failed = True
                exception_type = type(exc).__name__
                if io and args[1] == "write":
                    print(f"PROFILE io_error {key} {exception_type}", flush=True)
                raise
            finally:
                stop = time.perf_counter()
                if token is not None:
                    self.stage.reset(token)
                with self.lock:
                    self.events.append(
                        {
                            "kind": "io" if io else "stage",
                            "name": key,
                            "start": start - self.started,
                            "end": stop - self.started,
                            "error": failed,
                            "exception_type": exception_type,
                            "bytes": size,
                            "diff": extra,
                        }
                    )
                if not io and label in {
                    "stage_source",
                    "materialize",
                    "shared_materialize",
                    "parse",
                    "finalize",
                    "sync_tree",
                    "local_persist",
                    "local_diff_apply",
                    "semantic_dag",
                }:
                    print(f"PROFILE {self.case} {label} {stop - start:.3f}s", flush=True)

        setattr(
            owner, name, staticmethod(wrapped) if isinstance(descriptor, staticmethod) else wrapped
        )
        self.patches.append((owner, name, descriptor, inherited))

    def restore(self):
        for owner, name, original, inherited in reversed(self.patches):
            if inherited:
                delattr(owner, name)
            else:
                setattr(owner, name, original)
        self.patches.clear()

    def finish(self):
        with self.lock:
            self.active = False
            elapsed = time.perf_counter() - self.started
            events = list(self.events)
        result = {"case": self.case, "elapsed_s": elapsed, "stages": {}, "io": {}, "events": events}
        groups = defaultdict(list)
        for event in events:
            groups[(event["kind"], event["name"])].append(event)
        for (kind, name), group in groups.items():
            intervals = [(e["start"], e["end"]) for e in group]
            result["io" if kind == "io" else "stages"][name] = {
                "calls": len(group),
                "errors": sum(e["error"] for e in group),
                "sum_s": sum(b - a for a, b in intervals),
                "wall_union_s": self.union_seconds(intervals),
                "bytes": sum(e["bytes"] for e in group),
            }
        return result

    def install(self):
        targets = [
            (
                "openviking.server.temp_upload_store",
                "TempUploadStore",
                "save_upload",
                "shared_upload",
            ),
            (
                "openviking.server.temp_upload_store",
                "TempUploadStore",
                "_resolve_shared",
                "shared_resolve",
            ),
            (
                "openviking.server.temp_upload_store",
                "ResolvedTempUpload",
                "cleanup",
                "upload_local_cleanup",
            ),
            ("openviking.resource.staged_source", None, "stage_source", "stage_source"),
            ("openviking.resource.staged_source", None, "materialize_source", "materialize"),
            (
                "openviking.resource.shared_source",
                None,
                "materialize_shared_source",
                "shared_materialize",
            ),
            ("openviking.utils.media_processor", "UnifiedResourceProcessor", "process", "parse"),
            ("openviking.parse.tree_builder", "TreeBuilder", "finalize_from_temp", "finalize"),
            (
                "openviking.utils.resource_processor",
                "ResourceProcessor",
                "acquire_resource_lock",
                "resource_lock",
            ),
            ("openviking.storage.viking_fs", "VikingFS", "sync_tree", "sync_tree"),
            ("openviking.storage.viking_fs", "VikingFS", "persist_temp_tree", "persist"),
            ("openviking.storage.viking_fs", "VikingFS", "delete_temp", "delete_temp"),
            (
                "openviking.utils.resource_processor",
                "ResourceProcessor",
                "_persist_local_artifact",
                "local_persist",
            ),
            (
                "openviking.utils.resource_processor",
                "ResourceProcessor",
                "_apply_local_incremental",
                "local_diff_apply",
            ),
            (
                "openviking.storage.queuefs.semantic_processor",
                "SemanticProcessor",
                "_rewrite_target_image_uris",
                "image_rewrite",
            ),
            (
                "openviking.storage.queuefs.semantic_dag",
                "SemanticDagExecutor",
                "run",
                "semantic_dag",
            ),
            (
                "openviking.storage.queuefs.semantic_dag",
                "SemanticDagExecutor",
                "_file_summary_task",
                "file_summary",
            ),
            (
                "openviking.storage.queuefs.semantic_dag",
                "SemanticDagExecutor",
                "_overview_task",
                "directory_summary",
            ),
            (
                "openviking.storage.queuefs.semantic_dag",
                "SemanticDagExecutor",
                "_check_file_content_changed",
                "check_file",
            ),
            (
                "openviking.storage.queuefs.semantic_dag",
                "SemanticDagExecutor",
                "_read_existing_summary",
                "reuse_summary",
            ),
            (
                "openviking.storage.queuefs.semantic_processor",
                "SemanticProcessor",
                "_generate_single_file_summary",
                "generate_file_summary",
            ),
            (
                "openviking.storage.queuefs.semantic_processor",
                "SemanticProcessor",
                "_generate_overview",
                "generate_overview",
            ),
            (
                "openviking.models.vlm.backends.volcengine_vlm",
                "VolcEngineVLM",
                "get_completion_async",
                "llm",
            ),
            (
                "openviking.models.embedder.volcengine_embedders",
                "VolcengineDenseEmbedder",
                "embed_async",
                "embedding",
            ),
            (
                "openviking.storage.collection_schemas",
                "TextEmbeddingHandler",
                "on_dequeue",
                "embedding_handler",
            ),
            (
                "openviking.storage.viking_vector_index_backend",
                "VikingVectorIndexBackend",
                "upsert",
                "vector_upsert",
            ),
        ]
        for module, cls, method, label in targets:
            owner = importlib.import_module(module)
            if cls:
                owner = getattr(owner, cls)
            self.patch(owner, method, label)
        from openviking.pyagfs.async_client import AsyncAGFSClient

        self.patch(AsyncAGFSClient, "run", "io", io=True)


def prepare_fixture(root, limit):
    output = root / "source"
    output.mkdir()
    repo = Path(__file__).resolve().parents[2]
    names = subprocess.check_output(
        [
            "git",
            "ls-files",
            "openviking/storage",
            "openviking/parse",
            "openviking/core",
            "openviking/utils",
        ],
        cwd=repo,
        text=True,
    ).splitlines()
    names = sorted(n for n in names if n.endswith(".py"))
    if limit:
        names = names[:limit]
    manifest = []
    for name in names:
        data = (repo / name).read_bytes()
        dst = output / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        manifest.append(
            {
                "path": name,
                "bytes": len(data),
                "lines": len(data.splitlines()),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    subprocess.run(["git", "init", "--quiet", str(output)], check=True)
    return output, manifest


def pack_fixture(source, archive):
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(source.parent).as_posix())
    return archive


async def start_http_server(service):
    import httpx
    import uvicorn

    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig, TempUploadConfig

    api_key = uuid4().hex
    config = ServerConfig(
        root_api_key=api_key,
        temp_upload=TempUploadConfig(default_mode="shared"),
    )
    app = create_app(config=config, service=service)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    ready = asyncio.Event()
    startup = server.startup

    async def startup_and_signal(sockets=None):
        await startup(sockets=sockets)
        ready.set()

    server.startup = startup_and_signal
    task = asyncio.create_task(server.serve(sockets=[sock]))
    await asyncio.wait_for(ready.wait(), timeout=180)
    if not server.started:
        await task
        raise RuntimeError("Benchmark HTTP server failed to start")
    api_key = await app.state.api_key_manager.create_account("sharedbench", "benchmark")
    client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        headers={"X-API-Key": api_key},
        timeout=None,
        trust_env=False,
    )
    return server, task, client, sock


async def benchmark(args):
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source, manifest = prepare_fixture(root, args.limit)
    raw = json.loads(Path(args.config).read_text())
    raw["storage"]["workspace"] = str(root / "workspace")
    raw["storage"]["agfs"]["backend"] = "s3"
    raw["storage"]["agfs"]["s3"]["prefix"] = f"bench/add-resource/{root.name}/"
    raw["storage"]["agfs"].pop("backups", None)
    raw["storage"]["agfs"].pop("redirects", None)
    raw["storage"]["vectordb"] = {"backend": "local"}
    if args.parse_output == "local":
        raw["storage"]["parse_output"] = {
            "mode": "local",
            "local_root": str(root / "parse-out"),
        }
    raw["log"] = {"level": "INFO", "output": "stdout"}
    raw["enable_watch_scheduler"] = False
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    config = OpenVikingConfigSingleton.initialize(config_dict=raw)
    from openviking.server.identity import RequestContext, Role
    from openviking.service.core import OpenVikingService
    from openviking.storage.queuefs.queue_manager import get_queue_manager
    from openviking.telemetry import OperationTelemetry, bind_telemetry
    from openviking_cli.session.user_id import UserIdentifier

    info = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "files": len(manifest),
        "bytes": sum(x["bytes"] for x in manifest),
        "lines": sum(x["lines"] for x in manifest),
        "manifest": manifest,
        "mode": args.mode,
        "entry": args.entry,
        "parse_output_mode": args.parse_output,
        "upload_mode": "shared" if args.entry == "shared_http" else None,
        "shared_settle_seconds": (
            args.shared_settle_seconds if args.entry == "shared_http" else None
        ),
        "s3_prefix": raw["storage"]["agfs"]["s3"]["prefix"],
        "vlm_model": config.vlm.model,
        "vlm_concurrency": config.vlm.max_concurrent,
        "embedding_concurrency": config.embedding.max_concurrent,
        "embedding_model": raw["embedding"]["dense"]["model"],
    }
    (root / "environment.json").write_text(json.dumps(info, indent=2))
    print(
        "PROFILE environment",
        json.dumps({k: v for k, v in info.items() if k != "manifest"}),
        flush=True,
    )
    profiler = Profiler()
    profiler.install()
    user = UserIdentifier("default", "benchmark")
    ctx = RequestContext(user=user, role=Role.ROOT)
    service = OpenVikingService(user=user)
    server = server_task = http_client = sock = None
    try:
        await service.initialize()
        if args.entry == "shared_http":
            server, server_task, http_client, sock = await start_http_server(service)
            ctx = RequestContext(user=UserIdentifier("sharedbench", "benchmark"), role=Role.ROOT)
        await get_queue_manager().wait_complete(timeout=args.timeout)
        print("PROFILE initialized", flush=True)
        target = "viking://resources/ingest-profile"
        editable = [m for m in manifest if m["bytes"] > 1000]
        if not editable:
            editable = manifest
        cases = ["initial"] + args.cases.split(",")
        for index, case in enumerate(cases):
            changed = []
            if case.startswith("edit"):
                count = max(1, round(len(manifest) * 0.01)) if case == "edit_1pct" else 1
                for item in editable[:count]:
                    path = source / item["path"]
                    path.write_bytes(
                        path.read_bytes() + f"\nPROFILE_REVISION_{index} = {index}\n".encode()
                    )
                    changed.append(item["path"])
            archive = None
            pack_s = None
            if http_client is not None:
                pack_started = time.perf_counter()
                archive = await asyncio.to_thread(pack_fixture, source, root / "source.zip")
                pack_s = time.perf_counter() - pack_started
            upload_s = None
            ingest_http_s = None
            telemetry = OperationTelemetry(operation="add_resource", enabled=True)
            profiler.begin(f"{index:02d}-{case}")
            started = time.perf_counter()
            status = "error"
            api_s = None
            queue_status = {}
            result = {}
            try:
                if http_client is not None:
                    upload_started = time.perf_counter()
                    with archive.open("rb") as upload_file:
                        response = await http_client.post(
                            "/api/v1/resources/temp_upload",
                            data={"upload_mode": "shared"},
                            files={"file": ("source.zip", upload_file, "application/zip")},
                        )
                    response.raise_for_status()
                    upload_s = time.perf_counter() - upload_started
                    upload_id = response.json()["result"]["temp_file_id"]
                    if not upload_id.startswith("shared_"):
                        raise RuntimeError("Upload did not use shared mode")
                    if args.shared_settle_seconds > 0:
                        await asyncio.sleep(args.shared_settle_seconds)
                    ingest_started = time.perf_counter()
                    response = await http_client.post(
                        "/api/v1/resources",
                        json={
                            "temp_file_id": upload_id,
                            "to": target,
                            "wait": True,
                            "timeout": args.timeout,
                            "processing_mode": args.mode,
                        },
                    )
                    if response.is_error:
                        print("PROFILE http_error", response.text, flush=True)
                    response.raise_for_status()
                    ingest_http_s = time.perf_counter() - ingest_started
                    result = response.json()["result"]
                    result.setdefault("status", response.json().get("status", "unknown"))
                else:
                    with bind_telemetry(telemetry):
                        result = await service.resources.add_resource(
                            str(source),
                            ctx=ctx,
                            to=target,
                            wait=True,
                            timeout=args.timeout,
                            processing_mode=args.mode,
                        )
                api_s = time.perf_counter() - started
                queues = await get_queue_manager().wait_complete(timeout=args.timeout)
                queue_status = {
                    name: {
                        key: getattr(q, key)
                        for key in (
                            "pending",
                            "in_progress",
                            "processed",
                            "error_count",
                            "requeue_count",
                        )
                    }
                    for name, q in queues.items()
                }
                status = result.get("status", "unknown")
                if any(q.error_count for q in queues.values()):
                    status = "queue_error"
            finally:
                report = profiler.finish()
                report.update(
                    {
                        "api_s": api_s,
                        "pack_s": pack_s,
                        "archive_bytes": archive.stat().st_size if archive else None,
                        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()
                        if archive
                        else None,
                        "upload_http_s": upload_s,
                        "shared_settle_s": (
                            args.shared_settle_seconds if http_client is not None else None
                        ),
                        "ingest_http_s": ingest_http_s,
                        "status": status,
                        "queues": queue_status,
                        "changed": changed,
                        "result": result,
                    }
                )
                (root / f"{index:02d}-{case}.json").write_text(
                    json.dumps(report, indent=2, default=str)
                )
            if status != "success":
                raise RuntimeError(f"Ingestion failed: {status}")
            files = await service.viking_fs.tree(target, node_limit=None, level_limit=None, ctx=ctx)
            visible = {
                entry["uri"]
                for entry in files
                if not entry.get("isDir") and not entry.get("name", "").startswith(".")
            }
            expected = {f"{target}/{m['path']}" for m in manifest}
            missing = sorted(expected - visible)
            mismatches = []
            from openviking.storage.expr import And, Eq, PathScope

            records = await service.vikingdb_manager.filter(
                filter=And([PathScope("uri", target, depth=-1), Eq("level", 2)]),
                limit=10000,
                output_fields=["uri"],
                ctx=ctx,
            )
            indexed = {record["uri"] for record in records}
            expected_indexed = {
                f"{target}/{m['path']}"
                for m in manifest
                if (source / m["path"]).read_bytes().strip()
            }
            vectors_missing = sorted(expected_indexed - indexed)
            checks = (
                [m["path"] for m in manifest]
                if args.entry == "shared_http"
                else changed or [editable[0]["path"]]
            )
            validation_limit = asyncio.Semaphore(8)

            async def check_content(
                name, visible=visible, validation_limit=validation_limit, mismatches=mismatches
            ):
                if f"{target}/{name}" not in visible:
                    return
                async with validation_limit:
                    remote = await service.viking_fs.read_file_bytes(f"{target}/{name}", ctx=ctx)
                if remote != (source / name).read_bytes():
                    mismatches.append(name)

            await asyncio.gather(*(check_content(name) for name in checks))
            report["validation"] = {
                "visible_files": len(visible),
                "expected_files": len(expected),
                "missing_files": missing,
                "unexpected_files": sorted(visible - expected),
                "content_checked_files": len(checks),
                "unexpected_vectors": sorted(indexed - expected_indexed),
                "content_mismatches": mismatches,
                "indexed_files": len(indexed),
                "vectors_missing": vectors_missing,
            }
            (root / f"{index:02d}-{case}.json").write_text(
                json.dumps(report, indent=2, default=str)
            )
            print(
                "PROFILE result",
                json.dumps(
                    {k: v for k, v in report.items() if k not in {"events", "io", "result"}}
                ),
                flush=True,
            )
            if (
                missing
                or mismatches
                or vectors_missing
                or visible - expected
                or indexed - expected_indexed
            ):
                raise RuntimeError("Benchmark correctness validation failed")
    finally:
        if http_client is not None:
            await http_client.aclose()
        if server is not None:
            server.should_exit = True
            await server_task
            sock.close()
        await service.close()
        profiler.restore()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", choices=["local", "shared_http"], default="local")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--mode", choices=["semantic_and_vectors", "vectors_only"], default="semantic_and_vectors"
    )
    parser.add_argument(
        "--parse-output",
        dest="parse_output",
        choices=["agfs", "local"],
        default="agfs",
        help="Parse artifact backend: agfs (shared temp) or local (local dir).",
    )
    parser.add_argument("--cases", default="noop,noop,edit_one,edit_one,edit_1pct")
    parser.add_argument("--shared-settle-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=3600)
    asyncio.run(benchmark(parser.parse_args()))
