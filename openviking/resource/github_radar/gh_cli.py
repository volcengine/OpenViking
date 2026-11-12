# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Thin async wrapper around the ``gh`` CLI.

Riding ``gh`` instead of raw HTTP keeps the token out of OpenViking config: the
CLI resolves its credential from the host keyring, and requests land in the
core 5000/hour rate-limit bucket rather than the tiny search buckets. A missing
CLI is an operational condition, not an import error, so detection is explicit.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, Dict, List, Optional, Sequence

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class GhCliError(RuntimeError):
    """Raised when a ``gh`` invocation fails or returns unusable output."""


class GhCliUnavailable(GhCliError):
    """Raised when the ``gh`` binary cannot be found on PATH."""


_RETRY_STATUSES = ("429", "500", "502", "503", "504")
_MAX_ATTEMPTS = 3


def have_gh() -> bool:
    return shutil.which("gh") is not None


class GhCli:
    """Minimal ``gh api`` client with bounded retries on throttle/server errors."""

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self._timeout = timeout_seconds

    async def api_get_json(
        self,
        path: str,
        params: Optional[Dict[str, str]] = None,
    ) -> Any:
        """GET a REST endpoint and parse its JSON payload.

        ``-X GET`` is mandatory: ``gh api`` silently upgrades a request carrying
        ``-f`` fields to POST, which the read endpoints reject with a misleading
        422.
        """
        if not have_gh():
            raise GhCliUnavailable("the `gh` CLI is not on PATH")

        argv: List[str] = ["gh", "api", "-X", "GET", path]
        for key, value in (params or {}).items():
            argv += ["-f", f"{key}={value}"]

        last_error: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                raw = await self._run(argv)
            except GhCliError as exc:
                last_error = exc
                if not self._retryable(exc) or attempt == _MAX_ATTEMPTS - 1:
                    raise
                delay = 2**attempt
                logger.warning(
                    "[PrRadar] gh call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            try:
                return json.loads(raw or "null")
            except json.JSONDecodeError as exc:
                raise GhCliError(f"gh returned non-JSON for {path}: {exc}") from exc
        raise GhCliError(f"gh call exhausted retries for {path}: {last_error}")

    async def _run(self, argv: Sequence[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise GhCliUnavailable(f"cannot spawn `gh`: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise GhCliError(f"gh timed out after {self._timeout}s: {' '.join(argv)}") from exc
        except asyncio.CancelledError:
            # Task cancellation must not leak the child process: communicate()
            # being cancelled closes the pipes but does not terminate `gh`.
            # Kill it and re-raise so the cancellation still propagates.
            proc.kill()
            raise

        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:400]
            raise GhCliError(f"gh exited {proc.returncode}: {detail}")
        return stdout.decode(errors="replace")

    @staticmethod
    def _retryable(error: GhCliError) -> bool:
        message = str(error).lower()
        if "timed out" in message:
            # Local timeout raised by _run: the server may simply be slow.
            return True
        if "403" in message and "rate limit" in message:
            # GitHub's primary rate-limit bucket answers 403 rather than 429.
            # A plain 403 (permission denied) stays non-retryable.
            return True
        return any(code in message for code in _RETRY_STATUSES)
