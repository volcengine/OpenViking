# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Global test fixtures"""

collect_ignore = ["api_test", "oc2ov_test"]

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

_CONFIG_ENV_NAME = "OPENVIKING_CONFIG_FILE"
_CLI_CONFIG_ENV_NAME = "OPENVIKING_CLI_CONFIG_FILE"
_BOOTSTRAP_TMP = tempfile.TemporaryDirectory(prefix="openviking-root-config-")
_BOOTSTRAP_CONFIG_PATH = Path(_BOOTSTRAP_TMP.name) / "ov.conf"
_BOOTSTRAP_CLI_CONFIG_PATH = Path(_BOOTSTRAP_TMP.name) / "ovcli.conf"
_BOOTSTRAP_WORKSPACE = Path(_BOOTSTRAP_TMP.name) / "workspace"
_BOOTSTRAP_CONFIG_PATH.write_text(
    json.dumps(
        {
            "storage": {
                "workspace": str(_BOOTSTRAP_WORKSPACE),
                "agfs": {"backend": "local"},
                "vectordb": {"name": "test", "backend": "local", "project": "default"},
            },
            "embedding": {
                "dense": {"provider": "litellm", "model": "root-bootstrap", "dimension": 4}
            },
            "vlm": {"provider": None, "model": None},
        }
    ),
    encoding="utf-8",
)
_BOOTSTRAP_CONFIG_PATH.chmod(0o600)
os.environ[_CONFIG_ENV_NAME] = str(_BOOTSTRAP_CONFIG_PATH)
os.environ[_CLI_CONFIG_ENV_NAME] = str(_BOOTSTRAP_CLI_CONFIG_PATH)

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import set_task_tracker
from openviking.storage import viking_fs as viking_fs_module
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.config.vlm_config import VLMConfig
from tests.utils.mock_agfs import MockLocalAGFS


def pytest_collection_modifyitems(config, items):
    """Keep bot-marked tests in the standalone VikingBot harness."""
    del config
    bot_root = str(Path(__file__).resolve().parents[1] / "bot")
    configured_paths = {str(Path(entry).resolve()) for entry in sys.path if entry}
    if bot_root in configured_paths:
        return
    skip_bot = pytest.mark.skip(reason="run via the standalone bot pytest manifest")
    for item in items:
        if item.get_closest_marker("bot") is not None:
            item.add_marker(skip_bot)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_litellm_logging_worker():
    """Stop LiteLLM's process-global worker before its event loop closes."""
    yield
    worker_module = sys.modules.get("litellm.litellm_core_utils.logging_worker")
    worker = getattr(worker_module, "GLOBAL_LOGGING_WORKER", None)
    if worker is not None:
        await worker.stop()
        await worker.clear_queue()


@pytest.fixture
def offline_test_models(monkeypatch):
    """Use a deterministic embedder in direct-service tests."""

    class OfflineEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="root-offline-embedder", config={"provider": "test"})

        def embed(self, content, is_query: bool = False) -> EmbedResult:
            del content, is_query
            return EmbedResult(dense_vector=[0.0, 0.0, 0.0, 0.0])

        def get_dimension(self) -> int:
            return 4

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda _self: OfflineEmbedder())


# ── Workaround: local .so may lack AGFS_Grep symbol (new in latest source) ──
def _patch_agfs_grep_if_missing():
    """Wrap _setup_functions to catch missing AGFS_Grep and skip its binding."""
    try:
        from openviking.pyagfs.binding_client import BindingLib

        _orig_setup = BindingLib._setup_functions

        def _safe_setup(self):
            try:
                _orig_setup(self)
            except AttributeError as e:
                if "AGFS_Grep" not in str(e):
                    raise
                # Re-implement _setup_functions but skip AGFS_Grep lines.
                # We do this by temporarily removing the Grep lines from the
                # source, but since we can't edit .so, we monkey-patch the lib
                # object's __getattr__ to not fail on AGFS_Grep.
                import ctypes

                class _GrepStub:
                    """Fake ctypes function descriptor for AGFS_Grep."""

                    argtypes = [
                        ctypes.c_int64,
                        ctypes.c_char_p,
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                    ]
                    restype = ctypes.c_char_p

                    def __call__(self, *args):
                        return b'{"error":"AGFS_Grep not available in this .so version"}'

                # Patch at the CDLL instance level by overriding __getattr__
                orig_class = type(self.lib)
                orig_getattr = orig_class.__getattr__

                def patched_getattr(cdll_self, name):
                    if name == "AGFS_Grep":
                        return _GrepStub()
                    return orig_getattr(cdll_self, name)

                orig_class.__getattr__ = patched_getattr
                try:
                    _orig_setup(self)
                finally:
                    orig_class.__getattr__ = orig_getattr

        BindingLib._setup_functions = _safe_setup
    except Exception:
        pass


_patch_agfs_grep_if_missing()

