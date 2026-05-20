from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Error classification categories returned by classify_api_error()
ERROR_CLASS_PERMANENT = "permanent"
ERROR_CLASS_QUOTA_EXCEEDED = "quota_exceeded"
ERROR_CLASS_TRANSIENT = "transient"
ERROR_CLASS_UNKNOWN = "unknown"

PERMANENT_API_ERROR_PATTERNS = (
    "400",
    "401",
    "403",
    "forbidden",
    "unauthorized",
    "accountoverdue",
)

QUOTA_EXCEEDED_PATTERNS = (
    "quotaexceeded", # also 429
    "quota limit",
    "quota exceed",
    "usage quota",
)

_PERMANENT_IO_ERRORS = (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)

TRANSIENT_API_ERROR_PATTERNS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "toomanyrequests",
    "ratelimit",
    "requestbursttoofast",
    "timeout",
    "connectionerror",
    "connection refused",
    "connection reset",
)


def classify_api_error(error: Exception) -> str:
    """Classify an API error as permanent, quota_exceeded, transient, or unknown.

    ``quota_exceeded`` is checked before ``transient`` because quota errors
    typically include "429" / "TooManyRequests" which would otherwise match
    the transient category.  Quota errors should not be retried; the caller
    should fail over to a backup model instead.
    """
    for exc in (error, getattr(error, "__cause__", None)):
        if exc is not None and isinstance(exc, _PERMANENT_IO_ERRORS):
            return ERROR_CLASS_PERMANENT

    texts = [str(error)]
    if error.__cause__ is not None:
        texts.append(str(error.__cause__))

    for text in texts:
        text_lower = text.lower()
        for pattern in PERMANENT_API_ERROR_PATTERNS:
            if pattern in text_lower:
                return ERROR_CLASS_PERMANENT

    # Check quota_exceeded *before* transient so that "429 … AccountQuotaExceeded"
    # is classified as quota_exceeded, not transient.
    for text in texts:
        text_lower = text.lower()
        for pattern in QUOTA_EXCEEDED_PATTERNS:
            if pattern in text_lower:
                return ERROR_CLASS_QUOTA_EXCEEDED

    for text in texts:
        text_lower = text.lower()
        for pattern in TRANSIENT_API_ERROR_PATTERNS:
            if pattern in text_lower:
                return ERROR_CLASS_TRANSIENT

    return ERROR_CLASS_UNKNOWN


def is_quota_exceeded_api_error(error: Exception) -> bool:
    """Return True if the error indicates an account quota has been exceeded."""
    return classify_api_error(error) == ERROR_CLASS_QUOTA_EXCEEDED


def is_retryable_api_error(error: Exception) -> bool:
    """Return True if the error should be retried."""
    return classify_api_error(error) == ERROR_CLASS_TRANSIENT


def _compute_delay(
    attempt: int,
    *,
    base_delay: float,
    max_delay: float,
    jitter: bool,
) -> float:
    delay = min(base_delay * (2**attempt), max_delay)
    if jitter:
        delay += random.uniform(0.0, min(base_delay, delay))
    return delay


def retry_sync(
    func: Callable[[], T],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Callable[[Exception], bool] = is_retryable_api_error,
    logger=None,
    operation_name: str = "operation",
) -> T:
    """Retry a sync function on known transient errors."""
    attempt = 0

    while True:
        try:
            return func()
        except Exception as e:
            if max_retries <= 0 or attempt >= max_retries or not is_retryable(e):
                raise

            delay = _compute_delay(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
            )
            if logger:
                logger.warning(
                    "%s failed with retryable error (retry %d/%d): %s; retrying in %.2fs",
                    operation_name,
                    attempt + 1,
                    max_retries,
                    e,
                    delay,
                )
            time.sleep(delay)
            attempt += 1


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Callable[[Exception], bool] = is_retryable_api_error,
    logger=None,
    operation_name: str = "operation",
) -> T:
    """Retry an async function on known transient errors."""
    attempt = 0

    while True:
        try:
            return await func()
        except Exception as e:
            if max_retries <= 0 or attempt >= max_retries or not is_retryable(e):
                raise

            delay = _compute_delay(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
            )
            if logger:
                logger.warning(
                    "%s failed with retryable error (retry %d/%d): %s; retrying in %.2fs",
                    operation_name,
                    attempt + 1,
                    max_retries,
                    e,
                    delay,
                )
            await asyncio.sleep(delay)
            attempt += 1


class PrimaryBackupSwitcher:
    """Thread-safe primary/backup switcher with automatic failback logic.

    When an error of type ERROR_CLASS_PERMANENT or ERROR_CLASS_QUOTA_EXCEEDED occurs,
    switches to backup immediately. Then, after either:
    - 10 minutes have passed, OR
    - 200 requests have been made to backup
    it will attempt to failback to primary. If failback fails, it switches back
    to backup and resets the timer/counter.
    """

    def __init__(
        self,
        failback_timeout_seconds: float = 600.0,  # 10 minutes
        failback_request_count: int = 200,
    ):
        self._failback_timeout = failback_timeout_seconds
        self._failback_request_count = failback_request_count
        self._lock = threading.Lock()

        # State
        self._using_backup = False
        self._switch_to_backup_time: float = 0.0
        self._backup_request_count = 0

    def should_try_primary(self) -> bool:
        """Check if we should try primary again.

        Returns True if we're using backup and either the timeout has elapsed
        or we've made enough requests to backup.
        """
        with self._lock:
            if not self._using_backup:
                return True  # Already using primary

            elapsed = time.monotonic() - self._switch_to_backup_time
            if elapsed >= self._failback_timeout:
                logger.info(
                    f"Failback timeout elapsed ({elapsed:.0f}s), attempting to switch back to primary"
                )
                return True

            if self._backup_request_count >= self._failback_request_count:
                logger.info(
                    f"Failback request count reached ({self._backup_request_count}), attempting to switch back to primary"
                )
                return True

            return False

    def record_primary_success(self) -> None:
        """Record a successful primary call - stay on primary."""
        with self._lock:
            if self._using_backup:
                logger.info("Primary succeeded, switching back from backup to primary")
                self._using_backup = False
                self._backup_request_count = 0
            # else already on primary, do nothing

    def record_primary_failure(self, error: Exception) -> bool:
        """Record a primary failure. Returns True if should switch to backup.

        Switches to backup immediately for ERROR_CLASS_PERMANENT or ERROR_CLASS_QUOTA_EXCEEDED.
        """
        error_class = classify_api_error(error)
        if error_class in (ERROR_CLASS_PERMANENT, ERROR_CLASS_QUOTA_EXCEEDED):
            with self._lock:
                if not self._using_backup:
                    logger.warning(
                        f"Primary failed with {error_class}, switching to backup"
                    )
                    self._using_backup = True
                # Always reset timer and counter when we fail (whether initial fail or failback fail)
                self._switch_to_backup_time = time.monotonic()
                self._backup_request_count = 0
            return True
        return False

    def record_backup_request(self) -> None:
        """Record a request to backup (for counting towards failback)."""
        with self._lock:
            if self._using_backup:
                self._backup_request_count += 1

    @property
    def is_using_backup(self) -> bool:
        """Check if currently using backup."""
        with self._lock:
            return self._using_backup
