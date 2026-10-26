# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Command-line entry point for the LlamaParse bridge."""

import uvicorn

from .bridge import create_app, load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
    )


if __name__ == "__main__":
    main()
