# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Run server roles without constructing an HTTP application or binding a port."""

import asyncio
import multiprocessing
import signal
from multiprocessing.connection import wait

from openviking.server.config import ServerConfig, validate_server_config
from openviking.server.runtime import ServerRuntime, configure_session_runtime
from openviking.service.core import OpenVikingService
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.logger import (
    configure_uvicorn_logging,
    init_otel_log_handler_from_server_config,
)


async def serve(config: ServerConfig) -> None:
    from openviking.metrics.global_api import (
        init_metrics_from_server_config,
        shutdown_metrics_async,
    )
    from openviking.server.app import _configure_default_executor
    from openviking.server.dependencies import set_server_config, set_service
    from openviking.telemetry import tracer_module
    from openviking.usage_reporter.config import build_usage_reporter

    validate_server_config(config)
    _configure_default_executor(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous_handlers:
        loop.add_signal_handler(sig, stop.set)
    service = None
    runtime = None
    reporter = None
    try:
        service = OpenVikingService()
        set_server_config(config)
        set_service(service)
        reporter = build_usage_reporter(config.usage_reporter)
        configure_session_runtime(service, config, reporter)
        runtime = ServerRuntime(service, config)
        init_metrics_from_server_config(config, service=service)
        if config.observability.metrics.enabled:
            from openviking.metrics.core.runtime import install_executor_monitor

            install_executor_monitor()
        tracer_module.init_tracer_from_server_config(config)
        init_otel_log_handler_from_server_config(config)
        await runtime.start()
        print(f"OpenViking roles ready: {','.join(config.roles)}", flush=True)
        await stop.wait()
    finally:
        try:
            if runtime is not None:
                await runtime.stop()
            elif service is not None:
                await service.close()
            await shutdown_metrics_async()
            if config.observability.metrics.enabled:
                from openviking.metrics.core.runtime import uninstall_executor_monitor

                uninstall_executor_monitor()
            if reporter is not None:
                await reporter.close()
        finally:
            for sig, handler in previous_handlers.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, handler)


def _worker(config_data: dict, config_path: str | None) -> None:
    OpenVikingConfigSingleton.initialize(config_path=config_path)
    configure_uvicorn_logging()
    asyncio.run(serve(ServerConfig.model_validate(config_data)))


def run_headless(config: ServerConfig, *, config_path: str | None) -> None:
    if "api" in config.roles:
        raise ValueError("Headless entry point cannot run the api role")
    if config.workers == 1:
        asyncio.run(serve(config))
        return
    context = multiprocessing.get_context("spawn")
    processes = []
    try:
        for index in range(config.workers):
            process = context.Process(
                target=_worker,
                args=(config.model_dump(mode="json"), config_path),
                name=f"openviking-role-worker-{index + 1}",
            )
            process.start()
            processes.append(process)
        # Any child exit, including a clean early exit, ends this deployment.
        ready = wait([process.sentinel for process in processes])
        exited = next(process for process in processes if process.sentinel in ready)
        exited.join()
        raise RuntimeError(f"Server role process {exited.pid} exited (code {exited.exitcode})")
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=60)
            if process.is_alive():
                process.kill()
                process.join()
