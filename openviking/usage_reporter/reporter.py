# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage reporter dispatcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from openviking_cli.utils import get_logger

from .extractors import UsageExtractor
from .models import UsageContext, UsageEvent
from .sinks import UsageSink

logger = get_logger(__name__)


@dataclass
class UsageReporter:
    extractors: list[UsageExtractor] = field(default_factory=list)
    sinks: list[UsageSink] = field(default_factory=list)

    async def extract(
        self,
        *,
        messages,
        context: UsageContext,
    ) -> list[UsageEvent]:
        events: list[UsageEvent] = []
        for extractor in self.extractors:
            try:
                events.extend(await extractor.extract(messages=messages, context=context))
            except Exception:
                logger.exception("Usage extractor failed: %s", getattr(extractor, "name", ""))
        return events

    async def report(
        self,
        *,
        events: Iterable[UsageEvent],
        context: UsageContext,
    ) -> None:
        event_list = list(events)
        if not event_list or not self.sinks:
            return
        for sink in self.sinks:
            try:
                await sink.write(events=event_list, context=context)
            except Exception:
                logger.exception("Usage sink failed: %s", type(sink).__name__)

    async def extract_and_report(self, *, messages, context: UsageContext) -> list[UsageEvent]:
        events = await self.extract(messages=messages, context=context)
        await self.report(events=events, context=context)
        return events
