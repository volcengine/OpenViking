# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One harness run through a real OpenViking server and the real gateway, without a model.

The OpenViking server runs on a local port with a fake embedder and VLM (as its server tests
do), so ``session_history`` is the real MCP tool over really stored sessions. The upstream is
the self-test's scripted looker. Data goes to a temporary directory.

    uv run --no-sync python -m benchmark.session_history.smoke_e2e [--arms history,grep-hint]
"""

import argparse
import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.app import create_app as create_server
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.config.vlm_config import VLMConfig
from openviking_gateway.app import create_app as create_gateway
from openviking_gateway.config import GatewayConfig

from .fakes import ScriptedModel, serve
from .run import Settings, markdown, run, write
from .scenario import build


class FakeEmbedder(DenseEmbedderBase):
    def __init__(self):
        super().__init__(model_name="fake-embedder")

    def embed(self, text, is_query=False):
        return EmbedResult(dense_vector=[0.1] * 1024)

    def get_dimension(self):
        return 1024


async def fake_completion(self, prompt, thinking=False, **kwargs):
    return "Fake summary."


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_openviking(workspace):
    EmbeddingConfig.get_embedder = lambda self: FakeEmbedder()
    VLMConfig.is_available = lambda self: True
    VLMConfig.get_completion_async = fake_completion
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "enable_watch_scheduler": False,
            "storage": {
                "workspace": str(workspace),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "fake-embedder",
                    "api_key": "unused",
                    "dimension": 1024,
                }
            },
        }
    )
    service = OpenVikingService(
        path=str(workspace), user=UserIdentifier.the_default_user("bench_user")
    )
    await service.initialize()
    service.viking_fs.query_embedder = FakeEmbedder()
    app = create_server(config=ServerConfig(root_api_key="bench-root-key"), service=service)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                if getattr(app.state, "api_key_manager", None) is not None:
                    break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("OpenViking server did not start")
    key = await app.state.api_key_manager.create_account("bench", "bench_user")
    context = RequestContext(user=UserIdentifier("bench", "bench_user"), role=Role.ADMIN)
    await service.initialize_account_directories(context)
    await service.initialize_user_directories(context)
    return server, thread, service, f"http://127.0.0.1:{port}", key


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arms", default="history,grep-hint,closed-book")
    parser.add_argument("--filler-turns", type=int, default=8)
    parser.add_argument("--context-window", type=int, default=8000)
    parser.add_argument("--settle-seconds", type=float, default=8)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as temp:
        server, thread, service, url, key = await start_openviking(Path(temp) / "ov")
        scenario = build(1, "history")
        model = ScriptedModel([*scenario.probes, scenario.gap_probe])
        runner, base = await serve(None, model)
        try:
            config = GatewayConfig(
                storage_path=str(Path(temp) / "gateway"),
                openviking_keys=True,
                default_upstreams={
                    "fake": {
                        "name": "fake",
                        "protocol": "chat",
                        "base_url": base + "/v1",
                        "api_key": "model-secret",
                        "models": ["looker", "guesser"],
                    }
                },
            )
            gateway = create_gateway(config, openviking_url=url)
            async with gateway.router.lifespan_context(gateway):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway, root_path="/gateway"),
                    base_url="http://gateway/gateway",
                    timeout=120,
                ) as http:
                    settings = Settings(
                        model="looker",
                        arms=[a for a in args.arms.split(",") if a],
                        seeds=[1],
                        salt="smoke",
                        filler_turns=args.filler_turns,
                        context_window=args.context_window,
                        settle_seconds=args.settle_seconds,
                    )
                    result = await run(settings, http, key, run_id="smoke")
        finally:
            await runner.cleanup()
            server.should_exit = True
            thread.join(timeout=10)
            await service.close()
    print(markdown(result))
    for item in result["runs"]:
        print(item["arm"], json.dumps(item["checks"]))
        for probe in item["probes"]:
            print(f"  {probe['kind']:12} correct={probe['correct']} tools={probe['tools']}")
            print("     ", probe["answer"][:160].replace("\n", " "))
    if args.output_dir:
        write(result, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
