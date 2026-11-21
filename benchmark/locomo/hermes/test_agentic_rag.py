import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from build_rag_index import raw_chunks
from eval import rag_search_trace
from locomo_rag import INDEX_VERSION, TOOL_NAME, register


class TestAgenticRag(unittest.TestCase):
    def test_raw_chunks_preserve_session_boundaries(self):
        messages = [{"speaker": "A", "text": f"turn {i}"} for i in range(1, 6)]
        chunks = raw_chunks(
            {
                "session_1": messages,
                "session_1_date_time": "1:00 pm on 1 January, 2024",
                "session_2": [{"speaker": "B", "text": "next", "blip_caption": "a bicycle"}],
            }
        )
        self.assertEqual(
            [chunk["id"] for chunk in chunks], ["session_1:1-4", "session_1:4-5", "session_2:1-1"]
        )
        self.assertTrue(all("2024-01-01 13:00" in chunk["text"] for chunk in chunks[:2]))
        self.assertEqual(sum("turn 4" in chunk["text"] for chunk in chunks), 2)
        self.assertNotIn("turn 5", chunks[0]["text"])
        self.assertIn("caption: a bicycle", chunks[2]["text"])

    def test_character_chunks_include_metadata_in_limit(self):
        chunks = raw_chunks(
            {
                "session_1": [{"speaker": "A", "text": "历史" * 280}],
                "session_1_date_time": "1:00 pm on 1 January, 2024",
                "session_2": [{"speaker": "B", "text": "另一段对话"}],
            },
            chunk_chars=512,
        )
        first, second, other = chunks
        self.assertEqual(len(first["text"]), 512)
        self.assertTrue(all(len(chunk["text"]) <= 512 for chunk in chunks))
        self.assertTrue(all("2024-01-01 13:00" in chunk["text"] for chunk in chunks[:2]))
        first_end = int(first["id"].rsplit("-", 1)[1])
        next_start = int(second["id"].rsplit(":", 1)[1].split("-", 1)[0])
        self.assertEqual(first_end - next_start + 1, 64)
        self.assertTrue(other["id"].startswith("session_2:"))
        self.assertNotIn("另一段对话", first["text"])
        self.assertIn("另一段对话", other["text"])

    def test_search_is_scoped_and_trace_preserves_follow_up(self):
        index = {
            "version": INDEX_VERSION,
            "embedding_model": "test",
            "samples": {
                "a": {
                    "scope": "scope-a",
                    "chunks": [
                        {"id": "a1", "text": "first", "vector": [1.0, 0.0]},
                        {"id": "a2", "text": "second", "vector": [0.0, 1.0]},
                    ],
                },
                "b": {
                    "scope": "scope-b",
                    "chunks": [{"id": "b1", "text": "other", "vector": [1.0, 0.0]}],
                },
            },
        }
        tool = {}
        register(SimpleNamespace(register_tool=lambda **kwargs: tool.update(kwargs)))
        with TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "index.json"
            index_path.write_text(json.dumps(index))
            env = {
                "LOCOMO_RAG_INDEX": str(index_path),
                "LOCOMO_RAG_EMBEDDING_MODEL": "test",
            }
            output = []
            with patch.dict(os.environ, env):
                for call_id, query, vector in [
                    ("1", "initial", [1.0, 0.0]),
                    ("2", "refined", [0.0, 1.0]),
                ]:
                    arguments = {"query": query, "scope": "scope-a"}
                    with patch("locomo_rag.embed", return_value=([vector], 3)):
                        result = tool["handler"](arguments)
                    output.extend(
                        [
                            {
                                "type": "function_call",
                                "name": TOOL_NAME,
                                "call_id": call_id,
                                "arguments": json.dumps(arguments),
                            },
                            {"type": "function_call_output", "call_id": call_id, "output": result},
                        ]
                    )
        trace = rag_search_trace({"output": output}, "scope-a", {"a1", "a2"})
        self.assertEqual([item["type"] for item in trace], ["call", "result", "call", "result"])
        self.assertEqual(trace[1]["chunk_ids"], ["a1", "a2"])
        self.assertEqual(trace[2]["query"], "refined")
        self.assertEqual(trace[3]["chunk_ids"], ["a2", "a1"])
        with self.assertRaisesRegex(ValueError, "different history scope"):
            rag_search_trace({"output": output}, "scope-b", {"b1"})
        with self.assertRaisesRegex(ValueError, "completed history search"):
            rag_search_trace({"output": output[:-1]}, "scope-a", {"a1", "a2"})

    def test_rejects_a_foreign_tool(self):
        output = [{"type": "function_call", "name": "session_search"}]
        with self.assertRaisesRegex(ValueError, "Unexpected tool"):
            rag_search_trace({"output": output}, "scope-a", {"a1"})
