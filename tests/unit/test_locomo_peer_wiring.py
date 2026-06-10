import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


IMPORT_TO_OV = _load_module(
    "test_import_to_ov_module", "benchmark/locomo/vikingbot/import_to_ov.py"
)
RUN_EVAL = _load_module("test_run_eval_module", "benchmark/locomo/vikingbot/run_eval.py")


def _sample_payload():
    return {
        "sample_id": "conv-26",
        "conversation": {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1": [
                {"speaker": "Alice", "text": "Hi Bob"},
                {"speaker": "Bob", "text": "Hello Alice"},
            ],
            "session_1_date_time": "1:56 pm on 8 May, 2023",
        },
        "qa": [
            {
                "question": "Who said hello?",
                "answer": "Bob",
                "category": "1",
                "evidence": ["D1:2"],
            }
        ],
    }


def test_build_memory_policy_writes_peer_only():
    assert IMPORT_TO_OV.build_memory_policy(False) == {
        "self": {"enabled": False},
        "peer": {"enabled": True},
    }
    assert IMPORT_TO_OV.build_memory_policy(True) == {
        "self": {"enabled": False},
        "peer": {"enabled": True},
    }


def test_build_session_messages_non_group_uses_sample_peer_and_prefixes_speaker():
    sessions = IMPORT_TO_OV.build_session_messages(_sample_payload(), group_chat=False)

    assert len(sessions) == 1
    messages = sessions[0]["messages"]
    assert [msg["peer_id"] for msg in messages] == ["conv-26", "conv-26"]
    assert messages[0]["text"] == "Alice: Hi Bob"
    assert messages[1]["text"] == "Bob: Hello Alice"




@pytest.mark.asyncio
async def test_viking_ingest_uses_message_peer_id(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def initialize(self):
            return None

        async def create_session(self, memory_policy=None):
            calls.append(("create_session", memory_policy))
            return {"session_id": "sess-1"}

        async def add_message(
            self, session_id=None, role=None, parts=None, created_at=None, peer_id=None
        ):
            calls.append(
                (
                    "add_message",
                    {
                        "session_id": session_id,
                        "role": role,
                        "parts": parts,
                        "created_at": created_at,
                        "peer_id": peer_id,
                    },
                )
            )

        async def commit_session(self, session_id, telemetry=True, memory_policy=None):
            calls.append(("commit_session", memory_policy))
            return {"status": "accepted", "task_id": None, "trace_id": "trace-1"}

        async def close(self):
            return None

    monkeypatch.setattr(IMPORT_TO_OV.ov, "AsyncHTTPClient", lambda **kwargs: FakeClient(**kwargs))

    result = await IMPORT_TO_OV.viking_ingest(
        messages=[{"role": "user", "text": "Alice: Hi Bob", "peer_id": "conv-26"}],
        openviking_url="http://localhost:1933",
        session_time=None,
        user_id="",
        account="",
        api_key="user-key",
        group_chat=False,
    )

    assert result["trace_id"] == "trace-1"
    add_calls = [entry for entry in calls if entry[0] == "add_message"]
    assert len(add_calls) == 1
    assert add_calls[0][1]["peer_id"] == "conv-26"


def test_load_locomo_qa_keeps_internal_and_original_sample_ids(tmp_path):
    input_path = tmp_path / "locomo.json"
    input_path.write_text(json.dumps([_sample_payload()]), encoding="utf-8")

    qa_list = RUN_EVAL.load_locomo_qa(str(input_path))

    assert len(qa_list) == 1
    assert qa_list[0]["sample_id"] == "sample_0"
    assert qa_list[0]["original_sample_id"] == "conv-26"
    assert qa_list[0]["speakers"] == ["Alice", "Bob"]


def test_run_vikingbot_chat_non_group_builds_sender_without_memory_peers(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, timeout=None, check=False):
        calls.append(cmd)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "text": "ok",
                    "token_usage": {"total_tokens": 3},
                    "time_cost": 0.1,
                    "iteration": 1,
                    "tools_used_names": [],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(RUN_EVAL.subprocess, "run", fake_run)

    response, token_usage, _time_cost, iteration, tools_used_names = RUN_EVAL.run_vikingbot_chat(
        question="Who said hello?",
        question_time="2023-05-08",
        sender_peer_id="conv-26",
        question_id="sample_0_qa0",
        config="/tmp/ov.conf",
        memory_peer_ids=None,
    )

    assert response == "ok"
    assert token_usage == {"total_tokens": 3}
    assert iteration == 1
    assert tools_used_names == []
    assert len(calls) == 2
    assert calls[0].count("--memory-peer") == 0
    assert calls[1].count("--memory-peer") == 0
    assert calls[0][calls[0].index("--sender") + 1] == "conv-26"
    assert calls[1][calls[1].index("--sender") + 1] == "conv-26"


def test_run_eval_main_default_mode_uses_original_sample_id_as_sender_peer(monkeypatch, tmp_path):
    input_path = tmp_path / "locomo.json"
    output_path = tmp_path / "result.csv"
    errors_path = tmp_path / "errors.json"
    input_path.write_text(json.dumps([_sample_payload()]), encoding="utf-8")
    errors_path.write_text("[]", encoding="utf-8")

    captured = []

    def fake_run_vikingbot_chat(
        question,
        question_time=None,
        sender_peer_id=None,
        question_id=None,
        config=None,
        memory_peer_ids=None,
    ):
        captured.append(
            {
                "question": question,
                "question_time": question_time,
                "sender_peer_id": sender_peer_id,
                "question_id": question_id,
                "memory_peer_ids": memory_peer_ids,
            }
        )
        return ("ok", {"total_tokens": 1}, 0.1, 1, [])

    monkeypatch.setattr(RUN_EVAL, "run_vikingbot_chat", fake_run_vikingbot_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            str(input_path),
            "--output",
            str(output_path),
            "--errors",
            str(errors_path),
            "--threads",
            "1",
        ],
    )

    RUN_EVAL.main()

    assert len(captured) == 1
    assert captured[0]["sender_peer_id"] == "conv-26"
    assert captured[0]["memory_peer_ids"] is None
    assert captured[0]["question_id"] == "sample_0_qa0"


def test_run_eval_main_group_chat_uses_speaker_peers(monkeypatch, tmp_path):
    input_path = tmp_path / "locomo.json"
    output_path = tmp_path / "result.csv"
    errors_path = tmp_path / "errors.json"
    input_path.write_text(json.dumps([_sample_payload()]), encoding="utf-8")
    errors_path.write_text("[]", encoding="utf-8")

    captured = []

    def fake_run_vikingbot_chat(
        question,
        question_time=None,
        sender_peer_id=None,
        question_id=None,
        config=None,
        memory_peer_ids=None,
    ):
        captured.append(
            {
                "question": question,
                "question_time": question_time,
                "sender_peer_id": sender_peer_id,
                "question_id": question_id,
                "memory_peer_ids": memory_peer_ids,
            }
        )
        return ("ok", {"total_tokens": 1}, 0.1, 1, [])

    monkeypatch.setattr(RUN_EVAL, "run_vikingbot_chat", fake_run_vikingbot_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_eval.py",
            str(input_path),
            "--output",
            str(output_path),
            "--errors",
            str(errors_path),
            "--threads",
            "1",
            "--group-chat",
        ],
    )

    RUN_EVAL.main()

    assert len(captured) == 1
    assert captured[0]["sender_peer_id"] == "Alice"
    assert captured[0]["memory_peer_ids"] == ["Bob"]
    assert captured[0]["question_id"] == "sample_0_qa0"
