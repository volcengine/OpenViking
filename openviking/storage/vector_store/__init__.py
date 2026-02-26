# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Vector store driver architecture."""

from openviking.storage.vector_store.driver import VectorStoreDriver
from openviking.storage.vector_store.expr import (
    And,
    Contains,
    Eq,
    FilterExpr,
    In,
    Or,
    Prefix,
    Range,
    RawDSL,
    Regex,
    TimeRange,
)
from openviking.storage.vector_store.factory import create_driver
from openviking.storage.vector_store.registry import (
    get_driver_class,
    list_registered_backends,
    register_driver,
)

__all__ = [
    "VectorStoreDriver",
    "FilterExpr",
    "And",
    "Or",
    "Eq",
    "In",
    "Prefix",
    "Range",
    "Contains",
    "Regex",
    "TimeRange",
    "RawDSL",
    "create_driver",
    "register_driver",
    "get_driver_class",
    "list_registered_backends",
]
