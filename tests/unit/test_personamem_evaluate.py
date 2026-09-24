import copy
import json
import os
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from benchmark.aml.eval import evaluate as evaluator


def retrieval_row(unit="personamem_v2_32k", question="QUESTION_CORRECT"):
    return {
        "id": question,
        "unit": unit,
        "persona_id": "7",
        "question": question,
        "user_query": {"role": "user", "content": question},
        "retrieved_context": ["The user enjoys hiking."],
        "chat_history": [{"role": "user", "content": "UNRETRIEVED_HISTORY"}],
        "preference": "LABEL_ONLY_PREFERENCE",
        "correct_answer": "Take a hiking trip.",
        "gold_answer": "Take a hiking trip.",
        "incorrect_answers": ["Go shopping.", "Watch television.", "Play a video game."],
        "answer_mode": "generative",
        "options": [],
    }


class PersonaMemEvaluateTests(unittest.TestCase):
    def test_mode_override_uses_only_retrieved_memory_and_preserves_input(self):
        row = retrieval_row()
        original = copy.deepcopy(row)
        adapted, mode = evaluator._adapt_rows([row], "personamem_v2", persona_mode="mcq")
        self.assertEqual(mode, "mcq")
        self.assertEqual(adapted[0]["answer_mode"], "mcq")
        self.assertEqual(
            adapted[0]["chat_history"],
            [{"role": "system", "content": "[Retrieved memory 1]\nThe user enjoys hiking."}],
        )
        self.assertEqual(row, original)
        _, default_mode = evaluator._adapt_rows([row], "personamem_v2")
        self.assertEqual(default_mode, "mcq")
        _, explicit_mode = evaluator._adapt_rows([row], "personamem_v2", persona_mode="generative")
        self.assertEqual(explicit_mode, "generative")
        generic, generic_mode = evaluator._adapt_rows([row], "generic")
        self.assertIsNone(generic_mode)
        self.assertEqual(generic, [row])
        for size in ("32k", "128k"):
            unit = f"personamem_v2_{size}"
            self.assertEqual(evaluator._resolve_unit([retrieval_row(unit)], None), unit)
        adapted, _ = evaluator._adapt_rows(
            [{**row, "retrieved_context": []}], "personamem_v2", persona_mode="mcq"
        )
        self.assertEqual(
            adapted[0]["chat_history"][0]["content"], "No relevant memories were retrieved."
        )

    def test_invalid_options_or_unrelated_mode_override_fail_before_models(self):
        for incorrect in (
            [],
            ["only one"],
            ["a", "a", "b"],
            ["a", "b", " "],
            ["a", "b", "Take a hiking trip."],
            ["a", "b", 123],
            "not JSON",
        ):
            with self.subTest(incorrect=incorrect), self.assertRaises(ValueError):
                evaluator._adapt_rows(
                    [{**retrieval_row(), "incorrect_answers": incorrect}],
                    "personamem_v2",
                    persona_mode="mcq",
                )
        with self.assertRaisesRegex(ValueError, "only supported for PersonaMem"):
            evaluator._adapt_rows([retrieval_row()], "generic", persona_mode="mcq")
        with self.assertRaisesRegex(ValueError, "unsupported PersonaMem mode"):
            evaluator._adapt_rows([retrieval_row()], "personamem_v2", persona_mode="invalid")

    def test_public_mcq_pipeline_with_local_http_and_no_judge_credentials(self):
        repo = (
            Path(__file__).resolve().parents[2] / "result/aml-public-eval/agent-memory-leaderboard"
        )
        if not (repo / "data/personamem/pipeline_v2.py").is_file():
            self.skipTest("pinned public AML checkout is required for the integration check")
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, payload))
                messages = payload["messages"]
                options = dict(re.findall(r"^([A-D])\. (.+)$", messages[-1]["content"], re.M))
                question = messages[1]["content"]
                if "QUESTION_INVALID" in question:
                    answer = "I cannot select an option."
                else:
                    target = (
                        "Take a hiking trip." if "QUESTION_CORRECT" in question else "Go shopping."
                    )
                    letter = next(key for key, value in options.items() if value == target)
                    answer = f"Final Answer: {letter}"
                body = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment = {
            "ANSWER_API_BASE": f"http://127.0.0.1:{server.server_port}/v1",
            "ANSWER_API_KEY": "local-test-only",
            "ANSWER_MODEL": "local-test-model",
            "JUDGE_API_BASE": "",
            "JUDGE_API_KEY": "",
            "JUDGE_MODEL": "",
            "PYTHONHASHSEED": "0",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, environment):
                root = Path(directory)
                previous_mapping = None
                for size, concurrency in (("32k", 1), ("32k", 2), ("128k", 2)):
                    unit = f"personamem_v2_{size}"
                    rows = [
                        retrieval_row(unit, question)
                        for question in ("QUESTION_CORRECT", "QUESTION_WRONG", "QUESTION_INVALID")
                    ]
                    retrieval = root / f"{size}-{concurrency}.jsonl"
                    evaluator._write_jsonl(retrieval, rows)
                    original = retrieval.read_bytes()
                    summary = evaluator.evaluate(
                        retrieval,
                        root / f"output-{size}-{concurrency}",
                        aml_repo=repo,
                        answer_concurrency=concurrency,
                        eval_concurrency=concurrency,
                    )
                    self.assertEqual(retrieval.read_bytes(), original)
                    self.assertEqual(summary["persona_mode"], "mcq")
                    self.assertEqual(summary["correct_count"], 1)
                    self.assertEqual(summary["incorrect_count"], 2)
                    self.assertEqual(summary["invalid_answer_count"], 1)
                    self.assertEqual(summary["accuracy"], 1 / 3)
                    answers = evaluator._read_jsonl(Path(summary["answers"]))
                    self.assertEqual([row["id"] for row in answers], [row["id"] for row in rows])
                    mappings = [row["option_mapping"] for row in answers]
                    if previous_mapping is not None:
                        self.assertEqual(mappings, previous_mapping)
                    previous_mapping = mappings
                    judgments = evaluator._read_jsonl(Path(summary["judgments"]))
                    self.assertEqual([r["is_correct"] for r in judgments], [True, False, False])
                    self.assertTrue(
                        all(r["gold_answer"] == "Take a hiking trip." for r in judgments)
                    )
            self.assertEqual(len(requests), 9)  # Only Answer calls; scoring is local.
            for path, payload in requests:
                self.assertEqual(path, "/v1/chat/completions")
                self.assertEqual(payload["model"], "local-test-model")
                prompt = json.dumps(payload["messages"])
                self.assertIn("The user enjoys hiking.", prompt)
                self.assertNotIn("UNRETRIEVED_HISTORY", prompt)
                self.assertNotIn("LABEL_ONLY_PREFERENCE", prompt)
                self.assertNotIn("correct_answer", prompt)
                self.assertNotIn("correct_letter", prompt)
                self.assertEqual(prompt.count("Take a hiking trip."), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
