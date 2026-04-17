# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Storage layer interfaces and implementations.

Heavy submodules (vectordb engine, adapters) are loaded lazily via
``__getattr__`` to avoid an import-lock deadlock that occurs when the
native C++ engine extension is loaded while ``storage/__init__`` is
still executing.
"""

from openviking.storage.errors import (
    CollectionNotFoundError,
    ConnectionError,
    DuplicateKeyError,
    RecordNotFoundError,
    SchemaError,
    StorageException,
)

_LAZY_IMPORTS = {
    "BaseObserver": "openviking.storage.observers",
    "QueueObserver": "openviking.storage.observers",
    "QueueManager": "openviking.storage.queuefs",
    "get_queue_manager": "openviking.storage.queuefs",
    "init_queue_manager": "openviking.storage.queuefs",
    "VikingFS": "openviking.storage.viking_fs",
    "get_viking_fs": "openviking.storage.viking_fs",
    "init_viking_fs": "openviking.storage.viking_fs",
    "VikingVectorIndexBackend": "openviking.storage.viking_vector_index_backend",
    "VikingDBManager": "openviking.storage.vikingdb_manager",
    "VikingDBManagerProxy": "openviking.storage.vikingdb_manager",
}


def __getattr__(name: str):
    module_path = _LAZY_IMPORTS.get(name)
    if module_path is not None:
        import importlib

        module = importlib.import_module(module_path)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
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
    "VikingDBManagerProxy",
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
