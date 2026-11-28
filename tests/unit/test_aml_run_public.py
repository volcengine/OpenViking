import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

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
                    "question": f"What happened to history {history}?",
                }
            )
        cases.append(
            {
                **cases[0],
                "case_id": "same-history-second-question",
                "question": "What else happened?",
            }
        )
        observed: dict[str, list[dict[str, Any]]] = {}
        by_query = {}
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
                observed.setdefault(user, []).append(payload)
                active.remove(user)
                return httpx.Response(200, json={"success": True})
            self.assertEqual(request.url.path, "/search")
            self.assertEqual(sum(len(rows) for rows in observed.values()), 8)
            by_query[payload["query"]] = payload["user_id"]
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
            self.assertEqual(by_query[cases[0]["question"]], by_query[cases[2]["question"]])
            self.assertEqual(len(set(by_query.values())), 2)
            for history, case in enumerate(cases[:2]):
                requests = observed[by_query[case["question"]]]
                self.assertEqual([len(request["messages"]) for request in requests], [20, 1, 20, 1])
                first, second, third, fourth = [request["session_id"] for request in requests]
                self.assertEqual(first, second)
                self.assertEqual(third, fourth)
                self.assertNotEqual(first, third)
                self.assertEqual(
                    [message["content"] for request in requests for message in request["messages"]],
                    [f"{history}/{session}/{turn}" for session in range(2) for turn in range(21)],
                )
                self.assertEqual(len({request["request_id"] for request in requests}), 4)
            self.assertEqual(summary["units"][0]["add_requests"], 8)
            rows = [
                json.loads(line)
                for line in Path(summary["units"][0]["retrieval"]).read_text().splitlines()
            ]
            self.assertEqual([row["id"] for row in rows], [case["case_id"] for case in cases])
            self.assertEqual([row["retrieved_context"] for row in rows], [["event"]] * 3)
            self.assertEqual(len(started), 2)

    async def test_failed_batch_drains_other_workers_before_returning(self):
        slow_started = asyncio.Event()
        slow_cancelled = asyncio.Event()

        async def handle(request):
            self.assertEqual(request.url.path, "/add")
            [message] = json.loads(request.content)["messages"]
            if message["content"] == "fail":
                await slow_started.wait()
                return httpx.Response(503, text="service unavailable")
            slow_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                slow_cancelled.set()

        cases = [
            {
                "unit": "beam_100k",
                "case_id": name,
                "history_key": name,
                "question": "What happened?",
                "histories": [
                    {"session_id": name, "messages": [{"role": "user", "content": name}]}
                ],
            }
            for name in ("fail", "slow")
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = root / "prepared.json"
            prepared.write_text(json.dumps({"cases": cases}))
            output = root / "output"
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                with self.assertRaisesRegex(RuntimeError, "/add returned HTTP 503"):
                    await asyncio.wait_for(
                        runner.run_public(
                            [prepared],
                            output_dir=output,
                            namespace="test-run",
                            base_url="http://aml.test",
                            api_key="test-only",
                            add_concurrency=2,
                            client=client,
                        ),
                        timeout=5,
                    )

            self.assertTrue(slow_cancelled.is_set())
            self.assertEqual(list(output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
