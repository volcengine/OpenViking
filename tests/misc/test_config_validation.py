#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Test if config validators work correctly"""

import sys
from pathlib import Path

import pytest

from openviking.utils.agfs_utils import _generate_plugin_config, mount_agfs_backend
from openviking_cli.utils.config.agfs_config import AGFSConfig, S3Config
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig, VolcengineConfig
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_agfs_validation():
    """Test AGFS config validation"""
    print("=" * 60)
    print("Test AGFS config validation")
    print("=" * 60)

    # Test 1: local backend missing path (should use default)
    print("\n1. Test local backend (use default path)...")
    try:
        config = AGFSConfig(backend="local")
        print(f"   Pass (path={config.path})")
    except ValueError as e:
        print(f"   Fail: {e}")


def test_agfs_s3_normalize_encoding_chars_defaults_to_target_set():
    config = AGFSConfig(
        backend="s3",
        s3=S3Config(
            bucket="my-bucket",
            region="us-west-1",
            access_key="fake-access-key-for-testing",
            secret_key="fake-secret-key-for-testing-12345",
            endpoint="https://s3.amazonaws.com",
        ),
    )

    assert config.s3.normalize_encoding_chars == "?#%+@"


def test_agfs_s3_normalize_encoding_chars_is_forwarded_to_ragfs_plugin_config():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="s3",
        s3=S3Config(
            bucket="my-bucket",
            region="us-west-1",
            access_key="fake-access-key-for-testing",
            secret_key="fake-secret-key-for-testing-12345",
            endpoint="https://s3.amazonaws.com",
            normalize_encoding_chars="?#",
        ),
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["s3fs"]["config"]["normalize_encoding_chars"] == "?#"

    # Test 2: invalid backend
    print("\n2. Test invalid backend...")
    try:
        config = AGFSConfig(backend="invalid")
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

    # Test 3: S3 backend missing required fields
    print("\n3. Test S3 backend missing required fields...")
    try:
        config = AGFSConfig(backend="s3")
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

    # Test 4: S3 backend complete config
    print("\n4. Test S3 backend complete config...")
    try:
        config = AGFSConfig(
            backend="s3",
            s3=S3Config(
                bucket="my-bucket",
                region="us-west-1",
                access_key="fake-access-key-for-testing",
                secret_key="fake-secret-key-for-testing-12345",
                endpoint="https://s3.amazonaws.com",
            ),
        )
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")


def test_agfs_queuefs_defaults_to_sqlite_backend():
    config = AGFSConfig(path="/tmp/ov-test", backend="local")

    assert config.queuefs.mode == "shared"
    assert config.queuefs.backend == "sqlite"
    assert config.queuefs.recover_stale_sec == 0
    assert config.queuefs.busy_timeout_ms == 5000


def test_agfs_queuefs_accepts_memory_backend():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
        queuefs={"mode": "worker", "backend": "memory"},
    )

    assert config.queuefs.mode == "worker"
    assert config.queuefs.backend == "memory"


def test_agfs_queuefs_rejects_legacy_process_mode():
    with pytest.raises(ValueError, match="queuefs mode"):
        AGFSConfig(
            path="/tmp/ov-test",
            backend="local",
            queuefs={"mode": "process"},
        )


def test_agfs_queuefs_rejects_invalid_backend():
    with pytest.raises(ValueError, match="queuefs"):
        AGFSConfig(
            path="/tmp/ov-test",
            backend="local",
            queuefs={"backend": "bogus"},
        )


def test_agfs_queuefs_rejects_negative_timeouts():
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        AGFSConfig(
            path="/tmp/ov-test",
            backend="local",
            queuefs={"busy_timeout_ms": -1},
        )

    with pytest.raises(ValueError, match="recover_stale_sec"):
        AGFSConfig(
            path="/tmp/ov-test",
            backend="local",
            queuefs={"recover_stale_sec": -1},
        )


def test_generate_plugin_config_uses_queuefs_memory_backend_without_db_path():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
        queuefs={"backend": "memory"},
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["queuefs"]["config"]["backend"] == "memory"
    assert "db_path" not in plugins["queuefs"]["config"]


def test_generate_plugin_config_uses_new_queuefs_db_path_over_legacy_field():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
        queue_db_path="/tmp/legacy-queue.db",
        queuefs={"backend": "sqlite", "db_path": "/tmp/new-queue.db"},
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["queuefs"]["config"]["backend"] == "sqlite"
    assert plugins["queuefs"]["config"]["db_path"] == str(Path("/tmp/new-queue.db").resolve())


def test_generate_plugin_config_supports_legacy_queue_db_path():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
        queue_db_path="/tmp/legacy-queue.db",
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["queuefs"]["config"]["backend"] == "sqlite"
    assert plugins["queuefs"]["config"]["db_path"] == str(Path("/tmp/legacy-queue.db").resolve())


def test_generate_plugin_config_uses_workspace_default_queue_db_path():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["queuefs"]["config"]["backend"] == "sqlite"
    assert plugins["queuefs"]["config"]["db_path"] == "/tmp/ov-test/_system/queue/queue.db"


