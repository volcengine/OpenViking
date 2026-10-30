"""Shared SQLite admission for native extraction and AML adapter model calls.

Reservations are token estimates, never provider-reported usage. Both processes
share a deployment-owned database and optional live budget file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

_SQLITE_LOCAL_LOCK = threading.RLock()
_PID_ALIVE_CACHE = {}
DEFAULTS = {
    "source": {"concurrent": 40, "rpm": 240, "estimated_tpm": 4800000},
    "mini": {"concurrent": 48, "rpm": 600, "estimated_tpm": 6000000},
    "rerank": {"concurrent": 4, "rpm": 120, "estimated_tpm": 500000},
    "embedding": {"concurrent": 8, "rpm": 1200, "estimated_tpm": 1000000},
}


class Gate:
    def __init__(self, path=None, budgets=None, window=60.0, budget_file=None):
        configured = budget_file or os.environ.get("AML_MODEL_ADMISSION_BUDGETS")
        self.live_budget_file = Path(configured).expanduser() if configured else None
        selected = path or os.environ.get("AML_MODEL_ADMISSION_DB")
        if not selected:
            raise ValueError("Set AML_MODEL_ADMISSION_DB or pass a database path")
        self.path = Path(selected).expanduser()
        self.budgets = budgets or DEFAULTS
        self._validate_budgets(self.budgets)
        if self.live_budget_file:
            self._load_budgets()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.window = window
        with self.db() as d:
            d.executescript(
                "CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,kind TEXT,started REAL,pid INTEGER,reserved INTEGER,actual INTEGER,http_status INTEGER,phase TEXT);CREATE INDEX IF NOT EXISTS kind_started ON attempts(kind,started);CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY,kind TEXT,pid INTEGER);CREATE TABLE IF NOT EXISTS cooldown(kind TEXT PRIMARY KEY,until REAL);"
            )

    @staticmethod
    def _validate_budgets(budgets):
        if not isinstance(budgets, dict) or set(budgets) != set(DEFAULTS):
            raise ValueError("Admission budgets must define source, mini, rerank and embedding")
        for settings in budgets.values():
            if (
                not isinstance(settings, dict)
                or set(settings) != {"concurrent", "rpm", "estimated_tpm"}
                or any(type(value) is not int or value <= 0 for value in settings.values())
            ):
                raise ValueError("Admission limits must be positive integers")

    def _load_budgets(self):
        budgets = json.loads(self.live_budget_file.read_bytes())["budgets"]
        self._validate_budgets(budgets)
        return budgets

    @contextlib.contextmanager
    def db(self):
        # Serialize this process's open/transaction/close lifecycle. SQLite WAL close
        # and simultaneous opens were observed blocked on native inode mutexes.
        # Across processes SQLite remains the authority; budgets and bodies unchanged.
        with _SQLITE_LOCAL_LOCK:
            d = sqlite3.connect(self.path, timeout=30)
            d.execute("PRAGMA busy_timeout=30000")
            try:
                with d:
                    yield d
            finally:
                d.close()

    @staticmethod
    def alive(pid):
        # kill(pid, 0) also succeeds for unreaped zombie children. Such a process
        # cannot finish an HTTP call, so its original attempt stays unknown while its
        # lease must stop occupying concurrency. Cache ps for one second, not per call.
        stamp = time.monotonic()
        cached = _PID_ALIVE_CACHE.get(pid)
        if cached and stamp - cached[0] < 1:
            return cached[1]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        try:
            state = subprocess.run(
                ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            running = bool(state) and not state.startswith("Z")
        except (OSError, subprocess.TimeoutExpired):
            return True
        _PID_ALIVE_CACHE[pid] = (stamp, running)
        return running

    def try_acquire(self, kind, reserved, phase=""):
        assert kind in self.budgets and reserved >= 0
        now = time.time()
        budget = self.budgets[kind]
        if self.live_budget_file:
            budget = self._load_budgets()[kind]
        with self.db() as d:
            d.execute("BEGIN IMMEDIATE")
            for (pid,) in d.execute("SELECT DISTINCT pid FROM leases"):
                if not self.alive(pid):
                    d.execute("DELETE FROM leases WHERE pid=?", (pid,))
            c = d.execute("SELECT until FROM cooldown WHERE kind=?", (kind,)).fetchone()
            if c and c[0] > now:
                return None, min(2.0, c[0] - now)
            flight = d.execute("SELECT count(*) FROM leases WHERE kind=?", (kind,)).fetchone()[0]
            n, tokens = d.execute(
                "SELECT count(*),coalesce(sum(coalesce(actual,reserved)),0) FROM attempts WHERE kind=? AND started>?",
                (kind, now - self.window),
            ).fetchone()
            if flight >= budget["concurrent"] or n >= budget["rpm"]:
                return None, 0.25
            # A single exceptionally large request can run alone after an empty window.
            if n and tokens + reserved > budget["estimated_tpm"]:
                return None, 0.5
            ticket = uuid.uuid4().hex
            d.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
                (ticket, kind, now, os.getpid(), reserved, None, None, phase),
            )
            d.execute("INSERT INTO leases VALUES(?,?,?)", (ticket, kind, os.getpid()))
            return ticket, 0.0

    async def acquire(self, kind, reserved, phase=""):
        while True:
            # The DB transaction is short. Never occupy an executor thread while waiting.
            job = asyncio.create_task(asyncio.to_thread(self.try_acquire, kind, reserved, phase))
            try:
                ticket, delay = await asyncio.shield(job)
            except asyncio.CancelledError:
                ticket, _ = await job
                if ticket:
                    self.release(ticket)
                raise
            if ticket:
                return ticket
            await asyncio.sleep(delay)

    def acquire_sync(self, kind, reserved, phase=""):
        while True:
            ticket, delay = self.try_acquire(kind, reserved, phase)
            if ticket:
                return ticket
            time.sleep(delay)

    def release(self, ticket, status=None, actual=None, cooldown=0.0):
        with self.db() as d:
            d.execute("BEGIN IMMEDIATE")
            row = d.execute("SELECT kind FROM leases WHERE id=?", (ticket,)).fetchone()
            if not row:
                return
            kind = row[0]
            d.execute(
                "UPDATE attempts SET http_status=?,actual=? WHERE id=?", (status, actual, ticket)
            )
            d.execute("DELETE FROM leases WHERE id=?", (ticket,))
            if cooldown:
                d.execute(
                    "INSERT INTO cooldown VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET until=max(until,excluded.until)",
                    (kind, time.time() + cooldown),
                )

    def snapshot(self):
        with self.db() as d:
            return {
                "at": time.time(),
                "active": dict(d.execute("SELECT kind,count(*) FROM leases GROUP BY kind")),
                "http": list(
                    d.execute(
                        "SELECT kind,http_status,count(*) FROM attempts GROUP BY kind,http_status"
                    )
                ),
                "windows": list(
                    d.execute(
                        "SELECT kind,count(*),sum(coalesce(actual,reserved)) FROM attempts WHERE started>? GROUP BY kind",
                        (time.time() - self.window,),
                    )
                ),
            }


_TOKENIZER = None


def estimate(body, kind):
    global _TOKENIZER
    if _TOKENIZER is None:
        import tiktoken

        _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    # Proxy tokenizer with safety padding, explicitly not provider-reported tokens.
    text = json.dumps(
        {k: v for k, v in body.items() if k not in ("max_tokens", "max_completion_tokens")},
        ensure_ascii=False,
    )
    base = int(len(_TOKENIZER.encode(text, disallowed_special=())) * 1.35) + 64
    return base + (
        int(body.get("max_tokens", body.get("max_completion_tokens", 0)))
        if kind in ("source", "mini")
        else 0
    )


def install(source_model, embedding_model, native=False, phase="", database=None, budget_file=None):
    import httpx

    gate = Gate(database, budget_file=budget_file)
    original_async = httpx.AsyncClient.send
    original_sync = httpx.Client.send

    def classify(request):
        try:
            body = json.loads(request.content)
        except Exception:
            return None, None
        if not isinstance(body, dict):
            return None, None
        if "/rerank" in request.url.path:
            return "rerank", body
        if body.get("model") == source_model and "messages" in body:
            return "source", body
        if body.get("model") == "gpt-4o-mini-2024-07-18":
            return "mini", body
        if "embedding" in request.url.path and body.get("model") == embedding_model:
            return "embedding", body
        return None, None

    def outcome(response):
        try:
            b = response.json()
        except Exception:
            b = {}
        usage = b.get("usage") or {}
        actual = usage.get("total_tokens")
        text = json.dumps(b.get("error", {})) + json.dumps(b.get("code", ""))
        throttle = response.status_code == 429 or "-2004" in text
        try:
            pause = max(65.0, float(response.headers.get("retry-after", "65"))) if throttle else 0.0
        except ValueError:
            pause = 65.0
        return actual, pause, throttle

    async def wrapped(self, request, *a, **kw):
        kind, body = classify(request)
        if not kind:
            return await original_async(self, request, *a, **kw)
        reserved = estimate(body, kind)
        for attempt in range(8 if native else 1):
            ticket = await gate.acquire(kind, reserved, phase)
            status = actual = None
            pause = 0.0
            try:
                response = await original_async(self, request, *a, **kw)
                await response.aread()
                status = response.status_code
                actual, pause, throttle = outcome(response)
            finally:
                gate.release(ticket, status, actual, pause)
            if native and throttle and attempt < 7:
                await response.aclose()
                continue
            return response

    def wrapped_sync(self, request, *a, **kw):
        kind, body = classify(request)
        if not kind:
            return original_sync(self, request, *a, **kw)
        reserved = estimate(body, kind)
        for attempt in range(8 if native else 1):
            ticket = gate.acquire_sync(kind, reserved, phase)
            status = actual = None
            pause = 0.0
            try:
                response = original_sync(self, request, *a, **kw)
                response.read()
                status = response.status_code
                actual, pause, throttle = outcome(response)
            finally:
                gate.release(ticket, status, actual, pause)
            if native and throttle and attempt < 7:
                response.close()
                continue
            return response

    httpx.AsyncClient.send = wrapped
    httpx.Client.send = wrapped_sync
    return gate
