# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""``openviking-server ingest`` CLI: replay local agent-harness logs into OpenViking.

Commands:
  list-sources  show registered harnesses and their config
  status        show per-session ingest progress (read cursors)
  backfill      one-shot replay of existing logs
  watch         incremental, cursor-driven polling of new logs
  run           honor each harness's configured mode (backfill then watch)
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import List, Optional, Tuple

import typer

from openviking.ingest.cursor_store import CursorStore, SingleInstanceLock
from openviking.ingest.orchestrator import BackfillStats, IngestOrchestrator, enabled_sources
from openviking.ingest.poller import IngestPoller
from openviking.ingest.registry import SOURCE_REGISTRY
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.consts import DEFAULT_INGEST_STATE_DIR
from openviking_cli.utils.config.ingest_config import IngestConfig

logger = get_logger(__name__)

app = typer.Typer(
    add_completion=False,
    help="Replay local agent-harness conversation logs into OpenViking.",
)


def _load_ingest_config() -> IngestConfig:
    """Read the ``ingest`` section of ov.conf if present; else a default (env-driven).

    Only a *missing* config falls back to defaults; a malformed config surfaces its
    validation/JSON error instead of silently looking like "ingest not configured".
    """
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

    try:
        return OpenVikingConfigSingleton.get_instance().ingest
    except FileNotFoundError:
        return IngestConfig()


def _state_dir(config: IngestConfig) -> Path:
    return Path(config.state_dir).expanduser() if config.state_dir else DEFAULT_INGEST_STATE_DIR


def _make_store(config: IngestConfig) -> CursorStore:
    return CursorStore(_state_dir(config))


def _acquire_lock(config: IngestConfig) -> SingleInstanceLock:
    try:
        return SingleInstanceLock(_state_dir(config)).acquire()
    except RuntimeError as exc:
        typer.echo(f"error: {exc}")
        raise typer.Exit(1) from exc


async def _make_replayer(
    config: IngestConfig, store: CursorStore
) -> Tuple[ConversationReplayClient, SessionReplayer]:
    client = await ConversationReplayClient.create(
        url=config.server_url,
        api_key=config.api_key,
        account=config.account,
        user=config.user,
    )
    replayer = SessionReplayer(
        client,
        store,
        session_id_prefix=config.session_id_prefix,
        memory_policy=config.memory_policy or None,
    )
    return client, replayer


def _print_stats(results: dict[str, BackfillStats], dry_run: bool) -> None:
    label = "WOULD replay" if dry_run else "Replayed"
    total = BackfillStats()
    for name, s in results.items():
        typer.echo(
            f"  {name:12s}: {s.sessions} sessions, {label.lower()} {s.messages} messages, "
            f"{s.committed} committed, {s.skipped} skipped"
        )
        for err in s.errors:
            typer.echo(f"      ! {err}")
        total.merge(s)
    typer.echo(
        f"{label}: {total.sessions} sessions / {total.messages} messages / "
        f"{total.committed} commits" + (f" / {len(total.errors)} errors" if total.errors else "")
    )


# --------------------------------------------------------------------------- #
@app.command("list-sources")
def list_sources() -> None:
    """List registered harness adapters and their current config."""
    import openviking.ingest.sources  # noqa: F401 - populate the registry

    config = _load_ingest_config()
    typer.echo(f"ingest enabled: {config.enabled}   server_url: {config.server_url or '(default)'}")
    typer.echo("harnesses:")
    for name in sorted(SOURCE_REGISTRY):
        hc = config.harnesses.get(name)
        if hc is None:
            typer.echo(f"  {name:12s}  (not configured)")
        else:
            typer.echo(
                f"  {name:12s}  enabled={hc.enabled} mode={hc.mode} paths={hc.paths or '(default)'}"
            )


