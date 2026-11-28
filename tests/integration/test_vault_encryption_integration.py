# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Vault-specific failure handling. The file roundtrip is shared by all providers."""

import pytest

from openviking.crypto.exceptions import AuthenticationFailedError, ConfigError
from openviking.crypto.providers import create_root_key_provider
from tests.integration.conftest import VAULT_ADDR, requires_vault

pytestmark = pytest.mark.integration


class TestVaultProviderErrors:
    @requires_vault
    async def test_invalid_token(self):
        provider = create_root_key_provider(
            "vault",
            {"vault": {"address": VAULT_ADDR, "token": "invalid-test-token"}},
        )

        with pytest.raises(AuthenticationFailedError, match="authenticate with Vault"):
            await provider.get_root_key()

    def test_create_provider_missing_config(self):
        for options in ({}, {"address": VAULT_ADDR}, {"token": "test-token"}):
            with pytest.raises(ConfigError, match="vault.address and vault.token are required"):
                create_root_key_provider("vault", {"vault": options})
