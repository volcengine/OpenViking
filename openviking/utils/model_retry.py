from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import time
from typing import AsyncIterator, Awaitable, Callable, Iterable, Iterator, TypeVar

from openviking.utils.exceptions import AllCredentialsFailedError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Error classification categories returned by classify_api_error()
ERROR_CLASS_PERMANENT = "permanent"  # request-level 4xx (e.g. 400 invalid parameter)
ERROR_CLASS_AUTH = "auth"  # credential-level 401/403 (key invalid / no permission / overdue)
ERROR_CLASS_CONTENT_SAFETY = "content_safety"  # request content rejected by moderation
ERROR_CLASS_INPUT_TOO_LARGE = "input_too_large"
ERROR_CLASS_QUOTA_EXCEEDED = "quota_exceeded"
ERROR_CLASS_TRANSIENT = "transient"
ERROR_CLASS_UNKNOWN = "unknown"

_METRIC_ERROR_CODE_MAX_LENGTH = 64


def _normalize_metric_error_code(value: object) -> str | None:
    """Return a bounded structured error code suitable for a metric label."""
    if value is None:
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return str(value)
    if not isinstance(value, str):
        return None
    code = value.strip()
    if not code or len(code) > _METRIC_ERROR_CODE_MAX_LENGTH:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", code):
        return None
    return code


def _iter_structured_error_codes(error: BaseException) -> Iterator[str]:
    for exc in _iter_exception_chain(error):
        candidates = [getattr(exc, attr, None) for attr in ("status_code", "error_code", "code")]
        response = getattr(exc, "response", None)
        if response is not None:
            candidates.append(getattr(response, "status_code", None))
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            candidates.extend([body.get("status_code"), body.get("error_code"), body.get("code")])
            nested = body.get("error")
            if isinstance(nested, dict):
                candidates.extend(
                    [nested.get("status_code"), nested.get("error_code"), nested.get("code")]
                )

        for value in candidates:
            normalized = _normalize_metric_error_code(value)
            if normalized is not None:
                yield normalized


def extract_metric_error_code(error: BaseException) -> str:
    """Extract a low-cardinality provider error code for model-call metrics.

    Structured HTTP/SDK error codes are preferred over message parsing.  Free-form
    provider messages and request IDs are intentionally never used as metric labels.
    """
    error_chain = _iter_exception_chain(error)
    structured_code = next(_iter_structured_error_codes(error), None)
    if structured_code is not None:
        return structured_code

    if any(isinstance(exc, TimeoutError) for exc in error_chain):
        return "timeout"
    if any(isinstance(exc, ConnectionError) for exc in error_chain):
        return "connection_error"
    return "unknown"


INPUT_TOO_LARGE_PATTERNS = (
    "413",
    "payload too large",
    "request entity too large",
    "content too large",
    "contextwindowexceeded",
    "context window exceeded",
    "maximum context length",
    "max input tokens",
    "too many input tokens",
    "input length exceeds",
    "exceeds the context length",
    "exceeds the max input length",
    "is too large to process",
    "expected maxlength",
)

PERMANENT_API_ERROR_PATTERNS = ("400",)

# Credential-level errors: in multi-credential mode these advance to the next
# credential (another key may be valid / have permission / have balance); with a
# single credential or on the last credential they fail fast.
AUTH_API_ERROR_PATTERNS = (
    "401",
    "403",
    "forbidden",
    "unauthorized",
    "accountoverdue",
)

# Content moderation rejections. Same request content fails on every credential
# of the same model, so these fail fast (no point switching credentials).
CONTENT_SAFETY_PATTERNS = (
    "content policy",
    "content_filter",
    "contentfilter",
    "moderation",
    "sensitive content",
    "内容安全",
    "敏感",
)

