# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""共享的数据模型定义。"""

from dataclasses import dataclass, field
from typing import Dict

from openviking.server.identity import AccountNamespacePolicy, Role


@dataclass
class UserKeyEntry:
    """内存中的用户密钥索引条目。"""

    account_id: str
    user_id: str
    role: Role
    key_or_hash: str
    is_hashed: bool


@dataclass
class AccountInfo:
    """内存中的账户信息。"""

    created_at: str
    users: Dict[str, dict] = field(default_factory=dict)
    namespace_policy: AccountNamespacePolicy = field(default_factory=AccountNamespacePolicy)
