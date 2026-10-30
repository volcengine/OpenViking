"""Boundary and serialization tests against the actual copied legacy SDK types."""

import asyncio
import base64
import collections
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import httpx
from openviking_sdk import OpenVikingError

from benchmark.aml import commit_guard as guard
from benchmark.aml.server import OpenVikingBackend, OpenVikingBackendError


class Client:
    def __init__(self, stage="phase1_persist"):
        self.uri = "viking://user/synthetic-user/sessions/synthetic-session"
        self.archive = self.uri + "/history/archive_001"
        self.owner = {"account_id": "default", "user_id": "synthetic-user"}
        self.raw = (
            json.dumps(
                {
                    "id": "original-message",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Synthetic [EMAIL_1] \u2028 payload"}],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self.live = self.raw if stage == "phase1_persist" else ""
        self.phase = {
            "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "queue_message": {
                "session_id": "synthetic-session",
                "session_uri": self.uri,
                "archive_uri": self.archive,
                "user": dict(self.owner),
                "task_id": "unregistered-intent",
            },
            "original_message_ids": ["original-message"],
            "archived_message_ids": ["original-message"],
            "retained_message_ids": [],
            "keep_recent_count": 0,
        }
        self.failure = {"stage": stage, "error": "lock acquire timed out after 0ms"}
        self.persisted = {
            "commit_count": 0 if stage == "phase1_persist" else 1,
            "message_count": 1 if stage == "phase1_persist" else 0,
        }
        self.task_exists = False
        self.commits = []
        self.writes = []
        self.downloads = []
        self.always_conflict = False
        self.transport_error = False
        self.other_conflict = False
        self.change_before_CAS = False
        self.change_meta_after_CAS = False

    async def commit_session(self, sid, **kwargs):
        self.commits.append((sid, kwargs))
        if self.transport_error:
            raise httpx.ReadTimeout("Synthetic ambiguous transport error")
        if self.other_conflict:
            raise OpenVikingError("A different conflict", code="CONFLICT")
        if len(self.commits) == 1 or self.always_conflict:
            raise OpenVikingError("lock acquire timed out after 0ms", code="CONFLICT")
        return {"status": "accepted", "task_id": "first-accepted-task"}

    async def get_session(self, sid, **kwargs):
        return {
            "uri": self.uri,
            "user": self.owner,
            "auto_commit_policy": None,
            "commit_count": self.persisted["commit_count"],
            "message_count": 1 if self.live else 0,
        }

    async def ls(self, uri, **kwargs):
        return [self.archive]

    async def get_task(self, task):
        return {"status": "completed"} if self.task_exists else None

    async def _request(self, method, path, **kwargs):
        if method == "GET":
            assert path == "/api/v1/content/download", (
                "Never restore from privacy-expanded read API"
            )
            uri = kwargs["params"]["uri"]
            self.downloads.append(uri)
            values = {
                self.uri + "/messages.jsonl": self.live,
                self.archive + "/messages.jsonl": self.raw,
                self.uri + "/.meta.json": json.dumps(self.persisted),
                self.archive + "/.meta.json": json.dumps({"phase1": self.phase}),
                self.archive + "/.failed.json": json.dumps(self.failure),
            }
            return httpx.Response(200, content=values[uri].encode())
        assert method == "POST" and path == "/api/v1/content/batch-write"
        body = kwargs["json"]
        self.writes.append(body)
        assert body["defer_processing"] is True and body["root_uri"] == self.uri
        (operation,) = body["operations"]
        assert operation["uri"] == self.uri + "/messages.jsonl"
        if self.change_before_CAS:
            self.live = "newer message"
        if operation["expected_sha256"] != guard.digest(self.live):
            return httpx.Response(409, json={"error": {"code": "CONFLICT"}})
        self.live = base64.b64decode(operation["content_base64"], validate=True).decode()
        if self.change_meta_after_CAS:
            self.persisted["unexpected"] = True
        return httpx.Response(200, json={"result": {"updated": [operation["uri"]]}})

    def _handle_response(self, response):
        if response.status_code >= 400:
            raise OpenVikingError("Content revision changed", code="CONFLICT")
        return response.json()["result"]


class ProofTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, client, on_wait=None):
        self.audit = []

        async def wait(_):
            if on_wait:
                on_wait()

        with mock.patch.object(guard.asyncio, "sleep", side_effect=wait):
            return await guard.commit_session_with_proof(
                client,
                "synthetic-session",
                lambda event, **fields: self.audit.append((event, fields)),
            )

    async def test_live_snapshot_only_retries_Commit(self):
        c = Client()
        original = c.raw
        result = await self.call(c)
        self.assertEqual(result["task_id"], "first-accepted-task")
        self.assertEqual(len(c.commits), 2)
        self.assertEqual(c.writes, [])
        self.assertEqual(c.live, original)
        self.assertTrue(all(kwargs == {"keep_recent_count": 0} for _, kwargs in c.commits))

    async def test_empty_live_CAS_restores_exact_original_UTF8_and_ID(self):
        c = Client("publication_lease")
        meta = dict(c.persisted)
        await self.call(c)
        self.assertEqual(c.live.encode(), c.raw.encode())
        self.assertEqual(c.persisted, meta)
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(len(c.commits), 2)
        self.assertEqual(json.loads(c.live)["id"], "original-message")
        self.assertNotIn("_archive_raw", json.dumps(self.audit))
        self.assertNotIn("[EMAIL_1]", json.dumps(self.audit))

    async def reject(self, client):
        with self.assertRaises(OpenVikingError):
            await self.call(client)
        self.assertEqual(len(client.commits), 1)

    async def test_registered_task_never_retried_or_restored(self):
        c = Client("publication_lease")
        c.task_exists = True
        await self.reject(c)
        self.assertEqual(c.live, "")
        self.assertEqual(c.writes, [])

    async def test_ready_archive_never_retried(self):
        c = Client()
        c.phase["status"] = "ready"
        await self.reject(c)

    async def test_changed_live_never_overwritten(self):
        c = Client("publication_lease")
        c.live = "newer"
        await self.reject(c)
        self.assertEqual(c.live, "newer")
        self.assertEqual(c.writes, [])

    async def test_failed_early_partial_clear_remains_unproven(self):
        c = Client()
        c.live = ""
        await self.reject(c)
        self.assertEqual(c.writes, [])

    async def test_unknown_transport_result_has_one_Commit(self):
        c = Client()
        c.transport_error = True
        with self.assertRaises(httpx.ReadTimeout):
            await self.call(c)
        self.assertEqual(len(c.commits), 1)
        self.assertEqual(c.downloads, [])

    async def test_unrelated_conflict_not_retried(self):
        c = Client()
        c.other_conflict = True
        await self.reject(c)
        self.assertEqual(c.downloads, [])

    async def test_wrong_owner_rejected(self):
        c = Client()
        c.phase["queue_message"]["user"]["user_id"] = "other"
        await self.reject(c)

    async def test_retention_rejected(self):
        c = Client()
        c.phase["retained_message_ids"] = ["original-message"]
        await self.reject(c)

    async def test_stale_failed_archive_rejected(self):
        c = Client()
        c.phase["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        await self.reject(c)

    async def test_message_identity_mismatch_rejected(self):
        c = Client()
        c.phase["archived_message_ids"] = ["different"]
        await self.reject(c)

    async def test_change_between_proofs_rejected(self):
        c = Client()
        with self.assertRaises(OpenVikingError):
            await self.call(c, lambda: c.persisted.update(changed=True))
        self.assertEqual(len(c.commits), 1)

    async def test_CAS_conflict_preserves_newer_live(self):
        c = Client("publication_lease")
        c.change_before_CAS = True
        await self.reject(c)
        self.assertEqual(c.live, "newer message")

    async def test_metadata_change_during_restore_blocks_next_Commit(self):
        c = Client("publication_lease")
        c.change_meta_after_CAS = True
        await self.reject(c)

    async def test_bounded_at_three_Commit_attempts(self):
        c = Client()
        c.always_conflict = True
        with self.assertRaises(OpenVikingError):
            await self.call(c)
        self.assertEqual(len(c.commits), 3)


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_serial_and_different_users_parallel(self):
        active = collections.Counter()
        peaks = collections.Counter()
        total_peak = 0

        class BackendClient:
            def __init__(self, user):
                self.user = user

            async def initialize(self):
                pass

            async def close(self):
                pass

            async def batch_add_messages(self, sid, messages):
                nonlocal total_peak
                active[self.user] += 1
                peaks[self.user] = max(peaks[self.user], active[self.user])
                total_peak = max(total_peak, sum(active.values()))
                await asyncio.sleep(0.01)
                return {"added": len(messages)}

            async def commit_session(self, sid, **kwargs):
                return {"status": "accepted", "task_id": sid}

            async def get_task(self, task):
                await asyncio.sleep(0.01)
                active[self.user] -= 1
                return {"status": "completed"}

        backend = OpenVikingBackend(
            SimpleNamespace(
                retry_attempts=7,
                retry_delay_seconds=0,
                extraction_timeout_seconds=10,
                task_poll_seconds=0.01,
            ),
            client_factory=BackendClient,
        )
        await asyncio.gather(
            *(
                backend.add_and_commit(
                    user_id=user,
                    session_id=str(i),
                    messages=[{"role": "user", "content": "synthetic"}],
                )
                for i, user in enumerate(["a", "a", "b"])
            )
        )
        self.assertEqual(peaks["a"], 1)
        self.assertEqual(peaks["b"], 1)
        self.assertEqual(total_peak, 2)

    async def test_write_once_does_not_use_generic_retry_budget(self):
        backend = OpenVikingBackend(SimpleNamespace(retry_attempts=7, retry_delay_seconds=0))
        calls = 0

        async def ambiguous():
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("Synthetic ambiguous write")

        with self.assertRaises(OpenVikingBackendError):
            await backend._write_once("batch add", ambiguous)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