@pytest.fixture(scope="session")
def event_loop():
    """Create session-level event loop"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def temp_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create pytest-owned storage isolated from other tests and workers."""
    root = tmp_path / "root"
    root.mkdir()
    yield root


@pytest.fixture(scope="function")
def test_data_dir(temp_dir: Path) -> Path:
    """Create test data directory"""
    data_dir = temp_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest_asyncio.fixture(scope="function")
async def root_openviking_config(
    test_data_dir: Path, monkeypatch
) -> AsyncGenerator[dict, None]:
    """Install a function-scoped offline config for embedded-client tests."""
    OpenVikingConfigSingleton.reset_instance()

    workspace = test_data_dir.resolve()
    config = {
        "default_account": "root-fixture",
        "default_user": "root-fixture",
        "storage": {
            "workspace": str(workspace),
            "agfs": {"backend": "local"},
            "vectordb": {"name": "test", "backend": "local", "project": "default"},
        },
        "embedding": {
            "dense": {"provider": "litellm", "model": "test-offline", "dimension": 4}
        },
        "vlm": {"provider": None, "model": None},
    }
    config_path = test_data_dir.parent / "ov.conf"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_path.chmod(0o600)
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    for env_name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENVIKING_EMBEDDING_API_KEY",
        "OPENVIKING_VLM_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    class RootFixtureEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="root-fixture-embedder", config={"provider": "test"})

        def embed(self, content, is_query: bool = False) -> EmbedResult:
            del content, is_query
            return EmbedResult(dense_vector=[0.0, 0.0, 0.0, 0.0])

        def get_dimension(self) -> int:
            return 4

    class RootFixtureVLM:
        model = "root-fixture-vlm"

        async def get_completion_async(self, prompt: str = "", **_kwargs) -> str:
            if "context query planner" in prompt.lower():
                return '{"queries": [], "reasoning": "offline test fixture"}'
            if "extract user-private configuration items" in prompt.lower():
                return '{"values": {"api_key": "secret-xyz", "base_url": "https://example.com"}}'
            return "# Root fixture summary"

        def get_completion(self, prompt: str = "", **_kwargs) -> str:
            if "context query planner" in prompt.lower():
                return '{"queries": [], "reasoning": "offline test fixture"}'
            if "extract user-private configuration items" in prompt.lower():
                return '{"values": {"api_key": "secret-xyz", "base_url": "https://example.com"}}'
            return "# Root fixture summary"

        async def get_vision_completion_async(self, *_args, **_kwargs) -> str:
            return "Root fixture image summary"

        def get_vision_completion(self, *_args, **_kwargs) -> str:
            return "Root fixture image summary"

    offline_vlm = RootFixtureVLM()

    async def fake_completion(*args, **kwargs) -> str:
        prompt = str(args[1] if len(args) > 1 else kwargs.get("prompt", ""))
        forwarded_kwargs = dict(kwargs)
        forwarded_kwargs.pop("prompt", None)
        return await offline_vlm.get_completion_async(prompt, **forwarded_kwargs)

    async def fake_vision_completion(*_args, **_kwargs) -> str:
        return "Root fixture image summary"

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda _self: RootFixtureEmbedder())
    monkeypatch.setattr(VLMConfig, "is_available", lambda _self: True)
    monkeypatch.setattr(VLMConfig, "get_completion_async", fake_completion)
    monkeypatch.setattr(VLMConfig, "get_vision_completion_async", fake_vision_completion)
    monkeypatch.setattr(VLMConfig, "get_vlm_instance", lambda _self: offline_vlm)

    try:
        OpenVikingConfigSingleton.initialize(config_dict=config)
        yield config
    finally:
        OpenVikingConfigSingleton.reset_instance()


# ============ Service Fixtures ============


@pytest_asyncio.fixture(scope="function")
async def service(
    test_data_dir: Path,
    monkeypatch,
) -> AsyncGenerator[OpenVikingService, None]:
    """Create an initialized service for domain-level tests."""

    previous_viking_fs = viking_fs_module._instance

    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test-fake-embedder")

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            return EmbedResult(dense_vector=[0.1] * 1024)

        def get_dimension(self) -> int:
            return 1024

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())
    mock_agfs = MockLocalAGFS(root_path=test_data_dir / "mock_agfs_root")
    monkeypatch.setattr(
        "openviking.utils.agfs_utils.create_agfs_client",
        lambda *args, **kwargs: mock_agfs,
    )
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {
                "workspace": str(test_data_dir),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 1024,
                }
            },
        }
    )
    instance = OpenVikingService(
        path=str(test_data_dir),
        user=UserIdentifier.the_default_user(),
    )
    try:
        await instance.initialize()
        yield instance
    finally:
        await instance.close()
        set_task_tracker(None)
        viking_fs_module._instance = previous_viking_fs
        OpenVikingConfigSingleton.reset_instance()


@pytest.fixture(scope="function")
def request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role.USER,
    )
