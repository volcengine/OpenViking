import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmark.aml.eval.prepare_personamem import build_prepared, chat_messages, refresh_smoke
from benchmark.aml.eval.run_public import _prepare_unit


class PersonaMemPrepareTests(unittest.TestCase):
    def test_system_is_dropped_and_chat_text_order_is_exact(self):
        messages, removed, blank = chat_messages(
            [
                {"role": "system", "content": "EXPLICIT PERSONA"},
                {"role": "user", "content": "  Original text\n"},
                {"role": "system", "content": "ANOTHER PROFILE"},
                {"role": "assistant", "content": "reply"},
                {"role": "assistant", "content": " \n"},
            ]
        )
        self.assertEqual(removed, [0, 2])
        self.assertEqual(blank, [4])
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "  Original text\n"},
                {"role": "assistant", "content": "reply"},
            ],
        )

    def test_multimodal_or_unknown_messages_fail_in_text_mode(self):
        for message in [
            {"role": "user", "content": [{"type": "image"}]},
            {"role": "tool", "content": "text"},
        ]:
            with self.assertRaises(ValueError):
                chat_messages([message])

    def test_csv_links_labels_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {}

            def write(relative, content):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                expected[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

            write("benchmark/text/benchmark.csv", "test fixture")
            row = {
                "persona_id": "7",
                "raw_persona_file": "data/raw_data/persona7.json",
                "user_query": repr({"role": "user", "content": "question"}),
                "correct_answer": "GOLD ONLY",
                "incorrect_answers": repr(["WRONG ONLY"]),
                "preference": "ANNOTATION ONLY",
            }
            for size in ["32k", "128k"]:
                relative = f"data/chat_history_{size}/exact7.json"
                row[f"chat_history_{size}_link"] = relative
                write(
                    relative,
                    json.dumps(
                        {
                            "metadata": {
                                "persona_id": 7,
                                "input_filename": row["raw_persona_file"],
                                "final_token_count": 100,
                            },
                            "chat_history": [
                                {"role": "system", "content": "EXPLICIT PERSONA"},
                                {"role": "user", "content": size + " original chat"},
                            ],
                        }
                    ),
                )
                # Filename guessing or reading raw_data must not replace this link.
                write(f"data/chat_history_{size}/newer7.json", "invalid alternative")
            second = {**row, "user_query": repr({"role": "user", "content": "question two"})}
            units = [
                build_prepared(root, [row, second], size, "revision", expected)
                for size in ["32k", "128k"]
            ]
            users = []
            for payload in units:
                self.assertEqual(len(payload["histories"]), 1)
                session = payload["histories"][0]["sessions"][0]
                self.assertNotIn("GOLD ONLY", json.dumps(session))
                self.assertNotIn("ANNOTATION ONLY", json.dumps(session))
                cases = [{**case, "histories": [session]} for case in payload["cases"]]
                groups, searches = _prepare_unit(payload["unit"], cases, "same-run")
                self.assertEqual(len(groups), 1)
                self.assertEqual(searches[0].user_id, searches[1].user_id)
                users.append(searches[0].user_id)
                self.assertEqual(payload["cases"][0]["gold_answer"], "GOLD ONLY")
                self.assertEqual(payload["cases"][0]["extra"]["answer_mode"], "mcq")
                self.assertEqual(payload["cases"][0]["options"], [])
            self.assertNotEqual(*users)
            unchanged = {"unit": "clbench_0_4k", "case_id": "unrelated", "histories": []}
            smoke = {
                "cases": [
                    unchanged,
                    {"unit": units[0]["unit"], "case_id": units[0]["cases"][0]["case_id"]},
                ],
                "datasets": [{"unit": units[0]["unit"]}, {"unit": "clbench_0_4k"}],
            }
            refreshed = refresh_smoke(smoke, units[0])
            self.assertEqual(refreshed["cases"][0], unchanged)
            self.assertNotIn("system", json.dumps(refreshed["cases"][1]["histories"]))
            # A modified or wrong-sized linked file must fail before preparation.
            (root / row["chat_history_128k_link"]).write_text("tampered")
            with self.assertRaises(ValueError):
                build_prepared(root, [row], "128k", "revision", expected)


if __name__ == "__main__":
    unittest.main()
