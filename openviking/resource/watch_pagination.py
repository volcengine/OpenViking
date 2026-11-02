# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Portable Watch keyset cursors; visibility is always enforced by the store."""

import base64
import binascii
import hashlib
import json
import math
from typing import Optional

from openviking.resource.watch_types import WatchAuthority
from openviking_cli.exceptions import InvalidArgumentError

DEFAULT_WATCH_PAGE_SIZE = 100
MAX_WATCH_PAGE_SIZE = 200


class WatchPageQuery:
    """Bind a position to the store, caller and filter without node-local secrets.

    Cursors are positions, not authorization tokens. Editing a position can only
    skip visible rows; every page applies the caller's authority independently.
    """

    def __init__(
        self,
        authority: WatchAuthority,
        *,
        active_only: bool,
        limit: int,
        cursor: Optional[str],
        store_scope: str,
    ) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_WATCH_PAGE_SIZE:
            raise InvalidArgumentError(f"limit must be between 1 and {MAX_WATCH_PAGE_SIZE}")
        self.limit = limit
        self.scope = hashlib.sha256(
            json.dumps(
                [store_scope, authority.account_id, authority.user_id, authority.role, active_only],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.after: Optional[tuple[float, str]] = None
        if cursor is not None:
            try:
                if len(cursor) > 2048:
                    raise ValueError("cursor too long")
                data = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
                if data["v"] != 1 or data["scope"] != self.scope:
                    raise ValueError("cursor scope mismatch")
                timestamp, task_id = data["time"], data["id"]
                if (
                    type(timestamp) not in (int, float)
                    or not math.isfinite(timestamp)
                    or not isinstance(task_id, str)
                    or not 1 <= len(task_id) <= 255
                ):
                    raise ValueError("invalid cursor position")
                self.after = float(timestamp), task_id
            except (
                ValueError,
                TypeError,
                KeyError,
                OverflowError,
                UnicodeError,
                binascii.Error,
            ) as exc:
                raise InvalidArgumentError("Invalid watch cursor; reload the list") from exc

    def next_cursor(self, position: tuple[float, str]) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(
                {"v": 1, "scope": self.scope, "time": position[0], "id": position[1]},
                separators=(",", ":"),
            ).encode()
        ).decode()
