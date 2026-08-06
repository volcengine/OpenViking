# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Fixtures shared by service tests."""

import uuid

import pytest

from tests.transaction.conftest import MemoryAgfs


@pytest.fixture
def agfs_client():
    agfs = MemoryAgfs()
    agfs.mkdir("/local")
    agfs.mkdir("/local/default")
    return agfs


@pytest.fixture
def test_dir(agfs_client):
    path = f"/local/default/transaction-{uuid.uuid4().hex}"
    agfs_client.mkdir(path)
    return path
