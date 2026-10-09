#!/usr/bin/env python3
"""Reproducible local parse, long-session replay and concurrent SSE measurements.

The upstream is a local synthetic server. This measures gateway overhead, not
provider latency or cache hit rates. Run with PYTHONPATH set to the tested tree.
"""

import argparse
import asyncio
import json
import os
import platform
import socket
import statistics
import tempfile
import time
from pathlib import Path

import aiohttp
import orjson
import uvicorn
from aiohttp import web
from cryptography.fernet import Fernet
from gateway_profile import RuntimeProfile

from openviking_gateway.app import create_app
from openviking_gateway.config import OpenVikingGatewayConfig
from openviking_gateway.kernel import MemoryKernel
from openviking_gateway.models import Policy, Upstream
from openviking_gateway.protocols import parse_body
from openviking_gateway.storage import SQLiteKernelStore, digest


def cycle(i):
    return [
        {"role": "user", "content": f"Check module {i} and explain the changes."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{i}-{k}",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps(
                            {"path": f"src/module{i}/file{k}.py", "offset": 1, "limit": 60}
                        ),
                    },
                }
                for k in range(4)
            ],
        },
        *[
            {
                "role": "tool",
                "tool_call_id": f"call-{i}-{k}",
                "content": json.dumps(
                    {
                        "path": f"src/module{i}/file{k}.py",
                        "lines": [f"{x}: return calculate(value_{x})" for x in range(14)],
                    }
                ),
            }
            for k in range(4)
        ],
        {"role": "assistant", "content": "These modules implement validation and dispatch."},
    ]


def distribution(values):
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
    }


