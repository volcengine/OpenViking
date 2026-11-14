# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-safe timed log rotation shared by server and usage logging."""

import logging
import os
from contextlib import contextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Iterator


@contextmanager
def interprocess_lock(path: Path) -> Iterator[None]:
    """Serialize log rotation across server worker processes."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class ProcessSafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Timed handler that coordinates rollover with sibling worker processes."""

    def __init__(self, filename: Path, **kwargs: Any) -> None:
        self._process_lock_path = filename.with_name(f".{filename.name}.lock")
        if os.name == "nt":
            kwargs["delay"] = True
        super().__init__(filename=filename, **kwargs)
        self._base_file_identity = self._get_base_file_identity()

    def emit(self, record: logging.LogRecord) -> None:
        emitted = False
        try:
            with interprocess_lock(self._process_lock_path):
                # Never log through this handler while holding the lock.
                self._refresh_after_external_rollover()
                emitted = True
                try:
                    super().emit(record)
                finally:
                    self._base_file_identity = self._get_base_file_identity()
                    # Release Windows handles so sibling workers can rename.
                    if os.name == "nt" and self.stream is not None:
                        self.stream.close()
                        self.stream = None
        except OSError:
            if not emitted:
                # A lock timeout or unwritable lock directory must not stop logging.
                try:
                    super().emit(record)
                finally:
                    if os.name == "nt" and self.stream is not None:
                        self.stream.close()
                        self.stream = None

    @staticmethod
    def _file_identity(stat_result: os.stat_result) -> tuple[int, int]:
        return stat_result.st_dev, stat_result.st_ino

    def _get_base_file_identity(self) -> tuple[int, int] | None:
        try:
            return self._file_identity(os.stat(self.baseFilename))
        except FileNotFoundError:
            return None

    def _refresh_after_external_rollover(self) -> None:
        """Reopen a base file rotated by another process and sync its deadline."""
        try:
            path_stat = os.stat(self.baseFilename)
        except FileNotFoundError:
            path_stat = None
        path_identity = self._file_identity(path_stat) if path_stat is not None else None

        stream_stat = None
        if self.stream is not None:
            try:
                stream_stat = os.fstat(self.stream.fileno())
            except (OSError, ValueError):
                stream_stat = None

        if stream_stat is not None and path_stat is not None:
            if self._file_identity(stream_stat) == path_identity:
                self._base_file_identity = path_identity
                return
        elif self.stream is None and path_identity == self._base_file_identity:
            # Windows closes the stream after every emit. The missing handle
            # alone does not mean another worker rotated the file, so preserve
            # this handler's existing rollover deadline.
            return

        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self._base_file_identity = path_identity
        if path_stat is not None:
            self.rolloverAt = self.computeRollover(int(path_stat.st_mtime))
