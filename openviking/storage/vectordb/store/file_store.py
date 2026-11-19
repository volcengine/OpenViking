# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import os
from pathlib import Path
from typing import Optional

from openviking.observability.log_safety import short_hash
from openviking.storage.vectordb.store.store import IKVStore
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class FileStore(IKVStore):
    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize file storage

        Args:
            base_path: Base path for security validation. If None, no path restriction
        """
        super().__init__()
        self.base_path = Path(base_path).resolve() if base_path else None

    def _validate_path(self, key: str) -> Path:
        """
        Validate path security

        Args:
            key: File path

        Returns:
            Path: Resolved safe path

        Raises:
            ValueError: If path is unsafe (path traversal attack)
        """
        if self.base_path is not None:
            # Join key with base_path if base_path is set
            path = (self.base_path / key).resolve()
            # print(f"DEBUG: base_path={self.base_path}, key={key}, resolved_path={path}")
        else:
            path = Path(key).resolve()

        # If base path is set, ensure requested path is within base path
        if self.base_path is not None:
            try:
                path.relative_to(self.base_path)
            except ValueError as _log_error:
                logger.debug(
                    "Path traversal attempt detected: %s error_type=%s",
                    short_hash(str(key)),
                    type(_log_error).__name__,
                )
                raise ValueError(f"Invalid path: {key} is outside base directory")

        return path

    def get(self, key: str) -> Optional[bytes]:
        """
        Read file content

        Args:
            key: File path

        Returns:
            Optional[bytes]: File content, returns None if file doesn't exist or read fails
        """
        try:
            path = self._validate_path(key)
            # Open file in binary read-only mode
            with open(path, "rb") as f:
                # Read all binary data (returns bytes type)
                binary_data = f.read()
            return binary_data
        except FileNotFoundError:
            # logger.warning(f"File not found: {key}")
            return None
        except PermissionError as _log_error:
            logger.error(
                "Permission denied reading file: %s error_type=%s",
                short_hash(str(key)),
                type(_log_error).__name__,
            )
            return None
        except ValueError as e:
            # Path validation failed
            logger.info("get path rejected error_type=%s", type(e).__name__)
            return None
        except Exception as e:
            logger.error(
                "Unexpected error reading file %s: %s", short_hash(str(key)), type(e).__name__
            )
            return None

    def put(self, key: str, value: bytes) -> bool:
        """
        Write file content

        Args:
            key: File path
            value: Binary data to write

        Returns:
            bool: Returns True on success, False on failure
        """
        tmp_path = None
        try:
            path = self._validate_path(key)
            # Ensure parent directory exists
            path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: write to temp file then rename
            tmp_path = path.with_suffix(path.suffix + ".tmp")

            # Open temp file in binary write mode
            with open(tmp_path, "wb") as f:
                # Write binary data
                f.write(value)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            os.replace(tmp_path, path)
            return True
        except PermissionError as _log_error:
            logger.error(
                "Permission denied writing file: %s error_type=%s",
                short_hash(str(key)),
                type(_log_error).__name__,
            )
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False
        except OSError as e:
            # Handle disk full, path too long, and other system errors
            logger.error("OS error writing file %s: %s", short_hash(str(key)), type(e).__name__)
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False
        except ValueError as e:
            # Path validation failed
            logger.info("put path rejected error_type=%s", type(e).__name__)
            return False
        except Exception as e:
            logger.error(
                "Unexpected error writing file %s: %s", short_hash(str(key)), type(e).__name__
            )
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    def delete(self, key: str) -> bool:
        """
        Delete file

        Args:
            key: File path

        Returns:
            bool: Returns True on success, False on failure
        """
        try:
            path = self._validate_path(key)
            # Delete file
            path.unlink()
            return True
        except FileNotFoundError:
            # Idempotency: deleting a non-existent file is a success
            # logger.debug(f"File not found for deletion (ignored): {key}")
            return True
        except PermissionError as _log_error:
            logger.error(
                "Permission denied deleting file: %s error_type=%s",
                short_hash(str(key)),
                type(_log_error).__name__,
            )
            return False
        except ValueError as e:
            # Path validation failed
            logger.info("delete path rejected error_type=%s", type(e).__name__)
            return False
        except Exception as e:
            logger.error(
                "Unexpected error deleting file %s: %s", short_hash(str(key)), type(e).__name__
            )
            return False
