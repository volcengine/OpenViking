# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Built web-studio assets, populated by the docker web-studio-builder stage.

The actual SPA source lives at the repository root in ``web-studio/``. During
the docker build the Vite output (``web-studio/dist``) is copied into
``openviking/web_studio/dist`` so the python wheel ships the bundle as
package-data. The server resolves ``dist/`` relative to this package at
runtime; if it is absent (e.g. ``pip install`` without running ``npm run
build`` first) the /studio routes simply aren't registered.
"""
