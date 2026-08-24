"""Non-collected local fakes shared by stream configuration tests."""

from types import SimpleNamespace


class MockDelta:
    """Mock delta object for streaming chunks."""

    def __init__(self, content=None):
        self.content = content


class MockChoice:
    """Mock choice object for streaming chunks."""

    def __init__(self, delta=None):
        self.delta = delta


class MockChunk:
    """Mock streaming chunk with optional content and usage."""

    def __init__(self, content=None, usage=None):
        self.choices = [MockChoice(delta=MockDelta(content=content))] if content is not None else []
        self.usage = usage


class MockUsage:
    """Mock usage object."""

    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class DetailedMockUsage(MockUsage):
    def __init__(
        self,
        prompt_tokens=0,
        completion_tokens=0,
        cached_tokens=0,
        reasoning_tokens=0,
    ):
        super().__init__(prompt_tokens, completion_tokens)
        self.total_tokens = prompt_tokens + completion_tokens
        self.prompt_tokens_details = SimpleNamespace(cached_tokens=cached_tokens)
        self.completion_tokens_details = SimpleNamespace(reasoning_tokens=reasoning_tokens)


class ScriptedSyncStream:
    """Synchronous stream that replays events and tracks cleanup."""

    def __init__(self, events, close_error=None):
        self._events = iter(events)
        self.close_error = close_error
        self.close_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self._events)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class ScriptedAsyncStream:
    """Asynchronous stream that replays events and tracks cleanup."""

    def __init__(self, events, close_error=None, close_started=None, close_release=None):
        self._events = iter(events)
        self.close_error = close_error
        self.close_started = close_started
        self.close_release = close_release
        self.close_count = 0
        self.cleanup_finished = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.close_count += 1
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.cleanup_finished = True
        if self.close_error is not None:
            raise self.close_error


class NonAwaitableCloseStream(ScriptedAsyncStream):
    """Async stream where ``close`` is deliberately not awaitable."""

    def __init__(self, events):
        super().__init__(events)
        self.aclose_count = 0

    def close(self):
        self.close_count += 1
        self.cleanup_finished = True

    async def aclose(self):
        self.aclose_count += 1


class AcloseOnlyStream:
    """Async stream exposing only the ``aclose`` cleanup API."""

    def __init__(self, events):
        self._events = iter(events)
        self.aclose_count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        return item

    async def aclose(self):
        self.aclose_count += 1