def test_generate_plugin_config_forwards_queuefs_runtime_options():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
        queuefs={
            "backend": "sqlite3",
            "recover_stale_sec": 17,
            "busy_timeout_ms": 1234,
        },
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["queuefs"]["config"]["backend"] == "sqlite3"
    assert plugins["queuefs"]["config"]["recover_stale_sec"] == 17
    assert plugins["queuefs"]["config"]["busy_timeout_ms"] == 1234


def test_generate_plugin_config_ignores_db_paths_in_memory_mode():
    config = AGFSConfig(
        path="/tmp/ov-test",
        backend="local",
        queue_db_path="/tmp/legacy-queue.db",
        queuefs={"backend": "memory", "db_path": "/tmp/new-queue.db"},
    )

    plugins = _generate_plugin_config(config, Path("/tmp/ov-test"))

    assert plugins["queuefs"]["config"]["backend"] == "memory"
    assert "db_path" not in plugins["queuefs"]["config"]


class _FakeMountClient:
    def __init__(self):
        self.mount_calls = []

    def mount(self, plugin_name, mount_path, config):
        self.mount_calls.append((plugin_name, mount_path, config))

    def unmount(self, _mount_path):
        return None


def test_mount_agfs_backend_skips_queue_sqlite_dirs_for_memory_backend(tmp_path):
    config = AGFSConfig(
        path=str(tmp_path),
        backend="local",
        queuefs={"backend": "memory"},
    )
    client = _FakeMountClient()

    mount_agfs_backend(client, config)

    assert (tmp_path / "viking").exists()
    assert not (tmp_path / "_system" / "queue").exists()
    queuefs_mount = next(call for call in client.mount_calls if call[0] == "queuefs")
    assert queuefs_mount[2]["backend"] == "memory"
    assert "db_path" not in queuefs_mount[2]


def test_mount_agfs_backend_creates_queue_sqlite_dirs_for_sqlite_backend(tmp_path):
    queue_db_path = tmp_path / "custom-queue" / "queue.db"
    config = AGFSConfig(
        path=str(tmp_path),
        backend="local",
        queuefs={"backend": "sqlite", "db_path": str(queue_db_path)},
    )
    client = _FakeMountClient()

    mount_agfs_backend(client, config)

    assert (tmp_path / "viking").exists()
    assert queue_db_path.parent.exists()
    queuefs_mount = next(call for call in client.mount_calls if call[0] == "queuefs")
    assert queuefs_mount[2]["backend"] == "sqlite"
    assert queuefs_mount[2]["db_path"] == str(queue_db_path.resolve())


def test_vectordb_validation():
    """Test VectorDB config validation"""
    print("\n" + "=" * 60)
    print("Test VectorDB config validation")
    print("=" * 60)

    # Test 1: local backend missing path
    print("\n1. Test local backend missing path...")
    try:
        _ = VectorDBBackendConfig(backend="local", path=None)
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

    # Test 2: http backend missing url
    print("\n2. Test http backend missing url...")
    try:
        _ = VectorDBBackendConfig(backend="http", url=None)
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

    # Test 3: volcengine backend complete config
    print("\n3. Test volcengine backend complete config...")
    try:
        _ = VectorDBBackendConfig(
            backend="volcengine",
            volcengine=VolcengineConfig(ak="test_ak", sk="test_sk", region="cn-beijing"),
        )
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 4: volcengine backend with api_key complete config
    print("\n4. Test volcengine backend with api_key complete config...")
    try:
        _ = VectorDBBackendConfig(
            backend="volcengine",
            volcengine=VolcengineConfig(
                api_key="vk-test-token",
                host="api-vikingdb.vikingdb.cn-beijing.volces.com",
            ),
        )
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")


def test_vectordb_volcengine_validation_accepts_api_key_without_ak_sk():
    config = VectorDBBackendConfig(
        backend="volcengine",
        volcengine=VolcengineConfig(
            api_key="vk-test-token",
            host="api-vikingdb.vikingdb.cn-beijing.volces.com",
        ),
    )

    assert config.backend == "volcengine"
    assert config.volcengine is not None
    assert config.volcengine.api_key == "vk-test-token"
    assert config.volcengine.host == "api-vikingdb.vikingdb.cn-beijing.volces.com"


def test_vectordb_volcengine_without_api_key_still_requires_ak_sk():
    try:
        VectorDBBackendConfig(
            backend="volcengine",
            volcengine=VolcengineConfig(host="api-vikingdb.vikingdb.cn-beijing.volces.com"),
        )
        raise AssertionError("Expected ValueError for missing ak/sk")
    except ValueError as e:
        assert "ak" in str(e)


def test_removed_volcengine_api_key_backend_name_is_rejected():
    try:
        VectorDBBackendConfig(
            backend="volcengine_api_key",
        )
        raise AssertionError("Expected ValueError for removed backend name")
    except ValueError as e:
        assert "volcengine_api_key" in str(e)


def test_vectordb_volcengine_api_key_auth_requires_host_or_region():
    try:
        VectorDBBackendConfig(
            backend="volcengine",
            volcengine=VolcengineConfig(api_key="vk-test-token"),
        )
        raise AssertionError("Expected ValueError for missing host/region in api_key mode")
    except ValueError as e:
        assert "host' or 'region" in str(e)


