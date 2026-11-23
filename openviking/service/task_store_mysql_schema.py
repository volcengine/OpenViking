# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility exports for the MySQL Task/Work schema."""

from openviking.service.task_store_sql_schema import (
    TaskStoreModels,
    make_task_store_models,
    validate_table_prefix,
)

__all__ = ["TaskStoreModels", "make_task_store_models", "validate_table_prefix"]
