import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from benchmark.aml.eval import run_public as runner


class PublicRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_histories_overlap_but_each_users_sessions_and_chunks_stay_ordered(self):
        cases = []
        for history in range(2):
            sessions = [
                {
                    "session_id": f"session-{session}",
                    "messages": [
                        {"role": "user", "content": f"{history}/{session}/{turn}"}
                        for turn in range(21)
                    ],
                }
                for session in range(2)
            ]
            cases.append(
                {
                    "unit": "beam_100k",
                    "case_id": f"case-{history}",
                    "history_key": f"history-{history}",
                    "histories": sessions,
                    "question": "What happened?",
                }
            )
        cases.append({**cases[0], "case_id": "same-history-second-question"})
        groups, searches = runner._prepare_unit("beam_100k", cases, "test-run")
        expected = {}
        for group in groups:
            expected.setdefault(group[0].user_id, []).extend(op.payload() for op in group)
        observed = {user: [] for user in expected}
        active = set()
        started = set()
        both_started = asyncio.Event()

        async def handle(request):
            payload = json.loads(request.content)
            self.assertEqual(request.headers["Authorization"], "Token test-only")
            if request.url.path == "/add":
                user = payload["user_id"]
                self.assertNotIn(user, active)
                active.add(user)
                started.add(user)
                if len(started) == 2:
                    both_started.set()
                await both_started.wait()
                await asyncio.sleep(0)
                observed[user].append(payload)
                active.remove(user)
                return httpx.Response(200, json={"success": True})
            self.assertEqual(request.url.path, "/search")
            self.assertEqual(observed, expected)
            return httpx.Response(200, json={"data": [{"id": "memory", "content": "event"}]})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared.json"
            prepared.write_text(json.dumps({"cases": cases}))
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                summary = await asyncio.wait_for(
                    runner.run_public(
                        [prepared],
                        output_dir=root / "output",
                        namespace="test-run",
                        base_url="http://test-provider",
                        api_key="test-only",
                        add_concurrency=2,
                        search_concurrency=2,
                        client=client,
                    ),
                    timeout=5,
                )
            self.assertEqual(observed, expected)
            self.assertEqual(summary["units"][0]["add_requests"], 8)
            rows = [
                json.loads(line)
                for line in Path(summary["units"][0]["retrieval"]).read_text().splitlines()
            ]
            self.assertEqual([row["id"] for row in rows], [case.output["id"] for case in searches])
            self.assertEqual(len(started), 2)

    async def test_failed_batch_drains_other_workers_before_returning(self):
        slow_started = asyncio.Event()
        slow_cancelled = asyncio.Event()

        async def work(value):
            if value == "fail":
                await slow_started.wait()
                raise RuntimeError("Add failed")
            slow_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                slow_cancelled.set()

        with self.assertRaisesRegex(RuntimeError, "Add failed"):
            await asyncio.wait_for(runner._bounded_map(["fail", "slow"], 2, work), timeout=5)
        self.assertTrue(slow_cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
