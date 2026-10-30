"""Synthetic admission tests: every test ledger is a disposable temporary DB.

Provider statuses and usage below are fixtures, never campaign or release wire records.
"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmark.aml import model_admission as quota

MODULE = Path(quota.__file__)


class GateContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "synthetic.sqlite"
        self.budgets = copy.deepcopy(quota.DEFAULTS)
        self.gate = quota.Gate(self.path, self.budgets)

    def test_four_concurrent_cross_instance_and_process(self):
        other = quota.Gate(self.path, self.budgets)
        tickets = [self.gate.try_acquire("rerank", 10, "synthetic")[0] for _ in range(4)]
        self.assertTrue(all(tickets))
        self.assertIsNone(other.try_acquire("rerank", 10)[0])
        script = """import importlib.util,json,sys
s=importlib.util.spec_from_file_location('q',sys.argv[1]);q=importlib.util.module_from_spec(s);s.loader.exec_module(q)
g=q.Gate(sys.argv[2],q.DEFAULTS);t,d=g.try_acquire('rerank',10,'synthetic-child')
print(json.dumps({'acquired':bool(t)}))
if t:g.release(t)
"""

        def child():
            return json.loads(
                subprocess.check_output(
                    [sys.executable, "-c", script, str(MODULE), str(self.path)], text=True
                )
            )

        self.assertFalse(child()["acquired"])
        self.gate.release(tickets.pop())
        self.assertTrue(child()["acquired"])
        for ticket in tickets:
            self.gate.release(ticket)

    def test_120_requests_per_window(self):
        for _ in range(120):
            ticket, _ = self.gate.try_acquire("rerank", 1, "synthetic-rpm")
            self.assertIsNotNone(ticket)
            self.gate.release(ticket)
        self.assertIsNone(self.gate.try_acquire("rerank", 1)[0])

    def test_estimated_500k_tpm_and_actual_usage(self):
        ticket, _ = self.gate.try_acquire("rerank", 400000)
        self.gate.release(ticket)
        self.assertIsNone(self.gate.try_acquire("rerank", 100001)[0])
        ticket, _ = self.gate.try_acquire("rerank", 100000)
        self.assertIsNotNone(ticket)
        self.gate.release(ticket, actual=100)
        ticket, _ = self.gate.try_acquire("rerank", 99900)
        self.assertIsNotNone(ticket)
        self.gate.release(ticket)

    def test_shared_cooldown_not_released_before_65_seconds(self):
        with mock.patch.object(quota.time, "time", return_value=1000):
            ticket, _ = self.gate.try_acquire("rerank", 1)
            self.gate.release(ticket, cooldown=65)
        other = quota.Gate(self.path, self.budgets)
        with mock.patch.object(quota.time, "time", return_value=1064.99):
            self.assertIsNone(other.try_acquire("rerank", 1)[0])
        with mock.patch.object(quota.time, "time", return_value=1065.01):
            ticket, _ = other.try_acquire("rerank", 1)
            self.assertIsNotNone(ticket)
            other.release(ticket)

    def test_tokenizer_reservation_is_separate_from_reported_usage(self):
        from types import SimpleNamespace

        encoder = SimpleNamespace(encode=lambda text, **kwargs: list(text))
        tokenizer = SimpleNamespace(get_encoding=lambda name: encoder)
        body = {"model_name": "doubao-seed-rerank", "query": [{"text": "question"}]}
        with (
            mock.patch.dict(sys.modules, {"tiktoken": tokenizer}),
            mock.patch.object(quota, "_TOKENIZER", None),
        ):
            estimate = quota.estimate(body, "rerank")
            self.assertGreater(estimate, 64)
            self.assertEqual(quota.estimate({**body, "max_tokens": 16384}, "rerank"), estimate)
        ticket, _ = self.gate.try_acquire("rerank", estimate)
        self.gate.release(ticket, status=200)
        with self.gate.db() as db:
            reserved, actual = db.execute("select reserved,actual from attempts").fetchone()
        self.assertEqual(reserved, estimate)
        self.assertIsNone(actual)


class HTTPContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx

        self.httpx = httpx
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-http-admission-")
        self.addCleanup(self.temporary.cleanup)
        self.gate = quota.Gate(
            Path(self.temporary.name) / "synthetic.sqlite", copy.deepcopy(quota.DEFAULTS)
        )
        self.original_async, self.original_sync = httpx.AsyncClient.send, httpx.Client.send
        estimator = mock.patch.object(quota, "estimate", return_value=100)
        estimator.start()
        self.addCleanup(estimator.stop)
        self.addCleanup(setattr, httpx.AsyncClient, "send", self.original_async)
        self.addCleanup(setattr, httpx.Client, "send", self.original_sync)
        with mock.patch.object(quota, "Gate", return_value=self.gate):
            installed = quota.install(
                "source-fixture", "embedding-fixture", native=False, phase="synthetic-HTTP"
            )
        self.assertIs(installed, self.gate)

    async def request(self, status, response, payload=None):
        payload = payload or {
            "model_name": "doubao-seed-rerank",
            "model_version": "251028",
            "query": [{"text": "original query"}],
            "data": [[{"text": "original abstract"}]],
        }
        raw = json.dumps(payload).encode()
        observed = []

        def transport(request):
            observed.append((request.content, request.url.path))
            return self.httpx.Response(status, json=response)

        async with self.httpx.AsyncClient(transport=self.httpx.MockTransport(transport)) as client:
            result = await client.post("https://provider.invalid/api/vikingdb/rerank", content=raw)
        self.assertEqual(observed, [(raw, "/api/vikingdb/rerank")])
        self.assertEqual(result.status_code, status)
        return result

    async def test_rerank_model_name_classified_and_body_preserved(self):
        await self.request(200, {"usage": {"total_tokens": 1234}, "result": {"data": []}})
        with self.gate.db() as db:
            row = db.execute("select kind,http_status,actual,phase from attempts").fetchone()
            self.assertEqual(row, ("rerank", 200, 1234, "synthetic-HTTP"))
            self.assertEqual(db.execute("select count(*) from leases").fetchone()[0], 0)

    async def test_real_429_signature_sets_shared_65s_cooldown(self):
        before = quota.time.time()
        await self.request(429, {"error": {"message": "synthetic throttle fixture"}})
        with self.gate.db() as db:
            until = db.execute('select until from cooldown where kind="rerank"').fetchone()[0]
        self.assertGreaterEqual(until, before + 65)
        self.assertIsNone(self.gate.try_acquire("rerank", 1)[0])

    async def test_error_minus_2004_signature_sets_cooldown(self):
        before = quota.time.time()
        await self.request(200, {"code": -2004})
        with self.gate.db() as db:
            until = db.execute('select until from cooldown where kind="rerank"').fetchone()[0]
        self.assertGreaterEqual(until, before + 65)

    async def test_timestamp_containing_429_is_not_throttle(self):
        await self.request(200, {"created_at": "2026-10-27T14:29:00Z"})
        with self.gate.db() as db:
            self.assertEqual(db.execute("select count(*) from cooldown").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


def test_configured_shared_budget_file_is_applied_and_reloaded(tmp_path):
    budgets = copy.deepcopy(quota.DEFAULTS)
    budgets["rerank"]["concurrent"] = 1
    config = tmp_path / "budgets.json"
    config.write_text(json.dumps({"budgets": budgets}))
    first = quota.Gate(tmp_path / "shared.sqlite", budget_file=config)
    second = quota.Gate(tmp_path / "shared.sqlite", budget_file=config)
    ticket, _ = first.try_acquire("rerank", 1)
    assert ticket and second.try_acquire("rerank", 1)[0] is None
    budgets["rerank"]["concurrent"] = 2
    config.write_text(json.dumps({"budgets": budgets}))
    other, _ = second.try_acquire("rerank", 1)
    assert other and first.try_acquire("rerank", 1)[0] is None
    first.release(ticket)
    second.release(other)


def test_missing_or_invalid_explicit_budget_never_falls_back_to_defaults(tmp_path):
    config = tmp_path / "budgets.json"
    database = tmp_path / "shared.sqlite"
    with unittest.TestCase().assertRaises(FileNotFoundError):
        quota.Gate(database, budget_file=config)
    for value in [0, -1, True, 0.5]:
        budgets = copy.deepcopy(quota.DEFAULTS)
        budgets["rerank"]["concurrent"] = value
        config.write_text(json.dumps({"budgets": budgets}))
        with unittest.TestCase().assertRaises(ValueError):
            quota.Gate(database, budget_file=config)
    assert not database.exists()
