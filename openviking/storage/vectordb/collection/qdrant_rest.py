# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Small dependency-free Qdrant REST client."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class QdrantError(RuntimeError):
    """A bounded Qdrant REST failure."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        path: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.path = path
        self.status = status


def validate_qdrant_version(version: Any) -> None:
    """Fail closed unless the server supports native conditional point writes."""
    if not isinstance(version, str):
        raise QdrantError("Qdrant version is missing from the root response")
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        version.strip(),
    )
    if match is None:
        raise QdrantError(f"Qdrant version is unparseable: {version!r}")
    if tuple(int(part) for part in match.groups()) < (1, 16, 0):
        raise QdrantError(
            f"Qdrant version {version!r} does not support the required "
            "conditional ownership contract (minimum 1.16.0)"
        )


def _validate_timeout_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Qdrant timeout_seconds must be a finite number greater than zero")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Qdrant timeout_seconds must be a finite number greater than zero"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Qdrant timeout_seconds must be a finite number greater than zero")
    return timeout


class QdrantRestClient:
    """Minimal JSON REST transport with injectable opener for tests."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        normalized = str(base_url).strip().rstrip("/")
        if not normalized:
            raise ValueError("Qdrant URL must not be empty")
        self._base_url = normalized
        self._api_key = api_key
        self._timeout_seconds = _validate_timeout_seconds(timeout_seconds)
        self._opener = opener or urlopen
        self._version_checked = False

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def ensure_supported_version(self) -> None:
        if not self._version_checked:
            response = self.request("GET", "/")
            result = response.get("result", response)
            validate_qdrant_version(result.get("version") if isinstance(result, dict) else None)
            self._version_checked = True

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            path = f"/{path}"
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self._base_url}{path}{query}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **({"api-key": self._api_key} if self._api_key else {}),
            },
            method=method.upper(),
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            detail = raw.decode("utf-8", errors="replace")[:1000]
            raise QdrantError(
                f"Qdrant HTTP {exc.code} {method.upper()} {path}: {detail}",
                method=method.upper(),
                path=path,
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise QdrantError(
                f"Qdrant transport error {method.upper()} {path}: {exc.reason}",
                method=method.upper(),
                path=path,
            ) from exc
        except TimeoutError as exc:
            raise QdrantError(
                f"Qdrant request timed out {method.upper()} {path}",
                method=method.upper(),
                path=path,
            ) from exc

        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QdrantError(
                f"Qdrant returned invalid JSON for {method.upper()} {path}",
                method=method.upper(),
                path=path,
            ) from exc
        if not isinstance(decoded, dict):
            raise QdrantError(
                f"Qdrant returned a non-object response for {method.upper()} {path}",
                method=method.upper(),
                path=path,
            )
        return decoded
