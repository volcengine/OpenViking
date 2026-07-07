#!/usr/bin/env python3
"""Backfill VikingDB content fields from Local AGFS source data."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openviking.core.namespace import context_type_for_uri, owner_fields_for_uri
from openviking.server.identity import RequestContext, Role
from openviking.storage.vectordb_adapters import create_collection_adapter
from openviking.storage.vectordb_adapters.base import _truncate_text_field
from openviking.storage.viking_fs import VikingFS
from openviking.utils.agfs_utils import (
    RagfsBindingConfig,
    _generate_plugin_config,
    build_runtime_ragfs_binding_config,
)
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.session.user_id import UserIdentifier


def seed_uri_for_level(uri: str, level: Any) -> str:
    """Build the deterministic ID seed URI used by embedding writes."""
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        level_int = 2

    if level_int == 0:
        return uri if uri.endswith("/.abstract.md") else f"{uri}/.abstract.md"
    if level_int == 1:
        return uri if uri.endswith("/.overview.md") else f"{uri}/.overview.md"
    return uri


def vector_record_id(account_id: str, uri: str, level: Any) -> str:
    """Return the deterministic vector record ID for account, URI, and level."""
    seed_uri = seed_uri_for_level(uri, level)
    return hashlib.md5(f"{account_id}:{seed_uri}".encode("utf-8")).hexdigest()


def has_empty_content(record: dict[str, Any]) -> bool:
    """Return whether a vector record needs content backfill."""
    value = record.get("content")
    return value is None or value == ""


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = parse_iso_datetime(value)
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def created_after_cutoff(record: dict[str, Any], cutoff: datetime) -> bool:
    """Return True only when creation time is known and strictly after cutoff."""
    created = _coerce_datetime(record.get("created_at") or record.get("create_time"))
    if created is None:
        return False
    comparable_cutoff = cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff
    return created > comparable_cutoff.astimezone(timezone.utc)


def updated_at_unchanged(original: dict[str, Any], current: dict[str, Any]) -> bool:
    """Compare update timestamps for optimistic online backfill protection."""
    if "updated_at" not in original:
        return True
    return original.get("updated_at") == current.get("updated_at")


@dataclass(frozen=True)
class BackfillCandidate:
    account_id: str
    owner_user_id: str
    uri: str
    level: int
    context_type: str
    expected_record_id: str


@dataclass(frozen=True)
class BackfillContentResult:
    content: str
    source: str
    warning: str | None = None


class ContentBackfillResolver:
    """Resolve the content field for a backfill candidate from a source reader."""

    def __init__(self, source_reader: Any):
        self._source = source_reader
        # Defaults match the semantic config defaults closely enough for construction;
        # tests may override them, and the runner will set values from config.
        self._memory_chunk_chars = 4000
        self._memory_chunk_overlap = 200

    async def resolve(
        self,
        candidate: BackfillCandidate,
        record: dict[str, Any],
    ) -> BackfillContentResult:
        ctx = self._ctx(candidate)
        level = int(candidate.level)
        if level == 0:
            content = await self._source.abstract(candidate.uri, ctx)
            return BackfillContentResult(content=content, source="abstract")
        if level == 1:
            content = await self._source.overview(candidate.uri, ctx)
            return BackfillContentResult(content=content, source="overview")

        if "#" in candidate.uri and candidate.context_type == "memory":
            content = await self._resolve_memory_chunk(candidate, ctx)
            return BackfillContentResult(content=content, source="memory_chunk")

        content = await self._source.read_text(candidate.uri, ctx)
        if content:
            return BackfillContentResult(content=content, source="file")

        abstract = str(record.get("abstract") or "")
        if abstract:
            return BackfillContentResult(
                content=abstract,
                source="abstract_fallback",
                warning=f"source content unavailable for {candidate.uri}; used abstract",
            )
        return BackfillContentResult(
            content="",
            source="missing",
            warning=f"source content unavailable for {candidate.uri}",
        )

    def _ctx(self, candidate: BackfillCandidate) -> RequestContext:
        return RequestContext(
            user=UserIdentifier(candidate.account_id, candidate.owner_user_id or "default"),
            role=Role.ROOT,
        )

    async def _resolve_memory_chunk(
        self,
        candidate: BackfillCandidate,
        ctx: RequestContext,
    ) -> str:
        base_uri, chunk_name = candidate.uri.split("#", 1)
        body = await self._source.read_text(base_uri, ctx)
        for chunk_uri, chunk_text in self._chunk_memory_body(base_uri, body):
            if chunk_uri.endswith(f"#{chunk_name}"):
                return chunk_text
        return ""

    def _chunk_memory_body(self, uri: str, body: str) -> list[tuple[str, str]]:
        if len(body) <= self._memory_chunk_chars:
            return []

        chunks: list[str] = []
        start = 0
        overlap = max(0, int(self._memory_chunk_overlap))
        chunk_chars = max(1, int(self._memory_chunk_chars))
        while start < len(body):
            end = start + chunk_chars
            if end < len(body):
                boundary = body.rfind("\n\n", start, end)
                if boundary > start + chunk_chars // 2:
                    end = boundary + 2
            chunks.append(body[start:end].strip())
            if end >= len(body):
                break
            start = end - overlap
            if start < 0:
                start = 0
        return [(f"{uri}#chunk_{idx:04d}", chunk) for idx, chunk in enumerate(chunks) if chunk]


class ContentBackfillEnumerator:
    """Enumerate expected vector candidates from Local AGFS source data."""

    def __init__(self, raw_agfs: Any, source_reader: Any, node_limit: int | None = None):
        self._raw_agfs = raw_agfs
        self._source = source_reader
        self._node_limit = node_limit

    async def iter_candidates(self):
        for account_id in self._list_dir_names("/local"):
            if account_id.startswith("_"):
                print(f"skip internal account directory: {account_id}", flush=True)
                continue
            print(f"enumerating account={account_id} root=viking://", flush=True)
            async for candidate in self._iter_tree_candidates(
                account_id=account_id,
                root_uri="viking://",
                ctx=self._ctx(account_id, "default"),
                node_limit=self._node_limit,
            ):
                yield candidate

    def _list_dir_names(self, path: str) -> list[str]:
        try:
            entries = self._raw_agfs.ls(path)
        except Exception:
            return []
        names: list[str] = []
        for entry in entries or []:
            if entry.get("isDir", entry.get("is_dir", False)) and entry.get("name"):
                names.append(str(entry["name"]))
        return sorted(set(names))

    async def _iter_tree_candidates(
        self,
        *,
        account_id: str,
        root_uri: str,
        ctx: RequestContext,
        node_limit: int | None,
    ):
        try:
            entries = await self._source.tree(
                root_uri,
                ctx,
                show_all_hidden=True,
                node_limit=node_limit,
                level_limit=None,
            )
        except Exception:
            return

        for entry in entries:
            uri = entry.get("uri")
            if not uri:
                continue
            is_dir = bool(entry.get("isDir", entry.get("is_dir", False)))
            if is_dir:
                for level in (0, 1):
                    yield self._candidate(account_id, uri, level)
                continue
            if self._is_hidden_meta_file(uri):
                continue
            yield self._candidate(account_id, uri, 2)

    def _candidate(self, account_id: str, uri: str, level: int) -> BackfillCandidate:
        return BackfillCandidate(
            account_id=account_id,
            owner_user_id=self._owner_user_id(account_id, uri),
            uri=uri,
            level=level,
            context_type=context_type_for_uri(uri),
            expected_record_id=vector_record_id(account_id, uri, level),
        )

    @staticmethod
    def _is_hidden_meta_file(uri: str) -> bool:
        return uri.endswith("/.abstract.md") or uri.endswith("/.overview.md")

    @staticmethod
    def _owner_user_id(account_id: str, uri: str) -> str:
        owner_fields = owner_fields_for_uri(uri, account_id=account_id)
        return owner_fields.get("owner_user_id") or "default"

    @staticmethod
    def _ctx(account_id: str, user_id: str) -> RequestContext:
        return RequestContext(user=UserIdentifier(account_id, user_id), role=Role.ROOT)


class LocalAgfsContentSourceReader:
    """Read source content through a lightweight VikingFS instance."""

    def __init__(self, viking_fs: Any):
        self._viking_fs = viking_fs

    async def tree(
        self,
        uri: str,
        ctx: RequestContext,
        *,
        show_all_hidden: bool,
        node_limit: int | None,
        level_limit: int | None,
    ) -> list[dict[str, Any]]:
        return await self._viking_fs.tree(
            uri,
            output="original",
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        )

    async def read_text(self, uri: str, ctx: RequestContext) -> str:
        try:
            return await self._viking_fs.read_file(uri, ctx=ctx)
        except Exception:
            return ""

    async def read_bytes(self, uri: str, ctx: RequestContext) -> bytes:
        try:
            return await self._viking_fs.read(uri, ctx=ctx)
        except Exception:
            return b""

    async def abstract(self, uri: str, ctx: RequestContext) -> str:
        try:
            return await self._viking_fs.abstract(uri, ctx=ctx)
        except Exception:
            return ""

    async def overview(self, uri: str, ctx: RequestContext) -> str:
        try:
            return await self._viking_fs.overview(uri, ctx=ctx)
        except Exception:
            return ""


@dataclass(frozen=True)
class BackfillOptions:
    run_dir: Path
    execute: bool = False
    rewrite_non_empty: bool = False
    batch_size: int = 100
    limit: int | None = None
    cutoff: datetime | None = None
    fail_fast: bool = False
    record_candidates: bool = False
    record_skipped: bool = False


@dataclass
class BackfillSummary:
    candidate_count: int = 0
    existing_records: int = 0
    processed_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    missing_record_count: int = 0
    non_empty_content_count: int = 0
    created_after_cutoff_count: int = 0
    empty_resolved_content_count: int = 0
    changed_during_run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "existing_records": self.existing_records,
            "processed_count": self.processed_count,
            "updated_count": self.updated_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "missing_record_count": self.missing_record_count,
            "non_empty_content_count": self.non_empty_content_count,
            "created_after_cutoff_count": self.created_after_cutoff_count,
            "empty_resolved_content_count": self.empty_resolved_content_count,
            "changed_during_run_count": self.changed_during_run_count,
        }


class ContentBackfillRunner:
    """Run content backfill over enumerated deterministic vector candidates."""

    def __init__(
        self,
        *,
        enumerator: Any,
        resolver: ContentBackfillResolver,
        collection: Any,
        options: BackfillOptions,
    ):
        self._enumerator = enumerator
        self._resolver = resolver
        self._collection = collection
        self._options = options
        self._summary = BackfillSummary()

    async def run(self) -> BackfillSummary:
        self._options.run_dir.mkdir(parents=True, exist_ok=True)
        await self._count_candidates()
        self._write_progress()

        seen = 0
        async for candidate in self._enumerator.iter_candidates():
            if self._options.limit is not None and seen >= self._options.limit:
                break
            seen += 1
            await self._process_candidate(candidate)
            self._write_progress()

        self._write_summary()
        return self._summary

    async def _count_candidates(self) -> None:
        if self._options.record_candidates:
            candidate_path = self._options.run_dir / "candidates.jsonl"
            candidate_path.write_text("", encoding="utf-8")

        seen = 0
        async for candidate in self._enumerator.iter_candidates():
            if self._options.limit is not None and seen >= self._options.limit:
                break
            seen += 1
            if self._options.record_candidates:
                self._append_jsonl("candidates.jsonl", candidate.__dict__)
        self._summary.candidate_count = seen

    async def _process_candidate(self, candidate: BackfillCandidate) -> None:
        record = self._fetch_record(candidate.expected_record_id)
        if record is None:
            self._summary.skipped_count += 1
            self._summary.missing_record_count += 1
            self._append_skip(candidate, "missing")
            return
        self._summary.existing_records += 1

        cutoff = self._options.cutoff
        if cutoff is not None and created_after_cutoff(record, cutoff):
            self._summary.skipped_count += 1
            self._summary.created_after_cutoff_count += 1
            self._append_skip(candidate, "created_after_cutoff")
            return

        if not self._options.rewrite_non_empty and not has_empty_content(record):
            self._summary.skipped_count += 1
            self._summary.non_empty_content_count += 1
            self._append_skip(candidate, "content_non_empty")
            return

        result = await self._resolver.resolve(candidate, record)
        if not result.content:
            self._summary.skipped_count += 1
            self._summary.empty_resolved_content_count += 1
            self._append_skip(candidate, result.warning or "empty_resolved_content")
            return

        self._summary.processed_count += 1
        if not self._options.execute:
            return

        current = self._fetch_record(candidate.expected_record_id)
        if current is None:
            self._summary.skipped_count += 1
            self._summary.missing_record_count += 1
            self._append_skip(candidate, "missing_before_update")
            return
        if not self._options.rewrite_non_empty and not has_empty_content(current):
            self._summary.skipped_count += 1
            self._summary.non_empty_content_count += 1
            self._append_skip(candidate, "content_non_empty_before_update")
            return
        if not updated_at_unchanged(record, current):
            self._summary.skipped_count += 1
            self._summary.changed_during_run_count += 1
            self._append_skip(candidate, "updated_at_changed")
            return

        payload = {
            "id": candidate.expected_record_id,
            "content": _truncate_text_field(result.content),
        }
        try:
            self._collection.update_data([payload])
        except Exception as exc:
            self._summary.failed_count += 1
            self._append_jsonl(
                "failed.jsonl",
                {
                    "id": candidate.expected_record_id,
                    "uri": candidate.uri,
                    "reason": str(exc),
                },
            )
            if self._options.fail_fast:
                raise
            return
        self._summary.updated_count += 1
        self._append_jsonl(
            "updated.jsonl", {"id": candidate.expected_record_id, "uri": candidate.uri}
        )

    def _fetch_record(self, record_id: str) -> dict[str, Any] | None:
        result = self._collection.fetch_data([record_id])
        for item in getattr(result, "items", []) or []:
            if str(item.id) == str(record_id):
                record = dict(item.fields or {})
                record["id"] = item.id
                return record
        return None

    def _write_summary(self) -> None:
        (self._options.run_dir / "summary.json").write_text(
            json.dumps(self._summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_progress()

    def _write_progress(self) -> None:
        (self._options.run_dir / "progress.json").write_text(
            json.dumps(self._summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_jsonl(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self._options.run_dir / name
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _append_jsonl(self, name: str, row: dict[str, Any]) -> None:
        path = self._options.run_dir / name
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _append_skip(self, candidate: BackfillCandidate, reason: str) -> None:
        if not self._options.record_skipped:
            return
        self._append_jsonl(
            "skipped.jsonl",
            {"id": candidate.expected_record_id, "uri": candidate.uri, "reason": reason},
        )


SCRIPT_DIR = Path("scripts/maintenance/vikingdb_content_backfill")
RUNS_DIR = SCRIPT_DIR / "result"
ALLOWED_BACKENDS = {"volcengine", "vikingdb"}


def default_run_dir() -> Path:
    return RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")


def validate_backend(backend: str) -> None:
    if backend not in ALLOWED_BACKENDS:
        allowed = ", ".join(sorted(ALLOWED_BACKENDS))
        raise SystemExit(f"unsupported vectordb backend {backend!r}; expected one of: {allowed}")


def build_options(args: argparse.Namespace) -> BackfillOptions:
    return BackfillOptions(
        run_dir=Path(args.run_dir),
        execute=bool(args.execute),
        rewrite_non_empty=bool(getattr(args, "rewrite_non_empty", False)),
        batch_size=int(args.batch_size),
        limit=getattr(args, "limit", None),
        cutoff=datetime.now(),
        fail_fast=bool(getattr(args, "fail_fast", False)),
        record_candidates=bool(getattr(args, "record_candidates", False)),
        record_skipped=bool(getattr(args, "record_skipped", False)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=default_run_dir())
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--read-concurrency", type=int, default=8)
    parser.add_argument("--update-sleep-ms", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rewrite-non-empty", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--record-candidates", action="store_true")
    parser.add_argument("--record-skipped", action="store_true")
    return parser


async def run_from_args(args: argparse.Namespace) -> Any:
    from openviking_cli.utils.config import get_openviking_config

    config = get_openviking_config()
    validate_backend(config.storage.vectordb.backend)
    options = build_options(args)
    agfs = create_readonly_content_agfs_client(config)
    viking_fs = VikingFS(agfs=agfs, vector_store=None, query_embedder=None)
    source = LocalAgfsContentSourceReader(viking_fs)
    adapter = create_collection_adapter(config.storage.vectordb)
    collection = adapter.get_collection()
    enumerator = ContentBackfillEnumerator(agfs, source, node_limit=args.limit)
    resolver = ContentBackfillResolver(source)
    semantic = getattr(config, "semantic", None)
    if semantic is not None:
        resolver._memory_chunk_chars = getattr(semantic, "memory_chunk_chars", 4000)
        resolver._memory_chunk_overlap = getattr(semantic, "memory_chunk_overlap", 200)
    runner = ContentBackfillRunner(
        enumerator=enumerator,
        resolver=resolver,
        collection=collection,
        options=options,
    )
    summary = await runner.run()
    print(options.run_dir / "summary.json")
    return summary


def create_readonly_content_agfs_client(config: Any) -> Any:
    """Create a Local AGFS reader without mounting queuefs/serverinfofs."""
    binding_config, _ = build_runtime_ragfs_binding_config(config)
    from openviking.pyagfs import get_binding_client
    from openviking_cli.utils.config.config_loader import resolve_config_path
    from openviking_cli.utils.config.consts import DEFAULT_OV_CONF, OPENVIKING_CONFIG_ENV

    RAGFSBindingClient, _file_handle = get_binding_client()
    config_path = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    client = RAGFSBindingClient(
        str(config_path) if config_path else None,
        config=binding_config.to_binding_dict(),
    )
    mount_readonly_local_backend(client, binding_config)
    return client


def mount_readonly_local_backend(agfs: Any, binding_config: RagfsBindingConfig) -> None:
    agfs_config = binding_config.agfs
    data_path = Path(agfs_config.path).resolve()
    plugin_config = _generate_plugin_config(
        agfs_config,
        data_path,
        server_encryption_enabled=binding_config.encryption_enabled(),
    )
    for name in ("serverinfofs", "queuefs"):
        plugin_config.pop(name, None)
    for plugin in plugin_config.values():
        mount_path = plugin["path"]
        cfg = plugin.get("config", {})
        try:
            agfs.unmount(mount_path)
        except Exception:
            pass
        agfs.mount(_plugin_name_for_path(plugin_config, plugin), mount_path, cfg)


def _plugin_name_for_path(plugin_config: dict[str, Any], target: dict[str, Any]) -> str:
    for name, plugin in plugin_config.items():
        if plugin is target:
            return name
    raise ValueError("plugin not found")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run_from_args(args))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
