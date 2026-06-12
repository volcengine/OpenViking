# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy snapshot helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from openviking.session.train.domain import ExperienceSet
from openviking.telemetry import tracer


@dataclass(slots=True)
class ContentHashPolicySnapshotter:
    """Create deterministic policy snapshot ids from ExperienceSet content."""

    prefix: str = "policy-snapshot"

    @tracer(
        "train.policy_snapshotter.content_hash.snapshot",
        ignore_result=False,
        ignore_args=True,
    )
    async def snapshot(self, policy_set: ExperienceSet, context: Any = None) -> str:
        del context
        payload = {
            "root_uri": policy_set.root_uri,
            "policies": [
                {
                    "name": policy.name,
                    "uri": policy.uri,
                    "version": policy.version,
                    "status": policy.status,
                    "content": policy.content,
                    "metadata": policy.metadata,
                }
                for policy in sorted(policy_set.policies, key=lambda p: p.uri)
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"{self.prefix}:{digest}"
