# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Storage layer interfaces and implementations."""

from openviking.storage.observers import BaseObserver, QueueObserver
from openviking.storage.queuefs import QueueManager, get_queue_manager, init_queue_manager
from openviking.storage.viking_fs import VikingFS, get_viking_fs, init_viking_fs
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.storage.vikingdb_interface import (
    CollectionNotFoundError,
    ConnectionError,
    DuplicateKeyError,
    RecordNotFoundError,
    SchemaError,
    StorageException,
    VikingDBInterface,
)
from openviking.storage.vikingdb_manager import VikingDBManager

__all__ = [
    # Interface
    "VikingDBInterface",
    # Exceptions
    "StorageException",
    "CollectionNotFoundError",
    "RecordNotFoundError",
    "DuplicateKeyError",
    "ConnectionError",
    "SchemaError",
    # Backend
    "VikingVectorIndexBackend",
    "VikingDBManager",
    # QueueFS
    "QueueManager",
    "init_queue_manager",
    "get_queue_manager",
    # VikingFS
    "VikingFS",
    "init_viking_fs",
    "get_viking_fs",
    # Observers
    "BaseObserver",
    "QueueObserver",
]
