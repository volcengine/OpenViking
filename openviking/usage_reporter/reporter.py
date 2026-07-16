# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Usage reporter dispatcher."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
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
    sink_timeout_seconds: float = 5.0

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
    ) -> None:
        event_list = list(events)
        if not event_list or not self.sinks:
            return
        await asyncio.gather(
            *(self._report_to_sink(sink=sink, events=event_list) for sink in self.sinks)
        )

    async def _report_to_sink(self, *, sink: UsageSink, events: list[UsageEvent]) -> None:
        try:
            await asyncio.wait_for(
                sink.write(events=events),
                timeout=self.sink_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "Usage sink timed out after %.1fs: %s",
                self.sink_timeout_seconds,
                type(sink).__name__,
            )
        except Exception:
            logger.exception("Usage sink failed: %s", type(sink).__name__)

    async def extract_and_report(self, *, messages, context: UsageContext) -> list[UsageEvent]:
        events = await self.extract(messages=messages, context=context)
        await self.report(events=events)
        return events

    async def _close_sink(self, close) -> None:  # noqa: ANN001
        if inspect.iscoroutinefunction(close):
            await close()
            return
        result_future: concurrent.futures.Future = concurrent.futures.Future()

        def _run_sync_close() -> None:
            try:
                result_future.set_result(close())
            except concurrent.futures.InvalidStateError:
                # The timeout cancelled the waiter; the daemon thread may finish later.
                pass
            except Exception as exc:
                try:
                    result_future.set_exception(exc)
                except concurrent.futures.InvalidStateError:
                    pass

        threading.Thread(
            target=_run_sync_close,
            name="openviking-usage-sink-close",
            daemon=True,
        ).start()
        result = await asyncio.wrap_future(result_future)
        if inspect.isawaitable(result):
            await result

    async def close(self) -> None:
        for sink in self.sinks:
            close = getattr(sink, "close", None)
            if not callable(close):
                continue
            try:
                await asyncio.wait_for(
                    self._close_sink(close),
                    timeout=self.sink_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "Usage sink close timed out after %.1fs: %s",
                    self.sink_timeout_seconds,
                    type(sink).__name__,
                )
            except Exception:
                logger.exception("Usage sink close failed: %s", type(sink).__name__)
