"""Fence TTL-active file indexing against deletion and superseding writes."""

from openviking.config.ttl import resolve_loaded_ttl_config
from openviking.server.error_mapping import is_not_found_error
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.ttl import scope_and_root, timestamp


async def upsert_content(fs, backend, record, ctx, options):
    target = scope_and_root(record.get("uri", ""))
    reader = getattr(backend, "ttl_policy_reader", None)
    config = reader(ctx.account_id) if target and reader else None
    if (
        config is None
        or not config.enabled
        or (record.get("level", 2) != 2 and target[0] != "sessions")
        or config.resolve_uri_policy(record["uri"], target[0]).mode != "days"
    ):
        return await backend.upsert(record, ctx=ctx, options=options)

    if fs is None:
        from openviking.storage.viking_fs import get_viking_fs

        fs = get_viking_fs()

    async def protected():
        path = fs._uri_to_path(record["uri"], ctx=ctx)
        lease = await fs._async_agfs.pathlock_acquire_exact(path)
        try:
            try:
                stat = await fs._async_agfs.stat(path)
            except Exception as exc:
                if is_not_found_error(exc):
                    return None
                raise
            actual, indexed = timestamp(stat.get("modTime")), timestamp(record.get("updated_at"))
            if target[0] == "sessions":
                import json
                from openviking.storage.ttl import deletion_uri

                session_path = fs._uri_to_path(deletion_uri(record["uri"]), ctx=ctx)
                raw = fs._handle_agfs_read(await fs._async_agfs.read(session_path + "/.meta.json"))
                actual = timestamp(json.loads(raw).get("created_at"))
                indexed = timestamp(record.get("created_at"))
            # No late message can recreate a deleted file's vector. A superseded
            # content version is acknowledged without publishing its embedding.
            if (
                actual is None
                or indexed is None
                or actual != indexed
                or (stat.get("isDir") and target[0] != "sessions")
            ):
                return None
            return await backend.upsert(record, ctx=ctx, options=options)
        finally:
            await fs._async_agfs.pathlock_release(lease)

    return await run_to_completion(protected)
