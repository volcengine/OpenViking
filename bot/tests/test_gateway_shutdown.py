# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""A SIGTERM-requested stop is a clean exit, not a gateway failure."""

import asyncio
import os
import signal
from types import SimpleNamespace

import pytest
import typer
from vikingbot.cli import commands

posix_only = pytest.mark.skipif(os.name == "nt", reason="SIGTERM handling is POSIX-only")


async def _noop(*_args, **_kwargs):
    pass


def _run_gateway(monkeypatch, runtime_start=_noop, cleanup_all=_noop, on_ready=None):
    config = SimpleNamespace(
        gateway=SimpleNamespace(host="127.0.0.1", port=0, token=""),
        bot_data_path="unused",
        sandbox=None,
    )
    stub = SimpleNamespace(
        start=_noop,
        start_all=_noop,
        run=_noop,
        close_mcp=_noop,
        sandbox_manager=SimpleNamespace(cleanup_all=cleanup_all),
    )
    reported = []

    def report_startup(status, **_kwargs):
        reported.append(status)
        if status == "ready" and on_ready is not None:
            on_ready()

    for name in ("validate_openviking_auth", "_abort_if_port_in_use", "_init_bot_data"):
        monkeypatch.setattr(commands, name, lambda *a, **k: None)
    monkeypatch.setattr(commands, "ensure_config", lambda _: config)
    monkeypatch.setattr(commands, "SessionManager", lambda _: stub)
    monkeypatch.setattr(commands, "prepare_cron", lambda *a, **k: None)
    monkeypatch.setattr(commands, "prepare_agent_loop", lambda *a, **k: stub)
    monkeypatch.setattr(commands, "prepare_channel", lambda *a, **k: stub)
    monkeypatch.setattr(commands, "prepare_heartbeat", lambda *a, **k: stub)
    monkeypatch.setattr("vikingbot.compile.service.BotCompileService", lambda **_: stub)
    monkeypatch.setattr("vikingbot.utils.startup.report_startup", report_startup)

    class Runtime:
        def __init__(self, _config):
            pass

        async def start(self, _manager):
            await runtime_start()

        async def stop(self):
            pass

    monkeypatch.setattr("vikingbot.sandbox.runtime.OpenSandboxRuntime", Runtime)
    # A Python handler, so a SIGTERM the gateway fails to catch cannot kill the test run.
    sentinel = signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        installed = signal.getsignal(signal.SIGTERM)
        commands.gateway(port=None, host=None, verbose=False, config_path=None)
        assert signal.getsignal(signal.SIGTERM) is installed
    finally:
        signal.signal(signal.SIGTERM, sentinel)
    return reported


@posix_only
def test_sigterm_while_serving_exits_cleanly(monkeypatch):
    # uvicorn captures the signal, shuts down, then re-raises it to the gateway's handler.
    reported = _run_gateway(monkeypatch, on_ready=lambda: signal.raise_signal(signal.SIGTERM))
    assert reported == ["starting", "ready"]  # no "failed", no Exit(1)


@posix_only
def test_sigterm_during_startup_exits_cleanly(monkeypatch):
    async def start():
        signal.raise_signal(signal.SIGTERM)
        await asyncio.sleep(5)  # cancelled by the SIGTERM handler

    assert _run_gateway(monkeypatch, runtime_start=start) == ["starting"]


@posix_only
def test_second_sigterm_does_not_interrupt_cleanup(monkeypatch):
    cleaned = []

    async def start():
        signal.raise_signal(signal.SIGTERM)
        await asyncio.sleep(5)

    async def cleanup_all():
        signal.raise_signal(signal.SIGTERM)  # e.g. the server's terminate() after a group kill
        await asyncio.sleep(0.05)
        cleaned.append(True)

    _run_gateway(monkeypatch, runtime_start=start, cleanup_all=cleanup_all)
    assert cleaned == [True]


def test_real_failure_still_exits_nonzero(monkeypatch):
    async def start():
        raise RuntimeError("sandbox broke")

    with pytest.raises(typer.Exit) as exc:
        _run_gateway(monkeypatch, runtime_start=start)
    assert exc.value.exit_code == 1


def test_unrequested_cancellation_still_fails(monkeypatch):
    async def start():
        asyncio.current_task().cancel()
        await asyncio.sleep(5)

    with pytest.raises(typer.Exit) as exc:
        _run_gateway(monkeypatch, runtime_start=start)
    assert exc.value.exit_code == 1
