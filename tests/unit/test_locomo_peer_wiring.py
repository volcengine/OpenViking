import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


def _load_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / relative_path
    module_dir = str(module_path.parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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


@pytest.mark.asyncio
async def test_process_single_session_marks_ingest_record_with_canonical_sample_id(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "result" / "locomo").mkdir(parents=True)
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    http_client = httpx.AsyncClient
    requests = []
    entries = {
        ("POST", "/api/v1/sessions"): {"session_id": "sess-1"},
        ("POST", "/api/v1/sessions/sess-1/messages"): {},
        ("GET", "/api/v1/sessions/sess-1"): {"commit_count": 0},
        ("POST", "/api/v1/sessions/sess-1/commit"): {
            "status": "accepted",
            "task_id": "task-1",
            "trace_id": "trace-1",
        },
        ("GET", "/api/v1/tasks/task-1"): {"status": "completed"},
    }

    def request_handler(request):
        assert request.headers["X-API-Key"] == "user-key"
        assert "X-OpenViking-User" not in request.headers
        path = request.url.path
        requests.append((path, json.loads(request.content) if request.content else {}))
        return httpx.Response(200, json={"status": "ok", "result": entries[(request.method, path)]})

    monkeypatch.setattr(
        "openviking_sdk.client.httpx.AsyncClient",
        lambda **kwargs: http_client(**kwargs, transport=httpx.MockTransport(request_handler)),
    )
    for group_chat, peers, text in (
        (False, ["conv-26", "conv-26"], ["Alice: Hi Bob", "Bob: Hello Alice"]),
        (True, ["Alice", "Bob"], ["Hi Bob", "Hello Alice"]),
    ):
        requests.clear()
        ingest_record: dict = {}
        success_csv = tmp_path / f"import-{group_chat}.csv"
        [session] = IMPORT_TO_OV.build_session_messages(_sample_payload(), group_chat=group_chat)
        result = await IMPORT_TO_OV.process_single_session(
            messages=session["messages"],
            meta=session["meta"],
            sample_id="conv-26",
            display_id="sample_0",
            session_key="session_1",
            ingest_record=ingest_record,
            args=SimpleNamespace(
                api_key="user-key",
                auth_mode="api_key",
                user="",
                account="",
                openviking_url="http://openviking.test",
                group_chat=group_chat,
                success_csv=str(success_csv),
                error_log=str(tmp_path / "import_errors.log"),
                _show_progress=True,
            ),
        )

        assert result["status"] == "success", result
        assert (result["sample_id"], result["display_id"], result["trace_id"]) == (
            "conv-26",
            "sample_0",
            "trace-1",
        )
        assert [path for path, _ in requests] == [
            "/api/v1/sessions",
            "/api/v1/sessions/sess-1/messages",
            "/api/v1/sessions/sess-1/messages",
            "/api/v1/sessions/sess-1",
            "/api/v1/sessions/sess-1/commit",
            "/api/v1/tasks/task-1",
        ]
        assert requests[0][1]["memory_policy"] == {
            "self": {"enabled": False},
            "peer": {"enabled": True},
            "working_memory": {"enabled": False},
            "memory_types": ["entities", "events", "preferences", "profile"],
        }
        messages = [payload for _, payload in requests[1:3]]
        assert [message["peer_id"] for message in messages] == peers
        assert [message["parts"][0]["text"] for message in messages] == text
        assert requests[-2][1]["telemetry"] is True
        persisted = IMPORT_TO_OV.load_ingest_record()
        assert IMPORT_TO_OV.is_already_ingested("conv-26", "session_1", persisted)
        assert not IMPORT_TO_OV.is_already_ingested("sample_0", "session_1", persisted)
        with success_csv.open(encoding="utf-8", newline="") as stream:
            [row] = list(csv.DictReader(stream))
        assert (row["sample_id"], row["display_id"], row["session"]) == (
            "conv-26",
            "sample_0",
            "session_1",
        )


@pytest.mark.parametrize(
    ("group_args", "sender", "memory_peers"),
    [([], "conv-26", []), (["--group-chat"], "Alice", ["Bob"])],
    ids=["sample-peer", "speaker-peers"],
)
def test_run_eval_persists_answer_from_the_imported_peer(
    monkeypatch, tmp_path, group_args, sender, memory_peers
):
    input_path = tmp_path / "locomo.json"
    output_path = tmp_path / "result.csv"
    errors_path = tmp_path / "errors.json"
    input_path.write_text(json.dumps([_sample_payload()]), encoding="utf-8")
    errors_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("LOCOMO_VIKINGBOT_LOG_DIR", str(tmp_path / "logs"))
    commands = []

    def run_cli(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "text": "Bob",
                    "token_usage": {"total_tokens": 3},
                    "time_cost": 0.1,
                    "iteration": 1,
                    "tools_used_names": ["openviking_search"],
                }
            )
        )

    monkeypatch.setattr(RUN_EVAL.subprocess, "run", run_cli)
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
            *group_args,
        ],
    )
    RUN_EVAL.main()

    reset, answer = commands
    assert reset[reset.index("-m") + 1] == "/new"
    assert "Who said hello?" in answer[answer.index("-m") + 1]
    for cmd in commands:
        assert cmd[:2] == ["vikingbot", "chat"]
        assert cmd[cmd.index("--sender") + 1] == sender
        assert cmd[cmd.index("--session") + 1] == "sample_0_qa0"
        assert [
            value for flag, value in zip(cmd[:-1], cmd[1:], strict=True) if flag == "--memory-peer"
        ] == memory_peers
    with output_path.open(encoding="utf-8", newline="") as stream:
        [row] = list(csv.DictReader(stream))
    assert (row["sample_id"], row["question"], row["response"], row["iteration"]) == (
        "sample_0",
        "Who said hello?",
        "Bob",
        "1",
    )
    assert json.loads(row["token_usage"]) == {"total_tokens": 3}
    assert json.loads(row["tools_used_names"]) == ["openviking_search"]