@app.command("status")
def status(
    harness: Optional[List[str]] = typer.Option(None, "--harness", "-H", help="Filter by harness"),
) -> None:
    """Show per-session ingest progress recorded in the cursor store."""
    config = _load_ingest_config()
    store = _make_store(config)
    try:
        names = harness or None
        records = []
        for h in names or sorted({r.harness for r in store.all_records()}):
            records.extend(store.all_records(h))
        if not records:
            typer.echo("No sessions ingested yet.")
            return
        typer.echo(f"{'harness':12s} {'session':24s} {'msgs':>6s}  committed_at")
        for rec in records:
            typer.echo(
                f"{rec.harness:12s} {rec.native_session_id[:24]:24s} "
                f"{rec.last_appended_count:6d}  {rec.last_committed_at or '-'}"
            )
    finally:
        store.close()


@app.command()
def backfill(
    harness: Optional[List[str]] = typer.Option(
        None, "--harness", "-H", help="Only these harnesses (default: all enabled)"
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Skip sessions started before this ISO date (e.g. 2026-06-01)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count sessions/messages; write nothing"),
    reset: bool = typer.Option(
        False, "--reset", help="Delete + recreate each OV session before replaying"
    ),
) -> None:
    """One-shot backfill of existing logs."""
    asyncio.run(_run_backfill(harness, since, dry_run, reset))


async def _run_backfill(harness, since, dry_run, reset) -> None:
    config = _load_ingest_config()
    store = _make_store(config)
    client = None
    lock = None
    try:
        only = harness or None
        if dry_run:
            # No server (and no lock) needed for a read-only dry run.
            replayer = SessionReplayer(None, store, session_id_prefix=config.session_id_prefix)  # type: ignore[arg-type]
            orch = IngestOrchestrator(config, replayer)
            results = await orch.backfill(only=only, since=since, dry_run=True)
        else:
            lock = _acquire_lock(config)
            client, replayer = await _make_replayer(config, store)
            orch = IngestOrchestrator(config, replayer)
            results = await orch.backfill(only=only, since=since, dry_run=False, reset=reset)
        _print_stats(results, dry_run)
    finally:
        if client is not None:
            await client.close()
        if lock is not None:
            lock.release()
        store.close()


@app.command()
def watch(
    harness: Optional[List[str]] = typer.Option(
        None, "--harness", "-H", help="Only these harnesses (default: all enabled)"
    ),
) -> None:
    """Incrementally watch for new/changed logs and replay them."""
    asyncio.run(_run_watch(harness))


@app.command()
def run() -> None:
    """Honor each harness's configured mode: backfill (mode in backfill/both) then watch."""
    asyncio.run(_run_all())


async def _run_all() -> None:
    config = _load_ingest_config()
    store = _make_store(config)
    lock = _acquire_lock(config)
    client, replayer = await _make_replayer(config, store)
    try:
        orch = IngestOrchestrator(config, replayer)
        results = await orch.backfill(only=None, since=None, dry_run=False)
        _print_stats(results, dry_run=False)
        watch_sources = [
            (n, c, s) for n, c, s in enabled_sources(config) if c.mode in ("watch", "both")
        ]
        if watch_sources:
            await _watch_loop(replayer, watch_sources)
    finally:
        await client.close()
        lock.release()
        store.close()


async def _run_watch(harness) -> None:
    config = _load_ingest_config()
    store = _make_store(config)
    lock = _acquire_lock(config)
    client, replayer = await _make_replayer(config, store)
    try:
        only = harness or None
        sources = [
            (n, c, s)
            for n, c, s in enabled_sources(config, only)
            if c.mode in ("watch", "both") or only is not None
        ]
        if not sources:
            typer.echo("No harnesses enabled for watch.")
            return
        await _watch_loop(replayer, sources)
    finally:
        await client.close()
        lock.release()
        store.close()


async def _watch_loop(replayer, sources) -> None:
    poller = IngestPoller(sources, replayer)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, poller.stop)
        except (NotImplementedError, RuntimeError):
            pass  # not supported on this platform
    await poller.run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
