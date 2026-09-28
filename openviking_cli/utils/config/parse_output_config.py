# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ParseOutputConfig(BaseModel):
    """Where parsers write intermediate artifacts.

    ``agfs`` (default) writes to the shared AGFS ``viking://temp`` space. ``local``
    keeps parser intermediates on the current worker; add-resources commits the
    required bytes to the formal AGFS tree before enqueueing downstream work.
    """

    mode: str = Field(
        default="agfs",
        description="Parse artifact backend: 'agfs' (shared temp) or 'local' (local dir).",
    )
    local_root: Optional[str] = Field(
        default=None,
        description=(
            "Directory root for the 'local' backend. Defaults to a dedicated subdir "
            "under the system temp dir when unset."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode not in {"agfs", "local"}:
            raise ValueError("storage.parse_output.mode must be 'agfs' or 'local'")
        return self

    def resolved_local_root(self) -> str:
        """Return the local root, falling back to a dedicated system-temp subdir."""
        if self.local_root:
            return str(Path(self.local_root).expanduser())
        import tempfile

        return str(Path(tempfile.gettempdir()) / "openviking-parse")
