# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""``python -m openviking_openwebui`` entry point."""

from __future__ import annotations

import uvicorn

from .config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "openviking_openwebui.server:app",
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
