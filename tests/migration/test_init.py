# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""GREEN phase: verify that migration package imports successfully.

After state.py is implemented, the package import should succeed and
all key exports should be accessible.
"""


def test_migration_package_import_succeeds():
    """Importing the migration package should succeed after GREEN implementation."""
    import openviking.storage.migration  # noqa: F401

    from openviking.storage.migration import (
        MigrationPhase,
        MigrationState,
        MigrationStateFile,
        MigrationStateManager,
        ReindexProgress,
    )

    # Verify the package __all__ contains all key exports
    assert hasattr(openviking.storage.migration, "MigrationPhase")
    assert hasattr(openviking.storage.migration, "MigrationState")
    assert hasattr(openviking.storage.migration, "MigrationStateFile")
    assert hasattr(openviking.storage.migration, "MigrationStateManager")
    assert hasattr(openviking.storage.migration, "ReindexProgress")
