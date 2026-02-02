# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import threading
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

T = TypeVar("T")


class ThreadSafeDictManager(Generic[T]):
    """Thread-safe dictionary manager (generic version)

    Encapsulates all dictionary access operations to ensure concurrency safety.
    Can be used to manage any type of object dictionary, such as index, collection, etc.

    Type parameter:
        T: Type of values in the dictionary

    Example:
        # Manage index
        index_manager = ThreadSafeDictManager[IIndex]()

        # Manage collection
        collection_manager = ThreadSafeDictManager[Collection]()
    """

    def __init__(self):
        self._items: Dict[str, T] = {}
        self._lock = threading.RLock()

    def get(self, name: str) -> Optional[T]:
        """Get specified item"""
        with self._lock:
            return self._items.get(name, None)

    def set(self, name: str, item: T):
        """Set item"""
        with self._lock:
            self._items[name] = item

    def remove(self, name: str) -> Optional[T]:
        """Remove item and return"""
        with self._lock:
            return self._items.pop(name, None)

    def has(self, name: str) -> bool:
        """Check if item exists"""
        with self._lock:
            return name in self._items

    def list_names(self) -> List[str]:
        """Get list of all item names"""
        with self._lock:
            return list(self._items.keys())

    def get_all(self) -> Dict[str, T]:
        """Get copy of all items"""
        with self._lock:
            return dict(self._items)

    def clear(self):
        """Clear all items"""
        with self._lock:
            self._items.clear()

    def is_empty(self) -> bool:
        """Check if empty"""
        with self._lock:
            return len(self._items) == 0

    def count(self) -> int:
        """Get item count"""
        with self._lock:
            return len(self._items)

    def iterate(self, callback: Callable[[str, T], None]):
        """Safely iterate all items

        Args:
            callback: Function that accepts (name, item)
        """
        with self._lock:
            # Create copy to avoid modification during iteration
            items = list(self._items.items())

        # Execute callback outside lock to avoid deadlock
        for name, item in items:
            callback(name, item)

    def get_all_with_lock(self):
        """Get all items and hold lock (for scenarios requiring atomic operations)

        Returns a context manager, usage:
        with manager.get_all_with_lock() as items:
            # Operations on items here are thread-safe
            pass
        """
        return _DictLockContext(self._lock, self._items)


class _DictLockContext:
    """Dictionary lock context manager"""

    def __init__(self, lock, items):
        self._lock = lock
        self._items = items

    def __enter__(self):
        self._lock.acquire()
        return self._items

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()
        return False


def filter_dict_key_with_prefix(d: Dict[str, Any], prefix: str = "_") -> Dict[str, Any]:
    """
    Recursively filter out keys starting with a prefix from a dictionary.

    Args:
        d: The dictionary to filter.
        prefix: The prefix to check for. Defaults to "_".

    Returns:
        A new dictionary with filtered keys.
    """
    filtered: Dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(key, str) and key.startswith(prefix):
            continue
        if isinstance(value, dict):
            filtered[key] = filter_dict_key_with_prefix(value, prefix)
        elif isinstance(value, list):
            filtered[key] = [
                filter_dict_key_with_prefix(v, prefix) if isinstance(v, dict) else v for v in value
            ]
        else:
            filtered[key] = value
    return filtered


def recursive_update_dict(target: Dict[Any, Any], source: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    Recursively update dictionary target with source.
    - If values are dicts, recursive update.
    - If values are lists, extend target list with source list.
    - Otherwise, overwrite target value.

    Args:
        target: The target dictionary to update (modified in-place).
        source: The source dictionary.

    Returns:
        The updated target dictionary.
    """
    for key, src_val in source.items():
        if key in target:
            tgt_val = target[key]
            # Handle nested dictionary: recursive update
            if isinstance(tgt_val, dict) and isinstance(src_val, dict):
                recursive_update_dict(tgt_val, src_val)
            # Handle list: append source list elements to target list
            elif isinstance(tgt_val, list) and isinstance(src_val, list):
                tgt_val.extend(src_val)  # Equivalent to: for item in src_val: tgt_val.append(item)
            # Other types: direct overwrite
            else:
                target[key] = src_val
        else:
            # Key not in target: add directly
            target[key] = src_val

    return target
