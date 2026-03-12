# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource-level mutex lock management.

Implements resource URI-level mutual exclusion to prevent concurrent operations
on the same resource. Uses file-based locks stored in the AGFS filesystem.
"""

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


@dataclass
class LockInfo:
    """Lock metadata stored in lock file."""
    
    lock_id: str
    resource_uri: str
    operation: str
    created_at: float
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "lock_id": self.lock_id,
            "resource_uri": self.resource_uri,
            "operation": self.operation,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LockInfo":
        return cls(**data)
    
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at


class ResourceLockError(Exception):
    """Base exception for resource lock errors."""
    pass


class ResourceLockConflictError(ResourceLockError):
    """Raised when attempting to lock a resource that is already locked."""
    
    def __init__(self, resource_uri: str, lock_info: Optional[LockInfo] = None):
        self.resource_uri = resource_uri
        self.lock_info = lock_info
        message = f"Resource '{resource_uri}' is locked"
        if lock_info:
            message += f" by operation '{lock_info.operation}' (lock_id: {lock_info.lock_id})"
        super().__init__(message)


class ResourceLockManager:
    """
    Manages resource-level mutex locks using file-based storage.
    
    Lock files are stored under `.locks/` directory in the AGFS root.
    Each lock file is named after the resource URI (with path separators replaced).
    
    Features:
    - Atomic lock acquisition via file creation
    - Lock expiration detection
    - Automatic cleanup of expired locks
    - Service restart cleanup
    """
    
    LOCK_DIR = ".locks"
    LOCK_FILE_SUFFIX = ".lock"
    DEFAULT_TTL = 3600
    AGFS_MOUNT_PATH = "/local"
    
    def __init__(self, agfs: Any, default_ttl: Optional[int] = None):
        """
        Initialize ResourceLockManager.
        
        Args:
            agfs: AGFS client instance
            default_ttl: Default lock TTL in seconds (default: 3600)
        """
        self._agfs = agfs
        self._default_ttl = default_ttl or self.DEFAULT_TTL
        self._lock_dir_path = f"{self.AGFS_MOUNT_PATH}/{self.LOCK_DIR}"
        
    def _get_lock_file_path(self, resource_uri: str) -> str:
        """
        Get lock file path for a resource URI.
        
        Args:
            resource_uri: Resource URI (e.g., "viking://default/resources/my-repo")
            
        Returns:
            Lock file path (e.g., "/local/.locks/viking___default___resources___my-repo.lock")
        """
        safe_uri = resource_uri.replace("://", "___").replace("/", "___").replace(".", "_")
        return f"{self._lock_dir_path}/{safe_uri}{self.LOCK_FILE_SUFFIX}"
    
    def _ensure_lock_dir(self) -> None:
        """Ensure lock directory exists."""
        try:
            if not self.exists(self._lock_dir_path):
                self._agfs.mkdir(self._lock_dir_path)
                logger.info(f"Created lock directory: {self._lock_dir_path}")
        except Exception as e:
            logger.warning(f"Failed to ensure lock directory: {e}")
    
    def acquire_lock(
        self,
        resource_uri: str,
        operation: str,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LockInfo:
        """
        Acquire a lock on a resource URI.
        
        Args:
            resource_uri: Resource URI to lock
            operation: Operation name (e.g., "incremental_update", "full_update")
            ttl: Lock TTL in seconds (default: use default_ttl)
            metadata: Additional metadata to store with lock
            
        Returns:
            LockInfo for the acquired lock
            
        Raises:
            ResourceLockConflictError: If resource is already locked
        """
        self._ensure_lock_dir()
        
        lock_file = self._get_lock_file_path(resource_uri)
        ttl = ttl or self._default_ttl
        
        current_time = time.time()
        lock_info = LockInfo(
            lock_id=str(uuid.uuid4()),
            resource_uri=resource_uri,
            operation=operation,
            created_at=current_time,
            expires_at=current_time + ttl if ttl > 0 else None,
            metadata=metadata or {},
        )
        
        try:
            if self.exists(lock_file):
                existing_lock = self._read_lock(lock_file)
                if existing_lock and not existing_lock.is_expired():
                    logger.warning(
                        f"Lock conflict: resource={resource_uri}, "
                        f"existing_lock_id={existing_lock.lock_id}, "
                        f"operation={existing_lock.operation}"
                    )
                    raise ResourceLockConflictError(resource_uri, existing_lock)
                
                logger.info(f"Removing expired lock: {lock_file}")
                self._agfs.rm(lock_file)
            
            self._agfs.write(lock_file, json.dumps(lock_info.to_dict()).encode('utf-8'))
            
            logger.info(
                f"Acquired lock: resource={resource_uri}, "
                f"lock_id={lock_info.lock_id}, "
                f"operation={operation}, "
                f"ttl={ttl}s"
            )
            
            return lock_info
            
        except ResourceLockConflictError:
            raise
        except Exception as e:
            logger.error(f"Failed to acquire lock for {resource_uri}: {e}")
            raise ResourceLockError(f"Failed to acquire lock: {e}") from e
    
    def release_lock(self, resource_uri: str, lock_id: Optional[str] = None) -> bool:
        """
        Release a lock on a resource URI.
        
        Args:
            resource_uri: Resource URI to unlock
            lock_id: Optional lock ID to verify ownership
            
        Returns:
            True if lock was released, False if lock didn't exist
        """
        lock_file = self._get_lock_file_path(resource_uri)
        
        try:
            if not self.exists(lock_file):
                return False
            
            if lock_id:
                existing_lock = self._read_lock(lock_file)
                if existing_lock and existing_lock.lock_id != lock_id:
                    logger.warning(
                        f"Lock ID mismatch: expected={lock_id}, "
                        f"actual={existing_lock.lock_id}"
                    )
                    return False
            
            self._agfs.rm(lock_file)
            logger.info(f"Released lock: resource={resource_uri}, lock_id={lock_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to release lock for {resource_uri}: {e}")
            return False
    
    def is_locked(self, resource_uri: str) -> bool:
        """
        Check if a resource URI is locked.
        
        Args:
            resource_uri: Resource URI to check
            
        Returns:
            True if resource is locked, False otherwise
        """
        lock_file = self._get_lock_file_path(resource_uri)
        
        try:
            if not self.exists(lock_file):
                return False
            
            lock_info = self._read_lock(lock_file)
            if not lock_info:
                return False
            
            if lock_info.is_expired():
                logger.info(f"Found expired lock: {lock_file}")
                self._agfs.rm(lock_file)
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to check lock for {resource_uri}: {e}")
            return False
    
    def get_lock_info(self, resource_uri: str) -> Optional[LockInfo]:
        """
        Get lock information for a resource URI.
        
        Args:
            resource_uri: Resource URI to check
            
        Returns:
            LockInfo if resource is locked, None otherwise
        """
        lock_file = self._get_lock_file_path(resource_uri)
        
        try:
            if not self.exists(lock_file):
                return None
            
            lock_info = self._read_lock(lock_file)
            if not lock_info:
                return None
            
            if lock_info.is_expired():
                logger.info(f"Found expired lock: {lock_file}")
                self._agfs.rm(lock_file)
                return None
            
            return lock_info
            
        except Exception as e:
            logger.error(f"Failed to get lock info for {resource_uri}: {e}")
            return None
    
    def _read_lock(self, lock_file: str) -> Optional[LockInfo]:
        """Read lock information from a lock file."""
        try:
            data = self._agfs.read(lock_file)
            lock_dict = json.loads(data.decode('utf-8'))
            return LockInfo.from_dict(lock_dict)
        except Exception as e:
            logger.error(f"Failed to read lock file {lock_file}: {e}")
            return None
    
    def cleanup_expired_locks(self) -> int:
        """
        Clean up all expired locks.
        
        Returns:
            Number of locks cleaned up
        """
        cleaned = 0
        
        try:
            if not self.exists(self._lock_dir_path):
                return 0
            
            lock_files = self._agfs.ls(self._lock_dir_path)
            
            for file_info in lock_files:
                lock_file = file_info.get("name", "")
                if not lock_file.endswith(self.LOCK_FILE_SUFFIX):
                    continue
                
                lock_path = f"{self._lock_dir_path}/{lock_file}"
                lock_info = self._read_lock(lock_path)
                
                if not lock_info or lock_info.is_expired():
                    self._agfs.rm(lock_path)
                    cleaned += 1
                    logger.info(f"Cleaned up expired lock: {lock_path}")
            
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} expired locks")
            
            return cleaned
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired locks: {e}")
            return cleaned
    
    def cleanup_all_locks(self) -> int:
        """
        Clean up all locks (for service restart).
        
        Returns:
            Number of locks cleaned up
        """
        cleaned = 0
        
        try:
            if not self.exists(self._lock_dir_path):
                return 0
            
            lock_files = self._agfs.ls(self._lock_dir_path)
            
            for file_info in lock_files:
                lock_file = file_info.get("name", "")
                if not lock_file.endswith(self.LOCK_FILE_SUFFIX):
                    continue
                
                lock_path = f"{self._lock_dir_path}/{lock_file}"
                self._agfs.rm(lock_path)
                cleaned += 1
            
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} locks on service restart")
            
            return cleaned
            
        except Exception as e:
            logger.error(f"Failed to cleanup all locks: {e}")
            return cleaned
    
    def exists(self, uri: str) -> bool:
        """
        Check if a URI exists using AGFS stat interface.
        
        Args:
            uri: URI to check (e.g., "viking://default/resources/my-repo")
            
        Returns:
            True if URI exists, False otherwise
        """
        try:
            self._agfs.stat(uri)
            return True
        except Exception as e:
            return False
    
    @contextmanager
    def lock(
        self,
        resource_uri: str,
        operation: str,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Context manager for acquiring and releasing a lock.
        
        Args:
            resource_uri: Resource URI to lock
            operation: Operation name
            ttl: Lock TTL in seconds
            metadata: Additional metadata
            
        Yields:
            LockInfo for the acquired lock
            
        Raises:
            ResourceLockConflictError: If resource is already locked
        """
        lock_info = self.acquire_lock(resource_uri, operation, ttl, metadata)
        try:
            yield lock_info
        finally:
            self.release_lock(resource_uri, lock_info.lock_id)
