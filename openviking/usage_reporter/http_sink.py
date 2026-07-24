# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable HTTP usage event sink."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import UsageEvent

logger = logging.getLogger(__name__)


class HttpUsageSink:
    """Persist usage events before delivering them to an HTTP collector."""

    def __init__(
        self,
        *,
        endpoint: str,
        resource_id_env: str = "OV_RESOURCE_ID",
        outbox_dir: str | os.PathLike[str] | None = None,
        request_timeout_seconds: float = 10.0,
        inflight_lease_seconds: float = 60.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 300.0,
        max_batch_bytes: int = 1024 * 1024,
        max_outbox_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self._endpoint = endpoint.strip()
        self._resource_id = os.environ.get(resource_id_env, "").strip()
        if not self._endpoint:
            raise ValueError("endpoint is required")
        if not self._resource_id:
            raise ValueError(f"{resource_id_env} is required")

        self._outbox_dir = (
            Path(outbox_dir)
            if outbox_dir is not None
            else Path.home() / ".openviking" / "data" / ".usage_outbox"
        )
        self._pending_dir = self._outbox_dir / "pending"
        self._inflight_dir = self._outbox_dir / "inflight"
        self._dead_letter_dir = self._outbox_dir / "dead_letter"
        self._metadata_dir = self._outbox_dir / "metadata"
        for directory in (
            self._pending_dir,
            self._inflight_dir,
            self._dead_letter_dir,
            self._metadata_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self._request_timeout_seconds = float(request_timeout_seconds)
        self._inflight_lease_seconds = float(inflight_lease_seconds)
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)
        self._max_batch_bytes = int(max_batch_bytes)
        self._max_outbox_bytes = int(max_outbox_bytes)
        if self._request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self._inflight_lease_seconds <= 0:
            raise ValueError("inflight_lease_seconds must be positive")
        if self._inflight_lease_seconds <= self._request_timeout_seconds:
            raise ValueError(
                "inflight_lease_seconds must be greater than request_timeout_seconds"
            )
        if self._retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be non-negative")
        if self._retry_max_seconds < 0:
            raise ValueError("retry_max_seconds must be non-negative")
        if self._max_batch_bytes <= 0:
            raise ValueError("max_batch_bytes must be positive")
        if self._max_outbox_bytes <= 0:
            raise ValueError("max_outbox_bytes must be positive")
        self._outbox_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._start_worker()

    async def write(self, *, events: list[UsageEvent]) -> None:
        if not events:
            return
        await asyncio.to_thread(self._persist_events, events)

    def close(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._worker is not None:
            self._worker.join(timeout=self._request_timeout_seconds + 1)

    def _persist_events(self, events: list[UsageEvent]) -> None:
        records: list[dict[str, Any]] = []
        for event in events:
            record = event.to_dict()
            event_id = str(record.get("event_id") or "").strip()
            if not event_id:
                raise ValueError("event_id is required")
            record["prefix"] = self._resource_id
            records.append(record)

        batch: list[dict[str, Any]] = []
        for record in records:
            candidate = [*batch, record]
            if batch and (
                len(candidate) > 100
                or self._encoded_payload_size(candidate) > self._max_batch_bytes
            ):
                self._persist_batch(batch)
                batch = [record]
            else:
                batch = candidate
        if batch:
            self._persist_batch(batch)
        self._wake_event.set()

    def _persist_batch(self, events: list[dict[str, Any]]) -> bool:
        return self._persist_batches([events])

    def _persist_batches(
        self,
        event_batches: list[list[dict[str, Any]]],
        *,
        replace_path: Path | None = None,
    ) -> bool:
        encoded_batches: list[tuple[dict[str, Any], bytes, Path, Path]] = []
        for index, events in enumerate(event_batches):
            payload = self._build_payload(events)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            file_stem = f"{time.time_ns()}_{index}_{payload['batch_id']}"
            encoded_batches.append(
                (
                    payload,
                    encoded,
                    self._pending_dir / f".{file_stem}.tmp",
                    self._pending_dir / f"{file_stem}.json",
                )
            )

        incoming_bytes = sum(len(encoded) for _, encoded, _, _ in encoded_batches)
        with self._outbox_lock:
            reclaimable_bytes = 0
            if replace_path is not None:
                try:
                    reclaimable_bytes = replace_path.stat().st_size
                except FileNotFoundError:
                    return False
            if not self._ensure_outbox_capacity(
                incoming_bytes,
                reclaimable_bytes=reclaimable_bytes,
            ):
                batch_ids = [payload["batch_id"] for payload, _, _, _ in encoded_batches]
                logger.warning(
                    "Dropping OpenViking usage batch because the outbox is full, "
                    "batch_ids=%s size=%s max_outbox_bytes=%s",
                    batch_ids,
                    incoming_bytes,
                    self._max_outbox_bytes,
                )
                return False
            temporary_paths: list[Path] = []
            try:
                for _payload, encoded, temporary_path, pending_path in encoded_batches:
                    temporary_paths.append(temporary_path)
                    with temporary_path.open("wb") as output:
                        output.write(encoded)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary_path, pending_path)
                self._fsync_directory(self._pending_dir)
                if replace_path is not None:
                    replace_path.unlink()
                    self._fsync_directory(replace_path.parent)
            finally:
                for temporary_path in temporary_paths:
                    try:
                        temporary_path.unlink()
                    except FileNotFoundError:
                        pass
        return True

    def _ensure_outbox_capacity(
        self,
        incoming_bytes: int,
        *,
        reclaimable_bytes: int = 0,
    ) -> bool:
        if incoming_bytes > self._max_outbox_bytes:
            return False

        eviction_candidates: list[Path] = []
        total_bytes = 0
        for directory in (
            self._dead_letter_dir,
            self._pending_dir,
            self._inflight_dir,
        ):
            for path in directory.glob("*.json"):
                try:
                    total_bytes += path.stat().st_size
                except FileNotFoundError:
                    continue
                if directory != self._inflight_dir:
                    eviction_candidates.append(path)

        effective_total_bytes = max(0, total_bytes - reclaimable_bytes)
        if effective_total_bytes + incoming_bytes <= self._max_outbox_bytes:
            return True

        def _eviction_order(path: Path) -> tuple[int, int, str]:
            try:
                modified_at = path.stat().st_mtime_ns
            except FileNotFoundError:
                modified_at = 0
            directory_priority = 0 if path.parent == self._dead_letter_dir else 1
            return directory_priority, modified_at, path.name

        for path in sorted(eviction_candidates, key=_eviction_order):
            try:
                size = path.stat().st_size
                path.unlink()
            except FileNotFoundError:
                continue
            effective_total_bytes -= size
            logger.warning("Evicted OpenViking usage outbox batch: %s", path)
            if effective_total_bytes + incoming_bytes <= self._max_outbox_bytes:
                return True
        return effective_total_bytes + incoming_bytes <= self._max_outbox_bytes

    def _build_payload(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        event_ids = "\n".join(str(event["event_id"]) for event in events)
        batch_id = f"ub_{hashlib.sha256(event_ids.encode('utf-8')).hexdigest()}"
        created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00",
            "Z",
        )
        return {
            "schema_version": "v1",
            "resource_id": self._resource_id,
            "batch_id": batch_id,
            "created_at": created_at,
            "attempt": 0,
            "next_retry_at": created_at,
            "events": events,
        }

    def _encoded_payload_size(self, events: list[dict[str, Any]]) -> int:
        return len(
            json.dumps(
                self._build_payload(events),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _start_worker(self) -> None:
        self._recover_stale_inflight()
        with self._outbox_lock:
            self._ensure_outbox_capacity(0)
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="openviking-usage-http-sink",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._recover_stale_inflight()
                handled_batch = self._handle_next_batch()
            except Exception:
                logger.exception("OpenViking usage outbox worker failed")
                handled_batch = False
            if handled_batch:
                continue
            self._wake_event.wait(timeout=0.05)
            self._wake_event.clear()

    def _handle_next_batch(self) -> bool:
        now = datetime.now(timezone.utc)
        for pending_path in sorted(self._pending_dir.glob("*.json")):
            try:
                payload = self._read_payload(pending_path)
            except (OSError, ValueError, json.JSONDecodeError):
                self._move_to_dead_letter(pending_path)
                continue
            next_retry_at = self._parse_datetime(str(payload.get("next_retry_at") or ""))
            if next_retry_at is not None and next_retry_at > now:
                continue

            inflight_path = self._inflight_dir / pending_path.name
            try:
                os.replace(pending_path, inflight_path)
                os.utime(inflight_path, None)
            except FileNotFoundError:
                continue

            try:
                status = self._post_payload(payload)
            except Exception:
                logger.exception(
                    "OpenViking usage delivery failed unexpectedly, batch_id=%s",
                    payload.get("batch_id"),
                )
                self._schedule_retry(inflight_path, payload)
                return True
            if 200 <= status < 300:
                try:
                    inflight_path.unlink()
                except FileNotFoundError:
                    pass
                return True
            if status == 413:
                self._split_batch(inflight_path, payload)
                return True
            if status in (400, 422):
                self._move_to_dead_letter(inflight_path)
                return True

            self._schedule_retry(inflight_path, payload)
            return True
        return False

    def _split_batch(self, inflight_path: Path, payload: dict[str, Any]) -> None:
        events = payload.get("events")
        if not isinstance(events, list) or len(events) < 2:
            self._move_to_dead_letter(inflight_path)
            return
        midpoint = len(events) // 2
        if not self._persist_batches(
            [events[:midpoint], events[midpoint:]],
            replace_path=inflight_path,
        ):
            self._move_to_dead_letter(inflight_path)
        self._wake_event.set()

    def _post_payload(self, payload: dict[str, Any]) -> int:
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "V-Resource-Id": self._resource_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._request_timeout_seconds,
            ) as response:
                response.read()
                return response.status
        except urllib.error.HTTPError as error:
            error.read()
            return error.code
        except (OSError, TimeoutError, urllib.error.URLError):
            return 0

    def _schedule_retry(self, inflight_path: Path, payload: dict[str, Any]) -> None:
        attempt = int(payload.get("attempt") or 0) + 1
        delay = min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** max(0, attempt - 1)),
        )
        if delay > 0:
            delay *= random.uniform(0.8, 1.2)
        payload["attempt"] = attempt
        payload["next_retry_at"] = (
            datetime.fromtimestamp(time.time() + delay, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        try:
            self._replace_payload(inflight_path, payload)
            os.replace(inflight_path, self._pending_dir / inflight_path.name)
        except FileNotFoundError:
            return
        self._wake_event.set()

    def _replace_payload(self, path: Path, payload: dict[str, Any]) -> None:
        temporary_path = path.with_name(f".{path.name}.tmp")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with temporary_path.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
            self._fsync_directory(path.parent)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_payload(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as source:
            payload = json.load(source)
        if not isinstance(payload, dict):
            raise ValueError("outbox payload must be an object")
        return payload

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _move_to_dead_letter(self, path: Path) -> None:
        try:
            os.replace(path, self._dead_letter_dir / path.name)
        except FileNotFoundError:
            return

    def _recover_stale_inflight(self) -> None:
        stale_before = time.time() - self._inflight_lease_seconds
        with self._outbox_lock:
            for inflight_path in self._inflight_dir.glob("*.json"):
                try:
                    if inflight_path.stat().st_mtime > stale_before:
                        continue
                    os.replace(inflight_path, self._pending_dir / inflight_path.name)
                except FileNotFoundError:
                    continue