QUOTA_EXCEEDED_PATTERNS = (
    "quotaexceeded",  # also 429
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

RETRYABLE_RATE_LIMIT_MARKERS = (
    "TooManyRequests",
    "RateLimitExceeded",
    "ModelAccountTpmRateLimitExceeded",
    "TPM (Tokens Per Minute) limit",
    "RPM (Requests Per Minute) limit",
    "rate limit",
    "rate_limit",
)

_RATE_LIMIT_STATUS_RE = re.compile(
    r"(?:\b(?:error\s*code|status(?:\s*code)?|http(?:\s*status)?|code)"
    r"\s*[:=]?\s*429(?!\w)|(?<![\w-])429(?![\w-]))",
    re.IGNORECASE,
)
_RATE_LIMIT_ERROR_CLASSES: tuple[type[BaseException], ...] = ()
RATE_LIMIT_RETRY_BASE_DELAY_SECONDS = 5.0
RATE_LIMIT_RETRY_MAX_DELAY_SECONDS = 120.0

# Pre-compile regex for numeric status-code patterns to avoid substring false positives
# (e.g. "413" matching inside request IDs like "d7c9130f344..." or "req-413-abcd").
_NUMERIC_PATTERN_RE: dict[str, re.Pattern] = {}


def _get_numeric_pattern_re(pattern: str) -> re.Pattern:
    if pattern not in _NUMERIC_PATTERN_RE:
        escaped = re.escape(pattern)
        _NUMERIC_PATTERN_RE[pattern] = re.compile(
            rf"(?:\b(?:error\s*code|status(?:\s*code)?|http(?:\s*status)?|code)"
            rf"\s*[:=]?\s*{escaped}(?!\w)|(?<![\w-]){escaped}(?![\w-]))"
        )
    return _NUMERIC_PATTERN_RE[pattern]


def _pattern_matches(text_lower: str, text_compact: str, pattern: str) -> bool:
    """Check if pattern matches in text, using token-aware matching for numeric patterns.

    Numeric-only patterns (e.g. ``"413"``) must look like HTTP status codes, not
    request ID fragments. Non-numeric patterns use plain substring matching as before.
    """
    if pattern.isdigit():
        return bool(_get_numeric_pattern_re(pattern).search(text_lower)) or bool(
            _get_numeric_pattern_re(pattern).search(text_compact)
        )
    return pattern in text_lower or pattern.replace(" ", "") in text_compact


def _classify_error_values(values: Iterable[str]) -> str:
    texts = [(value.lower(), value.lower().replace(" ", "")) for value in values]
    categories = (
        (ERROR_CLASS_INPUT_TOO_LARGE, INPUT_TOO_LARGE_PATTERNS),
        (ERROR_CLASS_CONTENT_SAFETY, CONTENT_SAFETY_PATTERNS),
        (ERROR_CLASS_AUTH, AUTH_API_ERROR_PATTERNS),
        (ERROR_CLASS_QUOTA_EXCEEDED, QUOTA_EXCEEDED_PATTERNS),
        (ERROR_CLASS_PERMANENT, PERMANENT_API_ERROR_PATTERNS),
        (ERROR_CLASS_TRANSIENT, TRANSIENT_API_ERROR_PATTERNS),
    )
    for error_class, patterns in categories:
        for text_lower, text_compact in texts:
            if any(_pattern_matches(text_lower, text_compact, pattern) for pattern in patterns):
                return error_class
    return ERROR_CLASS_UNKNOWN


def classify_api_error(error: Exception) -> str:
    """Classify an API error into one of the ERROR_CLASS_* categories.

    Order matters:
    - structured HTTP status codes are checked before falling back to text matching.
    - ``content_safety`` is checked before ``permanent`` so a moderation
      rejection that happens to embed "400" in its message is not misclassified.
    - ``auth`` (401/403) is separated from ``permanent`` (400): auth errors are
      credential-level and may be resolved by switching credentials, whereas a
      400 is a request-level error that fails on every credential of the same
      model.
    - ``quota_exceeded`` is checked before ``transient`` because quota errors
      typically include "429" / "TooManyRequests" which would otherwise match
      the transient category.
    - an aggregated ``AllCredentialsFailedError`` is classified from its
      per-credential classes, not its concatenated message.
    """
    if isinstance(error, AllCredentialsFailedError):
        classes = [ec for (_cid, ec, _exc, _idx) in error.errors if ec]
        if ERROR_CLASS_TRANSIENT in classes:
            return ERROR_CLASS_TRANSIENT
        if ERROR_CLASS_QUOTA_EXCEEDED in classes:
            return ERROR_CLASS_QUOTA_EXCEEDED
        if classes and all(ec == ERROR_CLASS_AUTH for ec in classes):
            return ERROR_CLASS_AUTH
        return ERROR_CLASS_UNKNOWN

    error_chain = _iter_exception_chain(error)
    if any(isinstance(exc, _PERMANENT_IO_ERRORS) for exc in error_chain):
        return ERROR_CLASS_PERMANENT
    if any(isinstance(exc, (TimeoutError, ConnectionError)) for exc in error_chain):
        return ERROR_CLASS_TRANSIENT

    structured_class = _classify_error_values(_iter_structured_error_codes(error))
    if structured_class != ERROR_CLASS_UNKNOWN:
        return structured_class

    return _classify_error_values(str(exc) for exc in _iter_exception_chain(error))


def is_retryable_api_error(error: Exception) -> bool:
    """Return True if the error should be retried."""
    return classify_api_error(error) == ERROR_CLASS_TRANSIENT


def _load_rate_limit_error_classes() -> tuple[type[BaseException], ...]:
    classes: list[type[BaseException]] = []
    try:
        import openai

        classes.append(openai.RateLimitError)
    except Exception:
        pass
    try:
        from volcenginesdkarkruntime._exceptions import ArkRateLimitError

        classes.append(ArkRateLimitError)
    except Exception:
        pass
    return tuple(classes)


def _iter_exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        chain.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return chain


def _structured_rate_limit_match(exc: BaseException) -> bool:
    global _RATE_LIMIT_ERROR_CLASSES
    if not _RATE_LIMIT_ERROR_CLASSES:
        _RATE_LIMIT_ERROR_CLASSES = _load_rate_limit_error_classes()

    for item in _iter_exception_chain(exc):
        if _RATE_LIMIT_ERROR_CLASSES and isinstance(item, _RATE_LIMIT_ERROR_CLASSES):
            return True
        status_code = getattr(item, "status_code", None)
        if status_code == 429 or str(status_code) == "429":
            return True
        code = getattr(item, "code", None)
        error_type = getattr(item, "type", None)
        if any(
            isinstance(value, str)
            and any(marker.lower() in value.lower() for marker in RETRYABLE_RATE_LIMIT_MARKERS)
            for value in (code, error_type)
        ):
            return True
        body = getattr(item, "body", None)
        if isinstance(body, dict):
            values = [body.get("code"), body.get("type"), body.get("message")]
            if isinstance(body.get("error"), dict):
                error = body["error"]
                values.extend([error.get("code"), error.get("type"), error.get("message")])
            if any(
                isinstance(value, str)
                and any(marker.lower() in value.lower() for marker in RETRYABLE_RATE_LIMIT_MARKERS)
                for value in values
            ):
                return True
    return False


def is_retryable_rate_limit_error(exc: BaseException) -> bool:
    """Return True for SDK/text-shaped LLM rate-limit errors.

    This remains in a lightweight OpenViking utility module for benchmark
    integrations that cannot import heavier runtime dependencies.
    """
    if _structured_rate_limit_match(exc):
        return True
    text = str(exc or "")
    if not text:
        return False
    lower_text = text.lower()
    return any(marker.lower() in lower_text for marker in RETRYABLE_RATE_LIMIT_MARKERS) or bool(
        _RATE_LIMIT_STATUS_RE.search(text)
    )


def rate_limit_retry_delay(attempt: int) -> float:
    """Exponential backoff delay with jitter for LLM rate-limit retries."""
    delay = min(
        RATE_LIMIT_RETRY_MAX_DELAY_SECONDS,
        RATE_LIMIT_RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1)),
    )
    return delay * random.uniform(0.8, 1.2)


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


