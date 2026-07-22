#!/usr/bin/env python3
"""Persistent first-user-message cache for Tau2 rollout cases."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

CACHE_VERSION = 1

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class FirstUserCacheResult:
    message: str
    hit: bool
    path: Path | None


class FirstUserMessageCache:
    """Record on miss and replay on hit, with one lock per cache key."""

    def __init__(self, root: str | Path, *, enabled: bool = True):
        self.root = Path(root).expanduser()
        self.enabled = bool(enabled)

    def run(
        self,
        identity: Mapping[str, Any],
        reset_user: Callable[[str | None], str],
    ) -> FirstUserCacheResult:
        normalized_identity = _normalized_identity(identity)
        if not self.enabled:
            return FirstUserCacheResult(
                message=_required_message(reset_user(None)),
                hit=False,
                path=None,
            )

        path = self._path(normalized_identity)
        with _key_lock(path):
            cached = self._load(path, normalized_identity)
            if cached is not None:
                actual = _required_message(reset_user(cached))
                if actual != cached:
                    raise RuntimeError(
                        f"fixed first user replay mismatch for {normalized_identity['task_signature']}"
                    )
                return FirstUserCacheResult(message=cached, hit=True, path=path)

            message = _required_message(reset_user(None))
            self._write(path, normalized_identity, message)
            return FirstUserCacheResult(message=message, hit=False, path=path)

    def _path(self, identity: dict[str, Any]) -> Path:
        stable = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = sha256(stable.encode("utf-8")).hexdigest()[:24]
        signature = str(identity["task_signature"])
        parts = signature.split(":")
        group = _slug("_".join(parts[1:3]) if len(parts) >= 3 else "tau2")
        case_name = _slug(signature)
        return self.root / group / f"{case_name}_{digest}.json"

    @staticmethod
    def _load(path: Path, identity: dict[str, Any]) -> str | None:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("cache_version") != CACHE_VERSION:
            raise ValueError(f"unsupported first user cache version: {path}")
        if payload.get("identity") != identity:
            raise ValueError(f"first user cache identity mismatch: {path}")
        message = _required_message(payload.get("message"))
        expected_hash = sha256(message.encode("utf-8")).hexdigest()
        if payload.get("message_sha256") != expected_hash:
            raise ValueError(f"first user cache message hash mismatch: {path}")
        return message

    @staticmethod
    def _write(path: Path, identity: dict[str, Any], message: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_version": CACHE_VERSION,
            "identity": identity,
            "message": message,
            "message_sha256": sha256(message.encode("utf-8")).hexdigest(),
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _normalized_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(
        json.dumps(dict(identity), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    signature = str(normalized.get("task_signature") or "").strip()
    if not signature:
        raise ValueError("first user cache identity requires task_signature")
    normalized["task_signature"] = signature
    return normalized


def _required_message(value: Any) -> str:
    message = str(value or "")
    if not message.strip():
        raise ValueError("first user message must not be empty")
    return message


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "tau2"


def _key_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())
