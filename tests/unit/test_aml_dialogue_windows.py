import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark.aml.eval import dialogue_windows as dw


def message(ident, turn, *, session=1, text=None, role="user"):
    return {
        "id": ident,
        "session_number": session,
        "turn": turn,
        "text": text or f"[D{session}:{turn}] Pat: Message {ident}",
        "role": role,
        "created_at": "2023-10-13T10:31:00+00:00",
    }


def vector(dense=(1.0, 0.0), sparse=()):
    return {
        "embedding": list(dense),
        "sparse_embedding": [{"index": k, "value": v} for k, v in sparse],
    }


def make_index(sources, embeddings=None):
    windows = dw.build_windows(sources)
    embeddings = embeddings or {w["id"]: vector() for w in windows}
    return dw.DialogueIndex(windows, embeddings, dimension=2)


class DialogueWindowTests(unittest.TestCase):
    def test_embedding_key_and_returned_value_have_different_bounds(self):
        sources = {"a": [message(str(i), i) for i in range(1, 6)]}
        original = copy.deepcopy(sources)
        windows = dw.build_windows(sources)
        focal = windows[3]
        self.assertEqual(focal["key_message_ids"], ["3", "4", "5"])
        self.assertEqual([m["id"] for m in focal["messages"]], ["2", "3", "4", "5"])
        self.assertEqual(windows[0]["key_message_ids"], ["1", "2"])
        self.assertEqual(windows[-1]["key_message_ids"], ["4", "5"])
        self.assertEqual(sources, original)

    def test_boundaries_and_turn_order_are_scoped_to_user_and_session(self):
        windows = dw.build_windows(
            {
                "a": [message("two", 2), message("one", 1), message("s2", 1, session=2)],
                "b": [message("one", 1)],
            }
        )
        self.assertEqual(windows[0]["key_message_ids"], ["one", "two"])
        self.assertEqual(windows[2]["key_message_ids"], ["s2"])
        self.assertEqual(windows[3]["key_message_ids"], ["one"])
        self.assertNotEqual(windows[0]["id"], windows[3]["id"])

    def test_name_role_timestamp_and_image_metadata_are_preserved(self):
        text = "[D1:1] Lee: Last year.\n[Image caption] a lake\n[Image source] https://example.test"
        windows = dw.build_windows({"a": [message("m", 1, text=text, role="assistant")]})
        self.assertIn("[2023-10-13][Lee]", windows[0]["embedding_text"])
        self.assertIn("[Image caption]", windows[0]["embedding_text"])
        self.assertNotIn("https://", windows[0]["embedding_text"])
        picked, _ = dw.select_windows(windows, [], len, max_extra_tokens=1000)
        self.assertIn(
            "[2023-10-13T10:31:00+00:00][Lee][protocol role assistant]", picked[0]["content"]
        )
        self.assertIn(text, picked[0]["content"])

    def test_duplicate_messages_and_turns_are_rejected(self):
        for messages in (
            [message("a", 1), message("a", 2)],
            [message("a", 1), message("b", 1)],
        ):
            with self.subTest(messages=messages), self.assertRaises(ValueError):
                dw.build_windows({"user": messages})

    def test_rrf_combines_channels_and_never_searches_another_user(self):
        sources = {"a": [message("1", 1), message("2", 2)], "b": [message("x", 1)]}
        windows = dw.build_windows(sources)
        embeddings = {
            windows[0]["id"]: vector((1, 0)),
            windows[1]["id"]: vector((0, 1), [(7, 2)]),
            windows[2]["id"]: vector((1, 0), [(7, 999)]),
        }
        index = dw.DialogueIndex(windows, embeddings, dimension=2)
        ranked = index.rank("a", vector((1, 0), [(7, 3)]))
        self.assertEqual([w["focal_id"] for w in ranked], ["2", "1"])
        self.assertAlmostEqual(ranked[0]["rrf"], 1 / 62 + 1 / 61)
        self.assertEqual(ranked[0]["sparse_score"], 6)
        self.assertTrue(all(w["user_id"] == "a" for w in ranked))
        self.assertEqual(index.rank("missing", vector()), [])

    def test_ties_are_deterministic_and_dense_only_input_is_supported(self):
        index = make_index({"a": [message("z", 1), message("a", 2)]})
        ranked = index.rank("a", vector(), candidate_limit=1)
        self.assertEqual([w["focal_id"] for w in ranked], ["a"])

    def test_invalid_vectors_are_rejected(self):
        for data in (vector((0, 0)), vector((float("nan"), 1)), vector((1,))):
            with self.subTest(data=data), self.assertRaises(ValueError):
                dw._dense(data, 2)
        with self.assertRaises(ValueError):
            dw._sparse(vector(sparse=[(1, 1), (1, 2)]))

    def test_overlapping_source_messages_are_returned_once(self):
        index = make_index({"a": [message(str(i), i) for i in range(1, 7)]})
        picked, _ = dw.select_windows(index.rank("a", vector()), [], lambda _: 1)
        ids = [ident for w in picked for ident in w["source_message_ids"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {str(i) for i in range(1, 7)})

    def test_existing_literal_evidence_is_not_appended_again(self):
        raw = message("m", 1)
        index = make_index({"a": [raw]})
        self.assertEqual(dw.select_windows(index.rank("a", vector()), [raw["text"]], len), ([], 0))

    def test_budget_skips_large_window_without_consuming_its_message_ids(self):
        windows = dw.build_windows({"a": [message("1", 1), message("2", 2)]})
        small = {**windows[0], "messages": [windows[0]["messages"][0]]}
        picked, cost = dw.select_windows(
            [windows[0], small],
            [],
            lambda text: 20 if "Message 2" in text else 3,
            max_extra_tokens=5,
        )
        self.assertEqual(cost, 5)
        self.assertEqual(picked[0]["source_message_ids"], ["1"])
        self.assertEqual(dw.select_windows(windows, [], len, max_windows=0), ([], 0))

    def test_parent_contexts_and_metadata_are_preserved_with_fewer_than_twenty(self):
        index = make_index({"a": [message("m", 1)]})
        row = {
            "id": "q1",
            "user_id": "a",
            "question": "What happened?",
            "retrieved_context": ["original memory"],
            "memory_hits": [{"id": "viking://user/a/memories/events/one.md", "user_id": "a"}],
            "gold_answer": "gold must only be carried through",
            "unit": "locomo_refined",
        }
        original = copy.deepcopy(row)
        output = dw.augment_row(row, index, vector(), lambda _: 1)
        self.assertEqual(output["retrieved_context"][0], "original memory")
        self.assertEqual(output["memory_hits"][0], row["memory_hits"][0])
        self.assertEqual(output["gold_answer"], row["gold_answer"])
        self.assertEqual(len(output["retrieved_context"]), 2)
        self.assertEqual(row, original)
        self.assertNotIn(row["gold_answer"], output["retrieved_context"][1])
        with self.assertRaisesRegex(ValueError, "already augmented"):
            dw.augment_row(output, index, vector(), len)
        for hit in (
            {"id": "memory", "user_id": "b"},
            {"id": "viking://user/b/memories/events/one.md", "user_id": "a"},
        ):
            with self.subTest(hit=hit), self.assertRaises(ValueError):
                dw.augment_row({**row, "memory_hits": [hit]}, index, vector(), len)

    def test_cache_is_exact_text_model_bound_and_missing_cache_never_calls_api(self):
        request = {
            "model": "embedding-test",
            "input": [{"type": "text", "text": "query only"}],
            "sparse_embedding": {"type": "enabled"},
        }
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / digest[:2] / f"{digest}.json"
            path.parent.mkdir()
            record = {"request": request, "http": 200, "response": {"data": vector()}}
            path.write_text(json.dumps(record))
            self.assertEqual(
                dw.read_cached_embedding(root, "embedding-test", "query only"), vector()
            )
            with self.assertRaises(FileNotFoundError):
                dw.read_cached_embedding(root, "embedding-test", "query plus history")
            record["request"]["model"] = "wrong-model"
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                dw.read_cached_embedding(root, "embedding-test", "query only")

    def test_cli_preserves_literal_unicode_line_separator_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sources.json"
            source.write_text(json.dumps({"a": [message("m", 1)]}))
            retrieval = root / "retrieval.jsonl"
            row = {
                "id": "q",
                "user_id": "a",
                "question": "one\u2028question",
                "retrieved_context": [],
                "memory_hits": [],
            }
            retrieval.write_text(json.dumps(row, ensure_ascii=False) + "\n")
            output = root / "augmented.jsonl"
            args = [
                "--sources",
                str(source),
                "--retrieval",
                str(retrieval),
                "--embedding-cache",
                temporary,
                "--query-embedding-cache",
                temporary,
                "--embedding-model",
                "test",
                "--dimension",
                "2",
                "--output",
                str(output),
            ]
            with patch.object(dw, "read_cached_embedding", return_value=vector()) as cached:
                with patch("tiktoken.get_encoding") as encoding:
                    encoding.return_value.encode.return_value = [1]
                    self.assertEqual(dw.main(args), 0)
                self.assertEqual(cached.call_args_list[-1].args[-1], "one\u2028question")
            self.assertEqual(json.loads(output.read_text())["question"], row["question"])
            with self.assertRaises(SystemExit):
                dw.main(args)


if __name__ == "__main__":
    unittest.main()