async def retry_async_iterator(
    func: Callable[[], AsyncIterator[T]],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: bool = True,
    is_retryable: Callable[[Exception], bool] = is_retryable_api_error,
    logger=None,
    operation_name: str = "operation",
) -> AsyncIterator[T]:
    """Retry an async stream only if it fails before emitting output."""
    attempt = 0

    while True:
        emitted = False
        try:
            async for item in func():
                emitted = True
                yield item
            return
        except Exception as error:
            if emitted or max_retries <= 0 or attempt >= max_retries or not is_retryable(error):
                raise

            delay = _compute_delay(
                attempt,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
            )
            if logger:
                logger.warning(
                    f"{operation_name} failed before emitting output "
                    f"(retry {attempt + 1}/{max_retries}): {error}; "
                    f"retrying in {delay:.2f}s"
                )
            await asyncio.sleep(delay)
            attempt += 1


class PrimaryBackupSwitcher:
    """Thread-safe primary/backup switcher with automatic failback logic.

    Credential errors and transient failures exhausted by the active provider
    switch to backup. Then, after either:
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

        Request-level and unknown errors fail fast. Credential-level errors and
        exhausted transient failures switch to the backup.
        """
        error_class = classify_api_error(error)
        if error_class in (
            ERROR_CLASS_AUTH,
            ERROR_CLASS_QUOTA_EXCEEDED,
            ERROR_CLASS_TRANSIENT,
        ):
            with self._lock:
                if not self._using_backup:
                    logger.warning(f"Primary failed with {error_class}, switching to backup")
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


