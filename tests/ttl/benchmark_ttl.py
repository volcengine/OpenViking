"""Isolated local AGFS/Redis cost sample; not a production acceptance benchmark.

Run with TTL_TEST_REDIS and PYTHONPATH including the matching OSS checkout.
Prints JSON; never reads deployment credentials or touches an existing scope.
"""

import asyncio
import json
import os
import resource
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from uuid import uuid4

from openviking.storage.viking_fs import VikingFS
from openviking.ttl.adapter import TTLBackfillRequest, TTLRecycleRequest
from openviking.ttl.runtime import DirectoryTTL
from openviking.ttl.wiring import install_ttl_hook
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.ttl_config import TTLConfig


async def main():
    report = {"samples_per_mode": 200, "storage": "native AGFS local + Redis TCP"}
    with tempfile.TemporaryDirectory(prefix="ttl-bench-") as folder:
        fs = VikingFS(
            agfs=create_agfs_client(
                RagfsBindingConfig(agfs=AGFSConfig(path=folder, backend="local"))
            )
        )
        e = DirectoryTTL(
            resource_id="bench-" + uuid4().hex,
            redis={"endpoints": [os.environ["TTL_TEST_REDIS"]]},
            stat_rate=200,
            delete_interval=0.005,
        )
        e.fs = fs
        off, on = (TTLConfig(), None), (TTLConfig(sessions=1), None)
        mode = "baseline"

        async def policies(account):
            return on if mode == "enabled" or (mode == "mixed" and account == "enabled") else off

        e.policies = policies
        await e.start()
        root = "viking://user/u/sessions"
        uris = [root + f"/s{i}/messages.jsonl" for i in range(16)]
        for account in ("enabled", "disabled"):
            for uri in uris:
                await fs.write_file(uri, "warmup", ctx=e.context(account))
        counts = Counter()
        for owner, name, label in (
            (e, "client", "redis_calls"),
            (fs, "stat", "ttl_stat_calls"),
            (fs._async_agfs, "pathlock_acquire_exact", "exact_locks"),
        ):
            original = getattr(owner, name)

            async def counted(*args, _original=original, _label=label, **kwargs):
                counts[_label] += 1
                return await _original(*args, **kwargs)

            setattr(owner, name, counted)
        tracemalloc.start()
        for mode in ("baseline", "off", "mixed", "enabled", "off", "baseline"):
            fs.set_post_write_hook(None)
            fs._write_guard = None
            if mode != "baseline":
                install_ttl_hook(fs, e)
            counts.clear()
            latencies = []
            cpu = time.process_time()
            start = time.perf_counter()
            for i in range(200):
                account = "enabled" if i % 2 == 0 else "disabled"
                tick = time.perf_counter()
                await fs.write_file(uris[i % len(uris)], "x" * 512, ctx=e.context(account))
                latencies.append((time.perf_counter() - tick) * 1000)
            elapsed = time.perf_counter() - start
            pending_at_return = len(e._retry)
            drain_started = time.perf_counter()
            async with asyncio.timeout(30):
                while e._retry:
                    await asyncio.sleep(0.01)
            latencies.sort()
            sample = {
                "p95_ms": round(latencies[189], 3),
                "p99_ms": round(latencies[197], 3),
                "ops_s": round(200 / elapsed, 1),
                "cpu_s": round(time.process_time() - cpu, 3),
                "pending_at_return": pending_at_return,
                "drain_seconds": round(time.perf_counter() - drain_started, 3),
                **counts,
                "python_peak_bytes": tracemalloc.get_traced_memory()[1],
            }
            report.setdefault(mode, []).append(sample)
            if mode in ("baseline", "off"):
                assert (
                    counts["redis_calls"] == counts["ttl_stat_calls"] == counts["exact_locks"] == 0
                )
        # Known historical directories: scanning only stats missing roots.
        fs.set_post_write_hook(None)
        fs._write_guard = None
        for i in range(64):
            await fs.write_file(
                root + f"/history{i}/messages.jsonl",
                "history",
                ctx=e.context("enabled"),
            )
        mode = "enabled"
        counts.clear()
        await e.register_initial_backfill(
            TTLBackfillRequest(e.resource_id, "enabled", (root,), True)
        )
        started = time.perf_counter()
        await e._scan("enabled")
        report["initial"] = {
            "seconds": round(time.perf_counter() - started, 3),
            **counts,
        }
        # Large directory ls peak is independent from batch/stat rate limiting.
        tracemalloc.reset_peak()
        started = time.perf_counter()
        rows = await fs.ls(root, ctx=e.context("enabled"), node_limit=None, include_abstract=False)
        report["ls"] = {
            "directories": len(rows),
            "seconds": round(time.perf_counter() - started, 3),
            "python_peak_bytes": tracemalloc.get_traced_memory()[1],
        }
        # Recycle actual files under native locks using a fixed future clock.
        from unittest.mock import patch

        future = time.time_ns() + 3 * 86400 * 1_000_000_000
        started = time.perf_counter()
        with patch("openviking.ttl.runtime.time.time_ns", return_value=future):
            recycled = await e.recycle_due(
                TTLRecycleRequest(e.resource_id, "enabled", future // 1_000_000)
            )
        report["recycle"] = {
            "seconds": round(time.perf_counter() - started, 3),
            **vars(recycled),
            "delete_interval_s": e.delete_interval,
        }
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["process_peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
        redis = await e.client()
        for account in ("enabled", "disabled"):
            await redis.delete(
                *(
                    e.prefix(account) + s
                    for s in (
                        "sessions",
                        "backfill:initial",
                        "backfill:initial:done",
                        "backfill:pending",
                    )
                )
            )
        await e.aclose()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
