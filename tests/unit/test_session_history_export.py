import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from openviking_sdk.errors import NotFoundError


@pytest.fixture
def exporter():
    path = Path(__file__).parents[2] / "examples/session-history-export/export_history.py"
    spec = importlib.util.spec_from_file_location("history_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Client:
    root = "viking://user/default/sessions/source"

    def __init__(self):
        self.old = {
            "id": "u1",
            "role": "user",
            "parts": [{"type": "text", "text": "Keep\u2028this."}],
        }
        self.tail = {"id": "a1", "role": "assistant", "parts": [{"type": "text", "text": "Done."}]}
        self.files = {
            f"{self.root}/history/archive_001/messages.jsonl": json.dumps(
                self.old, ensure_ascii=False
            ),
            f"{self.root}/history/archive_001/.failed.json": "{}",
            f"{self.root}/messages.jsonl": "\n".join(
                json.dumps(m, ensure_ascii=False) for m in [self.old, self.tail]
            ),
        }

    def get_session(self, _):
        return {"uri": self.root}

    def ls(self, *args, **kwargs):
        return [{"name": "archive_001"}]

    def read(self, uri):
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]


def test_exports_failed_raw_and_active_without_context_or_duplicate_anchor(exporter, tmp_path):
    client = Client()
    manifest = exporter.export_history(client, "source", tmp_path)
    assert (
        hashlib.sha256((tmp_path / "history.json").read_bytes()).hexdigest() == manifest["sha256"]
    )
    content = json.loads((tmp_path / "history.json").read_text())
    assert content["messages"] == [client.old, client.tail]
    assert manifest["status"] == "exported_not_imported"
    assert exporter.export_history(client, "source", tmp_path) == manifest
    assert "Keep\u2028this." in (tmp_path / "handoff.md").read_text()


def test_conflicting_copies_require_manual_review(exporter, tmp_path):
    client = Client()
    changed = {**client.old, "parts": [{"type": "text", "text": "changed"}]}
    client.files[f"{client.root}/messages.jsonl"] = json.dumps(changed)
    with pytest.raises(ValueError, match="Conflicting copies"):
        exporter.export_history(client, "source", tmp_path)
    assert not (tmp_path / "manifest.json").exists()


def test_mutating_source_never_publishes_completed_export(exporter, tmp_path):
    client = Client()
    original = client.get_session
    calls = 0

    def changing(session_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            client.files[f"{client.root}/.meta.json"] = '{"changed": true}'
        return original(session_id)

    client.get_session = changing
    with pytest.raises(RuntimeError, match="Session changed"):
        exporter.export_history(client, "source", tmp_path)
    assert not (tmp_path / "manifest.json").exists()
