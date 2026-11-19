# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Field allowlist shared by log and trace adapters.

Content safety policy:
https://github.com/volcengine/OpenViking/blob/main/docs/repository/LOGGING.md
"""

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Mapping

DEFAULT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s pid=%(process)d "
    "request_id=%(request_id)s trace_id=%(trace_id)s message=%(message)s"
)
LEGACY_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

LOG_FIELDS = frozenset(
    "ts account_id user_id request_id trace_id span_id operation http_method http_route "
    "http_status_code duration_ms role task_id task_type task_status task_stage "
    "attempt retry_limit delay queue_depth error_code error_type file_type "
    "target_id "
    "degraded sensitive truncated payload total failed skipped original_length".split()
)
_TEXT_ID = re.compile(r"^[A-Za-z0-9_.:/{}@-]{1,256}$")
_COUNTS = frozenset(
    "http_status_code duration_ms attempt retry_limit delay queue_depth total failed skipped original_length".split()
)
_FLAGS = frozenset("degraded sensitive truncated payload".split())


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def safe_uri_for_log(uri: str) -> str:
    """Keep only the URI owner prefix, with an opaque locator for everything below it."""
    if re.fullmatch(r"viking://user/[A-Za-z0-9_.:-]+/hash/[0-9a-f]{8}", uri):
        return uri
    if uri.startswith("viking://user/"):
        owner = uri[len("viking://user/") :].split("/", 1)[0]
        if _TEXT_ID.fullmatch(owner):
            return f"viking://user/{owner}/hash/{short_hash(uri)}"
    return f"uri:{short_hash(uri)}"


def safe_path_for_log(path: str, workspace: str | Path | None = None) -> str:
    """Return an opaque locator; arbitrary file names and tenant layouts stay private."""
    if workspace is not None:
        try:
            relative = Path(path).resolve().relative_to(Path(workspace).resolve())
            return f"workspace:{short_hash(str(relative))}"
        except (ValueError, OSError):
            pass
    return f"path:{short_hash(path)}"


def safe_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Keep bounded primitive fields; never stringify arbitrary objects or exceptions."""
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in LOG_FIELDS:
            continue
        if value is None:
            safe[key] = None
        elif key in _FLAGS:
            if isinstance(value, bool):
                safe[key] = value
        elif key in _COUNTS:
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
                and (
                    (isinstance(value, int) and value.bit_length() <= 63)
                    or (isinstance(value, float) and math.isfinite(value))
                )
            ):
                safe[key] = value
        elif isinstance(value, str):
            if key == "target_id":
                if value.startswith("viking://"):
                    safe[key] = safe_uri_for_log(value)
                    continue
                if "/" in value:
                    safe[key] = safe_path_for_log(value)
                    continue
            if "peers/" not in value and "://" not in value and _TEXT_ID.fullmatch(value):
                safe[key] = value
    return safe