def test_vectordb_index_name_defaults_and_overrides():
    default_config = VectorDBBackendConfig()
    assert default_config.index_name == "default"

    custom_config = VectorDBBackendConfig(index_name="context_idx")
    assert custom_config.index_name == "context_idx"


def test_embedding_validation():
    """Test Embedding config validation"""
    print("\n" + "=" * 60)
    print("Test Embedding config validation")
    print("=" * 60)

    # Test 1: no embedder config -> default local dense
    print("\n1. Test no embedder config...")
    try:
        config = EmbeddingConfig()
        assert config.dense is not None
        print(
            f"   Pass (default provider={config.dense.provider}, model={config.dense.model}, dim={config.dimension})"
        )
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 2: OpenAI provider missing api_key
    print("\n2. Test OpenAI provider missing api_key...")
    try:
        _ = EmbeddingConfig(
            dense=EmbeddingModelConfig(provider="openai", model="text-embedding-3-small")
        )
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

    # Test 3: OpenAI provider complete config
    print("\n3. Test OpenAI provider complete config...")
    try:
        _ = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="fake-api-key-for-testing",
                dimension=1536,
            )
        )
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 4: Embedding Provider/Backend sync
    print("\n4. Test Embedding Provider/Backend sync...")
    # Case A: Only backend provided -> provider should be synced
    config_a = EmbeddingModelConfig(
        backend="openai", model="text-embedding-3-small", api_key="test-key", dimension=1536
    )
    if config_a.provider == "openai":
        print("   Pass (backend='openai' -> provider='openai')")
    else:
        print(f"   Fail (backend='openai' -> provider='{config_a.provider}')")

    # Case B: Both provided -> provider takes precedence
    config_b = EmbeddingModelConfig(
        provider="volcengine",
        backend="openai",  # Conflicting backend
        model="doubao",
        api_key="test-key",
        dimension=1024,
    )
    if config_b.provider == "volcengine":
        print("   Pass (provider='volcengine' priority over backend='openai')")
    else:
        print(f"   Fail (provider='volcengine' should have priority, got '{config_b.provider}')")

    # Test 5: Ollama provider (no API key required)
    print("\n5. Test Ollama provider (no API key required)...")
    try:
        _ = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                provider="ollama",
                model="nomic-embed-text",
                dimension=768,
            )
        )
        print("   Pass (Ollama does not require API key)")
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 6: Ollama provider with custom api_base
    print("\n6. Test Ollama provider with custom api_base...")
    try:
        _ = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                provider="ollama",
                model="nomic-embed-text",
                api_base="http://localhost:11434/v1",
                dimension=768,
            )
        )
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 7: OpenAI provider with api_base but no api_key (local OpenAI-compatible server)
    print("\n7. Test OpenAI provider with api_base but no api_key...")
    try:
        _ = EmbeddingConfig(
            dense=EmbeddingModelConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_base="http://localhost:8080/v1",
                dimension=1536,
            )
        )
        print("   Pass (OpenAI provider allows missing api_key when api_base is set)")
    except ValueError as e:
        print(f"   Fail: {e}")


def test_vlm_validation():
    """Test VLM config validation"""
    print("\n" + "=" * 60)
    print("Test VLM config validation")
    print("=" * 60)

    # Test 1: VLM not configured (optional)
    print("\n1. Test VLM not configured (optional)...")
    try:
        _ = VLMConfig()
        print("   Pass (VLM is optional)")
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 2: VLM partial config (has model but no api_key)
    print("\n2. Test VLM partial config...")
    try:
        _ = VLMConfig(model="gpt-4")
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

    # Test 3: VLM complete config
    print("\n3. Test VLM complete config...")
    try:
        _ = VLMConfig(model="gpt-4", api_key="fake-api-key-for-testing", provider="openai")
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")

    # Test 4: VLM Provider/Backend sync
    print("\n4. Test VLM Provider/Backend sync...")
    # Case A: Only backend provided -> provider should be synced
    config_a = VLMConfig(backend="openai", model="gpt-4", api_key="test-key")
    if config_a.provider == "openai":
        print("   Pass (backend='openai' -> provider='openai')")
    else:
        print(f"   Fail (backend='openai' -> provider='{config_a.provider}')")

    # Case B: Both provided -> provider takes precedence
    config_b = VLMConfig(
        provider="volcengine", backend="openai", model="doubao", api_key="test-key"
    )
    if config_b.provider == "volcengine":
        print("   Pass (provider='volcengine' priority over backend='openai')")
    else:
        print(f"   Fail (provider='volcengine' should have priority, got '{config_b.provider}')")


if __name__ == "__main__":
    print("\nStarting config validator tests...\n")

    try:
        test_agfs_validation()
        test_vectordb_validation()
        test_embedding_validation()
        test_vlm_validation()

        print("\n" + "=" * 60)
        print("All tests completed!")
        print("=" * 60)

    except Exception as e:
        print(f"\nUnexpected error during tests: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
