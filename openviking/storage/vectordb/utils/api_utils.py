# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from typing import Any, Dict, Optional


def form_error(err: Any, code: int = 400, data: Optional[Dict] = None) -> tuple:
    if data is None:
        data = {}
    return {"code": code, "message": str(err), "data": data}, 400


def form_res(data: Any = None, code: int = 200, message: str = "success") -> tuple:
    if data is None:
        data = {}
    return {"code": code, "message": str(message), "data": data}, 200