class OrderedCredentialSwitcher:
    """Thread-safe ordered N-credential switcher with hierarchical failback.

    Supports ordered failover across multiple credentials. Credential-level errors
    and exhausted transient failures advance to the next credential.
    After failback thresholds are met, it attempts to move back to a higher-priority
    credential (one step at a time, not all the way back to index 0).

    _active_idx == _n indicates all credentials are exhausted.
    """

    def __init__(
        self,
        n: int,
        failback_timeout_seconds: float = 600.0,  # 10 minutes
        failback_request_count: int = 50,
    ):
        """Initialize the switcher.

        Args:
            n: Number of credentials (must be >= 1)
            failback_timeout_seconds: Time after which to attempt failback
            failback_request_count: Number of requests after which to attempt failback

        Note:
            Failure handling is driven by the error class (see
            ``classify_api_error``):

            - request-level errors (``permanent`` 400 / ``input_too_large`` /
              ``content_safety``) fail fast: the same request fails on every
              credential of the same model, so switching is useless.
            - credential-level ``auth`` errors (401/403) advance to the next
              credential in multi-credential mode; the last (or single)
              credential fails fast.
            - ``quota_exceeded`` and ``transient`` once its retries are
              exhausted advance to the next credential.
            - ``unknown`` fails fast because replay safety is not known.
        """
        if n < 1:
            raise ValueError("Number of credentials must be >= 1")

        # Configuration (read-only after construction)
        self._n = n
        self._failback_timeout = failback_timeout_seconds
        self._failback_request_count = failback_request_count

        # Runtime state (protected by _lock)
        self._lock = threading.Lock()
        self._active_idx = 0
        self._last_switch_time: float = 0.0
        self._active_request_count = 0

    @property
    def n(self) -> int:
        """Get the number of credentials."""
        return self._n

    def maybe_failback(self) -> int:
        """Attempt a one-step failback toward higher-priority credentials.

        If the active credential is not already the highest priority (index 0)
        and a failback threshold (timeout or request count) is met, move the
        active index back one step. This mutates state and must be called only
        when about to issue a request, not for pure observation.

        Returns the (possibly updated) active credential index.
        """
        with self._lock:
            if self._active_idx > 0:
                timer_hit = (time.monotonic() - self._last_switch_time) >= self._failback_timeout
                count_hit = self._active_request_count >= self._failback_request_count
                if timer_hit or count_hit:
                    previous_idx = self._active_idx
                    self._active_idx -= 1
                    self._last_switch_time = time.monotonic()
                    self._active_request_count = 0
                    logger.info(
                        f"Failback condition met (timer={timer_hit}, count={count_hit}), "
                        f"switching active credential from {previous_idx} to {self._active_idx}"
                    )
            return self._active_idx

    def get_active_index(self) -> int:
        """Return the current active credential index (pure read, no side effects).

        Use :meth:`maybe_failback` to trigger failback before issuing a request.
        """
        with self._lock:
            return self._active_idx

    def on_success(self, idx: int) -> None:
        """Record a successful call on the given credential index.

        Increments the request counter for active_idx if idx matches.
        """
        with self._lock:
            if idx == self._active_idx and self._active_idx > 0:
                self._active_request_count += 1

    @staticmethod
    def is_fail_fast(error_class: str) -> bool:
        """Whether an error is request-level and must not try other credentials.

        Request-level errors fail on every credential of the same model. Unknown
        errors also fail fast because issuing the request again may duplicate work.
        """
        return error_class in (
            ERROR_CLASS_PERMANENT,
            ERROR_CLASS_INPUT_TOO_LARGE,
            ERROR_CLASS_CONTENT_SAFETY,
            ERROR_CLASS_UNKNOWN,
        )

    def commit_success(self, idx: int) -> None:
        """Record that credential ``idx`` successfully served a request.

        - If ``idx`` is the current active credential, advance the failback
          request counter (so failback to a higher-priority credential can
          eventually trigger).
        - If ``idx`` differs (a lower/other-priority credential served the
          request after the active one was unavailable), commit it as the new
          active credential (fast failover) and reset failback timers/counters.
        """
        with self._lock:
            if idx == self._active_idx:
                if self._active_idx > 0:
                    self._active_request_count += 1
                return
            logger.info(
                f"Fast failover: credential {idx} served the request; "
                f"switching active credential from {self._active_idx} to {idx}"
            )
            self._active_idx = idx
            self._last_switch_time = time.monotonic()
            self._active_request_count = 0
