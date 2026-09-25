# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Built-in sink that hands usage events to the in-process observability bus."""

from __future__ import annotations

from datetime import datetime, timezone

from openviking.observability.events import ObservabilityEvent, get_event_bus

from .models import UsageEvent

EXPERIENCE_USAGE_EVENT_NAME = "experience.usage"


def _event_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class EventBusUsageSink:
    """Publish usage events so local subscribers such as Usage/Audit can count them.

    Publishing never blocks: with no subscriber registered the event is dropped.
    """

    async def write(self, *, events: list[UsageEvent]) -> None:
        bus = get_event_bus()
        for event in events:
            bus.publish(
                ObservabilityEvent(
                    event_name=EXPERIENCE_USAGE_EVENT_NAME,
                    payload={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "resource_uri": event.resource_uri,
                    },
                    timestamp=_event_time(event.occurred_at),
                    account_id=event.account_id,
                    user_id=event.user_id,
                )
            )
