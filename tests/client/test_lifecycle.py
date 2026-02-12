# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Client lifecycle tests"""

from pathlib import Path

from openviking import AsyncOpenViking


class TestClientInitialization:
    """Test Client initialization"""

    async def test_initialize_success(self, uninitialized_client: AsyncOpenViking):
        """Test normal initialization"""
        await uninitialized_client.initialize()
        assert uninitialized_client._initialized is True

    async def test_initialize_idempotent(self, client: AsyncOpenViking):
        """Test repeated initialization is idempotent"""
        await client.initialize()
        await client.initialize()
        assert client._initialized is True

    async def test_initialize_creates_client(self, uninitialized_client: AsyncOpenViking):
        """Test initialization creates client"""
        await uninitialized_client.initialize()
        assert uninitialized_client._client is not None


class TestClientClose:
    """Test Client close"""

    async def test_close_success(self, test_data_dir: Path):
        """Test normal close"""
        await AsyncOpenViking.reset()
        client = AsyncOpenViking(path=str(test_data_dir))
        await client.initialize()

        await client.close()
        assert client._initialized is False

        await AsyncOpenViking.reset()

    async def test_close_idempotent(self, test_data_dir: Path):
        """Test repeated close is safe"""
        await AsyncOpenViking.reset()
        client = AsyncOpenViking(path=str(test_data_dir))
        await client.initialize()

        await client.close()
        await client.close()  # Should not raise exception

        await AsyncOpenViking.reset()


class TestClientReset:
    """Test Client reset"""

    async def test_reset_clears_singleton(self, test_data_dir: Path):
        """Test reset clears singleton"""
        await AsyncOpenViking.reset()

        client1 = AsyncOpenViking(path=str(test_data_dir))
        await client1.initialize()

        await AsyncOpenViking.reset()

        client2 = AsyncOpenViking(path=str(test_data_dir))
        # Should be new instance after reset
        assert client1 is not client2

        await AsyncOpenViking.reset()


class TestClientSingleton:
    """Test Client singleton pattern"""

    async def test_embedded_mode_singleton(self, test_data_dir: Path):
        """Test embedded mode uses singleton"""
        await AsyncOpenViking.reset()

        client1 = AsyncOpenViking(path=str(test_data_dir))
        client2 = AsyncOpenViking(path=str(test_data_dir))

        assert client1 is client2

        await AsyncOpenViking.reset()
