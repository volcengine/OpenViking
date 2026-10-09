"""Opt-in instrumentation for the gateway benchmark; no production hooks."""

import asyncio
import cProfile
import io
import pstats
import statistics
import time
from collections import defaultdict

from context_gateway.proxy import ProxyRequest
from context_gateway.storage import Database


def summary(values):
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(values), 2),
        "p95_ms": round(ordered[min(len(values) - 1, int(len(values) * 0.95))], 2),
    }


class RuntimeProfile:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.samples = defaultdict(list)
        self.profiles = []
        self.active = 0
        self.original = {}

    def __enter__(self):
        if not self.enabled:
            return self
        original = Database.run
        self.original[Database, "run"] = original

        async def run(db, fn, *args, **kwargs):
            queued = time.perf_counter()
            timing = []
            self.active += 1

            def measured(*inner):
                started = time.perf_counter()
                profile = cProfile.Profile()
                try:
                    return profile.runcall(fn, *inner)
                finally:
                    done = time.perf_counter()
                    timing.extend(((started - queued) * 1000, (done - started) * 1000, done))
                    self.profiles.append(profile)

            try:
                return await original(db, measured, *args, **kwargs)
            finally:
                self.active -= 1
                if timing:
                    name = type(db).__name__ + "." + fn.__qualname__
                    self.samples[name].append(
                        (*timing[:2], (time.perf_counter() - timing[2]) * 1000)
                    )

        Database.run = run
        for name in ("route", "prepare", "finish"):
            method = getattr(ProxyRequest, name)
            self.original[ProxyRequest, name] = method

            async def phase(request, method=method, name=name):
                self.active += 1
                started = time.perf_counter()
                try:
                    return await method(request)
                finally:
                    self.active -= 1
                    self.samples["phase." + name].append(
                        (0, (time.perf_counter() - started) * 1000, 0)
                    )

            setattr(ProxyRequest, name, phase)
        return self

    async def drain(self):
        while self.enabled and self.active:
            await asyncio.sleep(0.005)

    def __exit__(self, *_):
        for (cls, name), method in self.original.items():
            setattr(cls, name, method)

    def report(self):
        values = {
            name: {
                "calls": len(rows),
                "executor_wait": summary([r[0] for r in rows]),
                "work_or_phase": summary([r[1] for r in rows]),
                "loop_resume": summary([r[2] for r in rows]),
            }
            for name, rows in self.samples.items()
        }
        if self.profiles:
            out = io.StringIO()
            stats = pstats.Stats(self.profiles[0], stream=out)
            for profile in self.profiles[1:]:
                stats.add(profile)
            stats.strip_dirs().sort_stats("cumtime").print_stats(20)
            values["worker_cpu_profile"] = out.getvalue()
        return values
