# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.pyagfs import get_binding_client


@pytest.fixture(scope="session")
def ragfs_client_type():
    client_type, _ = get_binding_client()
    return client_type
