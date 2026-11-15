# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deliver matched Rules as one complete block, separate from memory summaries."""

import asyncio
from typing import Any

from openviking.server.identity import RequestContext
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
RULE_CONTEXT_TIMEOUT_SECONDS = 10.0
INCOMPLETE_NOTICE = (
    "OpenViking Rules check is incomplete. Applicable constraints have not been fully checked; "
    "do not treat this as no applicable Rules."
)


def _incomplete(reason: str, count: int = 0) -> dict:
    return {
        "status": "incomplete",
        "rendered": INCOMPLETE_NOTICE,
        "used_tokens": estimate_text_tokens(INCOMPLETE_NOTICE),
        "delivery": {"complete": False, "reason": reason, "rule_count": count},
    }


async def assemble_rules(service: Any, ctx: RequestContext, options: dict, budget: int) -> dict:
    # Import at the boundary: RuleService itself depends on retrieval services.
    from openviking.service.rule_service import RuleContextRequest, RuleSearchRequest

    request = RuleContextRequest.model_validate(options)
    try:
        result = await asyncio.wait_for(
            service.rules.search_rules(
                RuleSearchRequest.model_validate(request.model_dump(exclude={"max_tokens"})),
                ctx=ctx,
            ),
            timeout=RULE_CONTEXT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Do not expose task text, model responses or credentials through errors.
        logger.warning("Rule context lookup failed (%s)", type(exc).__name__)
        return _incomplete("lookup_failed")

    count = len(result["rules"])
    rendered = result["rendered"] if count else ""
    used = estimate_text_tokens(rendered) if rendered else 0
    complete = result.get("delivery", {}).get("complete") is True
    fits = used <= min(request.max_tokens, budget)
    delivered = complete and fits
    output = (
        {
            "status": "ok",
            "rendered": rendered,
            "used_tokens": used,
            "delivery": {"complete": True, "rule_count": count, "reason": "complete"},
        }
        if delivered
        else _incomplete("budget_exceeded" if not fits else "upstream_incomplete", count)
    )
    if result.get("warning"):
        output["warning"] = result["warning"]
    return output
