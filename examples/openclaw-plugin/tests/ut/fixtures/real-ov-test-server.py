"""Start a small real OpenViking HTTP server for plugin integration tests.

The server uses the real OpenViking service and HTTP routes, but patches external
model dependencies and AGFS with in-repo fakes so the test stays local.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import uvicorn

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.service.core import OpenVikingService
from openviking.storage.transaction import reset_lock_manager
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.config.vlm_config import VLMConfig
from tests.utils.mock_agfs import MockLocalAGFS

import openviking.utils.agfs_utils as agfs_utils


class FakeEmbedder(DenseEmbedderBase):
    def __init__(self) -> None:
        super().__init__(model_name="test-fake-embedder")

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        return EmbedResult(dense_vector=[0.1] * 2048)

    def embed_batch(self, texts: list[str], is_query: bool = False) -> list[EmbedResult]:
        return [self.embed(text, is_query=is_query) for text in texts]

    def get_dimension(self) -> int:
        return 2048


async def _fake_completion(self, prompt, thinking=False):  # noqa: ANN001, ANN202
    return "# Test Summary\n\nFake summary for local plugin integration testing."


async def _fake_vision_completion(self, prompt, images, thinking=False):  # noqa: ANN001, ANN202
    return "Fake image description for local plugin integration testing."


def _install_fakes(data_dir: Path) -> None:
    EmbeddingConfig.get_embedder = lambda self: FakeEmbedder()  # type: ignore[method-assign]
    VLMConfig.is_available = lambda self: True  # type: ignore[method-assign]
    VLMConfig.get_completion_async = _fake_completion  # type: ignore[method-assign]
    VLMConfig.get_vision_completion_async = _fake_vision_completion  # type: ignore[method-assign]
    agfs_utils.create_agfs_client = lambda config: MockLocalAGFS(  # type: ignore[assignment]
        root_path=data_dir / "mock_agfs_root",
    )


def _write_config(data_dir: Path) -> Path:
    config_path = data_dir / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(data_dir / "workspace"),
                    "agfs": {"backend": "local"},
                    "vectordb": {"backend": "local", "dimension": 2048},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "api_base": "http://127.0.0.1:9/v1",
                        "model": "fake",
                        "dimension": 2048,
                    },
                },
                "vlm": {
                    "provider": "openai",
                    "api_base": "http://127.0.0.1:9/v1",
                    "api_key": "fake",
                    "model": "fake",
                },
                "encryption": {"enabled": False},
            },
        ),
        encoding="utf-8",
    )
    return config_path


async def _wait_for_shutdown() -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, sys.stdin.readline)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    reset_lock_manager()
    _install_fakes(data_dir)
    os.environ[OPENVIKING_CONFIG_ENV] = str(_write_config(data_dir))
    OpenVikingConfigSingleton.reset_instance()

    service = OpenVikingService(
        path=str(data_dir / "data"),
        user=UserIdentifier.the_default_user("real_dialogue_test"),
    )
    await service.initialize()
    service.viking_fs.query_embedder = FakeEmbedder()

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(
                config=ServerConfig(host="127.0.0.1", port=args.port),
                service=service,
            ),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        ),
    )
    server_task = asyncio.create_task(server.serve())

    try:
        while not server.started:
            await asyncio.sleep(0.05)
        print(
            "OPENVIKING_TEST_SERVER_READY "
            + json.dumps({"port": args.port, "data_dir": str(data_dir)}),
            flush=True,
        )
        await _wait_for_shutdown()
    finally:
        server.should_exit = True
        await server_task
        await service.close()
        reset_lock_manager()
        OpenVikingConfigSingleton.reset_instance()


if __name__ == "__main__":
    asyncio.run(main())
