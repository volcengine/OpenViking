# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking HTTP Server module."""


def __getattr__(name: str):
    if name == "create_app":
        from openviking.server.app import create_app

        return create_app
    if name == "run_server":
        from openviking.server.bootstrap import main as run_server

        return run_server
    raise AttributeError(name)


__all__ = ["create_app", "run_server"]