async def benchmark(args):
    result = {
        "python": platform.python_version(),
        "concurrency": args.concurrency,
        "method": "one process, local synthetic upstream, warm replay, wall-clock milliseconds",
    }
    payload = orjson.dumps(
        {"model": "benchmark", "messages": [{"role": "user", "content": "x" * (8 * 1024 * 1024)}]}
    )
    times = []
    for _ in range(15):
        started = time.perf_counter()
        assert parse_body(payload)
        times.append((time.perf_counter() - started) * 1000)
    result["parse_8mib"] = distribution(times)
    with tempfile.TemporaryDirectory(prefix="ovgw-bench-") as directory:
        encryption = Fernet.generate_key().decode()
        store = SQLiteKernelStore(Path(directory) / "replay.sqlite3", encryption)
        await store.initialize()
        policy = Policy(recall=False, capture=False, takeover=False).model_dump()
        credential = {
            "account": "bench",
            "user_id": "bench",
            "id": "key",
            "openviking_key": "synthetic",
        }
        kernel = MemoryKernel(store, None)
        result["replay"] = {}
        for turns in (10, 100, 1000):
            messages = [
                m
                for i in range(turns)
                for m in (
                    {"role": "user", "content": f"question {i}"},
                    {"role": "assistant", "content": f"answer {i}"},
                )
            ]
            headers = {"x-openviking-session": str(turns)}
            body = {"model": "benchmark", "messages": messages}
            await kernel.prepare(body, "chat", headers, credential, {"id": "upstream"}, policy)
            with store.connect() as c:
                c.execute("BEGIN IMMEDIATE")
                c.executemany(
                    "INSERT OR IGNORE INTO state VALUES (?,?,?,1)",
                    (
                        (
                            digest("bench\0bench\0chat"),
                            f"tool:{turns}:{i}",
                            store.encode({"content": "old receipt"}),
                        )
                        for i in range(turns * 10)
                    ),
                )
                c.commit()
            times = []
            for _ in range(10):
                started = time.perf_counter()
                await kernel.prepare(body, "chat", headers, credential, {"id": "upstream"}, policy)
                times.append((time.perf_counter() - started) * 1000)
            result["replay"][str(turns)] = distribution(times)
        count = int(8_500_000 / len(orjson.dumps(cycle(1000))))
        dense = {
            "model": "benchmark",
            "messages": [m for i in range(count) for m in cycle(i)]
            + [{"role": "user", "content": "Summarize the changes."}],
        }
        raw = orjson.dumps(dense)
        result["tool_dense"] = {
            "bytes": len(raw),
            "messages": len(dense["messages"]),
            "tool_calls": count * 4,
        }
        for name, parse in (("parse", parse_body), ("orjson", orjson.loads)):
            times = []
            for _ in range(7):
                started = time.perf_counter()
                assert parse(raw)
                times.append((time.perf_counter() - started) * 1000)
            result["tool_dense"][name] = distribution(times)
        times = []
        for _ in range(7):
            started = time.perf_counter()
            await kernel.prepare(
                dense,
                "chat",
                {"x-openviking-session": "dense"},
                credential,
                {"id": "upstream"},
                policy,
            )
            times.append((time.perf_counter() - started) * 1000)
        result["tool_dense"]["prepare"] = distribution(times[1:])
        if hasattr(store, "close"):
            store.close()

        async def upstream(request):
            if request.path == "/health":
                return web.json_response(
                    {
                        "version": "0.4.16",
                        "auth_mode": "api_key",
                        "role": "user",
                        "account_id": "bench",
                        "user_id": "bench",
                    }
                )
            await request.read()
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
            )
            await asyncio.sleep(0.01)
            await response.write(
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
            )
            await response.write_eof()
            return response

        backend = web.Application()
        backend.router.add_route("*", "/{path:.*}", upstream)
        runner = web.AppRunner(backend)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, backlog=2048)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        os.environ["OVGW_BENCH_ENCRYPTION"] = encryption
        os.environ["OVGW_BENCH_ADMIN"] = "synthetic-benchmark-admin-token-000000"
        config = OpenVikingGatewayConfig(
            storage_path=directory,
            openviking_url=base,
            encryption_key_env="OVGW_BENCH_ENCRYPTION",
            admin_token_env="OVGW_BENCH_ADMIN",
        )
        app = create_app(config)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(2048)
        gateway_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            mgmt = app.state.management
            await mgmt.save("bench", "policies", "default", policy)
            await mgmt.save(
                "bench",
                "upstreams",
                "upstream",
                Upstream(
                    name="benchmark", protocol="chat", base_url=base, api_key="synthetic"
                ).model_dump(),
            )
            await mgmt.save(
                "bench",
                "keys",
                digest("synthetic"),
                {**credential, "policy_id": "default", "models": [], "upstream_ids": ["upstream"]},
            )
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=1024)) as http:

                async def measure(url, index):
                    started = time.perf_counter()
                    headers = {
                        "Authorization": "Bearer synthetic",
                        "X-OpenViking-Session": f"load-{index}",
                    }
                    body = {
                        "model": "benchmark",
                        "stream": True,
                        "messages": [{"role": "user", "content": f"question {index}"}],
                    }
                    async with http.post(
                        url + "/v1/chat/completions", json=body, headers=headers
                    ) as response:
                        if response.status != 200:
                            raise RuntimeError(f"benchmark HTTP {response.status}")
                        await response.content.readany()
                        first = (time.perf_counter() - started) * 1000
                        await response.read()
                    return first, (time.perf_counter() - started) * 1000

                async def settled(count):
                    # ASGI background bookkeeping is outside response latency.
                    # Drain warmup too, so it cannot contaminate the next burst.
                    while True:
                        with mgmt.connect() as c:
                            done = c.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0]
                        if done >= count:
                            return
                        await asyncio.sleep(0.01)

                await asyncio.gather(*(measure(gateway_url, i) for i in range(args.concurrency)))
                await settled(args.concurrency)
                await asyncio.gather(*(measure(base, i) for i in range(args.concurrency)))
                for name, url in (("direct_sse", base), ("gateway_sse", gateway_url)):
                    with RuntimeProfile(enabled=args.profile and name == "gateway_sse") as profile:
                        measured = await asyncio.gather(
                            *(measure(url, i) for i in range(args.concurrency))
                        )
                        if name == "gateway_sse":
                            await settled(args.concurrency * 2)
                        await profile.drain()
                    if name == "gateway_sse" and args.profile:
                        result["profile"] = profile.report()
                    result[name] = {
                        "first_byte": distribution([m[0] for m in measured]),
                        "complete": distribution([m[1] for m in measured]),
                    }
        finally:
            server.should_exit = True
            await task
            await runner.cleanup()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--profile", action="store_true", help="Profile only the warmed gateway burst"
    )
    arguments = parser.parse_args()
    report = json.dumps(asyncio.run(benchmark(arguments)), indent=2)
    print(report)
    if arguments.output:
        arguments.output.write_text(report + "\n")
