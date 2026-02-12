# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Main entry point for `python -m openviking`."""

from openviking_cli.cli.main import app


def main() -> None:
    """Run the OpenViking CLI."""
    app()


if __name__ == "__main__":
    main()
