# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Internal distributed expiry publisher process."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Internal OpenViking expiry publisher")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--startup-status", required=True, type=Path)
    return parser.parse_args(argv)


def load_expiry_config(path: Path):
    from openviking.server.config import TTLAdapterConfig
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    raw = json.loads(os.path.expandvars(path.expanduser().read_text(encoding="utf-8-sig")))
    config = OpenVikingConfig.from_dict(raw)
    ttl = TTLAdapterConfig.model_validate(raw.get("server", {}).get("ttl_adapter") or {})
    if not (config.uses_redis_expiry() or ttl.writer_enabled):
        raise ValueError(
            "expiry publisher requires memory.session_auto_commit.idle_backend='redis' "
            "or compile_api.scheduler_enabled=true or an enabled TTL writer (Redis)"
        )
    config.expiry.validate_for_runtime()
    if not config.expiry.publisher_separate_process:
        raise ValueError("expiry publisher process is disabled by configuration")
    return config.expiry


def write_startup_status(path: Path, status: str, error: str | None = None) -> None:
    payload: dict[str, object] = {"pid": os.getpid(), "status": status}
    if error:
        payload["error"] = error
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            signal.signal(signum, lambda *_args: loop.call_soon_threadsafe(stop.set))


async def run_publisher(config, stop: asyncio.Event, startup_status: Path) -> None:
    from openviking.scheduling.expiry.runtime import ExpiryRuntime, ExpiryRuntimeRoles

    runtime = ExpiryRuntime(config, roles=ExpiryRuntimeRoles(publish=True))
    wait_task: asyncio.Task[None] | None = None
    stop_task: asyncio.Task[bool] | None = None
    try:
        await runtime.start_storage()
        await runtime.start_roles()
        write_startup_status(startup_status, "ready")
        wait_task = asyncio.create_task(runtime.wait())
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait(
                {wait_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if wait_task in done:
                await wait_task
        finally:
            stop.set()
            await runtime.close()
    finally:
        if wait_task is not None:
            if not wait_task.done():
                wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if stop_task is not None:
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
        if wait_task is None:
            await runtime.close()


def main(argv: list[str] | None = None) -> int:
    startup_status: Path | None = None
    try:
        args = parse_args(argv)
        current_startup_status: Path = args.startup_status
        startup_status = current_startup_status
        try:
            config = load_expiry_config(args.config)
        except ValueError as exc:
            # Validation errors are already safely formatted. Avoid emitting
            # chained validation tracebacks containing raw configuration input.
            write_startup_status(current_startup_status, "failed", str(exc))
            logging.error("Expiry publisher configuration failed: %s", exc)
            return 1
        write_startup_status(current_startup_status, "starting")

        async def runner() -> None:
            stop = asyncio.Event()
            install_signal_handlers(stop)
            await run_publisher(config, stop, current_startup_status)

        asyncio.run(runner())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        if startup_status is not None:
            try:
                write_startup_status(startup_status, "failed", str(exc))
            except Exception:
                pass
        logging.exception("Expiry publisher failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
