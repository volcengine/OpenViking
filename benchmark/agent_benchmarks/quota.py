"""Retry Ark quota responses with waits, preserving the exact same request."""

import asyncio
import importlib
import json
import os
import time

import httpx


def install_quota_backoff(output, client_modules=None):
    """Client-level retries also cover SDK-specific transports and proxies."""

    def audit_request(request):
        target = output / "request-controls.json"
        if request.url.host != "ark.cn-beijing.volces.com" or target.exists():
            return
        try:
            body = json.loads(request.content)
        except (ValueError, RuntimeError):
            return
        target.write_text(
            json.dumps(
                {
                    k: body.get(k)
                    for k in ("model", "thinking", "reasoning_effort", "temperature", "max_tokens")
                },
                indent=2,
            )
        )

    def delay_for(response, attempt):
        try:
            retry_after = float(response.headers.get("retry-after", "0"))
        except ValueError:
            retry_after = 0
        return min(60, max(retry_after, 15 * 2 ** min(attempt, 2) + os.getpid() % 5))

    def log(response, attempt, delay):
        with (output / "quota-waits.jsonl").open("a") as file:
            file.write(
                json.dumps(
                    {
                        "at": time.time(),
                        "http_status": response.status_code,
                        "retry": attempt + 1,
                        "wait_seconds": delay,
                    }
                )
                + "\n"
            )

    def patch(client_module):
        original_sync = client_module.Client.send
        original_async = client_module.AsyncClient.send

        def sync(self, request, *args, **kwargs):
            audit_request(request)
            for attempt in range(9):
                response = original_sync(self, request, *args, **kwargs)
                if (
                    request.url.host != "ark.cn-beijing.volces.com"
                    or response.status_code != 429
                    or attempt == 8
                ):
                    return response
                delay = delay_for(response, attempt)
                log(response, attempt, delay)
                response.read()
                response.close()
                time.sleep(delay)

        async def asynchronous(self, request, *args, **kwargs):
            audit_request(request)
            for attempt in range(9):
                response = await original_async(self, request, *args, **kwargs)
                if (
                    request.url.host != "ark.cn-beijing.volces.com"
                    or response.status_code != 429
                    or attempt == 8
                ):
                    return response
                delay = delay_for(response, attempt)
                log(response, attempt, delay)
                await response.aread()
                await response.aclose()
                await asyncio.sleep(delay)

        client_module.Client.send = sync
        client_module.AsyncClient.send = asynchronous

    if client_modules is None:
        client_modules = [httpx]
        try:
            client_modules.append(importlib.import_module("httpx2"))
        except ImportError:
            pass
    seen = set()
    for module in client_modules:
        if id(module.Client) not in seen:
            seen.add(id(module.Client))
            patch(module)
