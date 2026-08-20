# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Small dependency-free Qdrant REST client."""

from __future__ import annotations

import json
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
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener or urlopen

    @property
    def base_url(self) -> str:
        return self._base_url

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
