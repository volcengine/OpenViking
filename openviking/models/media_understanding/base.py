# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Base interface for media understanding providers."""

import asyncio
import tempfile
import threading
import weakref
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

MediaType = Literal["audio", "video"]
ContentWriter = Callable[[Path], Awaitable[None]]


class MediaUnderstandingClient(ABC):
    """Concurrency-limited interface for audio and video understanding."""

    def __init__(self, max_concurrent: int) -> None:
        self._max_concurrent = max(1, int(max_concurrent))
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, weakref.ReferenceType[asyncio.Semaphore]
        ] = weakref.WeakKeyDictionary()
        self._semaphore_lock = threading.Lock()

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        with self._semaphore_lock:
            semaphore_ref = self._semaphores.get(loop)
            semaphore = semaphore_ref() if semaphore_ref is not None else None
            if semaphore is None:
                semaphore = asyncio.Semaphore(self._max_concurrent)
                self._semaphores[loop] = weakref.ref(semaphore)
            return semaphore

    async def understand(
        self,
        *,
        content: bytes,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        """Understand one audio or video file and return only output text."""
        async with self._get_semaphore():
            return await self._understand(
                content=content,
                filename=filename,
                media_type=media_type,
                prompt=prompt,
            )

    async def understand_from_loader(
        self,
        *,
        content_loader: Callable[[], Awaitable[bytes]],
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        """Load and understand media while holding the provider concurrency permit."""
        async with self._get_semaphore():
            content = await content_loader()
            return await self._understand(
                content=content,
                filename=filename,
                media_type=media_type,
                prompt=prompt,
            )

    async def understand_from_writer(
        self,
        *,
        content_writer: ContentWriter,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        """Stage and understand media while holding the concurrency permit."""
        async with self._get_semaphore():
            suffix = Path(filename).suffix.lower()
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=suffix, delete=False
                ) as temp_file:
                    temp_path = Path(temp_file.name)
                await content_writer(temp_path)
                return await self._understand_path(
                    path=temp_path,
                    filename=filename,
                    media_type=media_type,
                    prompt=prompt,
                )
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

    async def _understand_path(
        self,
        *,
        path: Path,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        """Default path hook for byte-oriented providers."""
        return await self._understand(
            content=path.read_bytes(),
            filename=filename,
            media_type=media_type,
            prompt=prompt,
        )

    @abstractmethod
    async def _understand(
        self,
        *,
        content: bytes,
        filename: str,
        media_type: MediaType,
        prompt: str,
    ) -> str:
        raise NotImplementedError
