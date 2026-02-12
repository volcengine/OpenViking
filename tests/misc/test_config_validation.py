#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Test if config validators work correctly"""

import sys

from openviking_cli.utils.config.agfs_config import AGFSConfig, S3Config
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig
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
            volcengine={"ak": "test_ak", "sk": "test_sk", "region": "cn-beijing"},
        )
        print("   Pass")
    except ValueError as e:
        print(f"   Fail: {e}")


def test_embedding_validation():
    """Test Embedding config validation"""
    print("\n" + "=" * 60)
    print("Test Embedding config validation")
    print("=" * 60)

    # Test 1: no embedder config
    print("\n1. Test no embedder config...")
    try:
        _ = EmbeddingConfig()
        print("   Should fail but passed")
    except ValueError as e:
        print(f"   Correctly raised exception: {e}")

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
