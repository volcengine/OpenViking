"""Native store commits reach the external plugin with exact entry identity."""

import json
import time
from contextlib import contextmanager

import pytest


class _Client:
    _endpoint, _api_key, _account, _user, _agent = "http://test", "", "test", "alice", ""

    def __init__(self):
        self.files = {}
        self.requests = []
        self.refresh_failure = None

    def get(self, path, **kwargs):
        return {"result": {"user": self._user}}

    def post(self, path, payload):
        self.requests.append(("write", dict(payload)))
        self.files[payload["uri"]] = payload["content"]
        result = {
            "uri": payload["uri"],
            "content_updated": True,
            "semantic_status": "skipped",
            "vector_status": "complete",
        }
        if self.refresh_failure:
            result[self.refresh_failure] = "failed"
        return {"status": "ok", "result": result}

    def delete(self, path, *, params):
        self.requests.append(("delete", dict(params)))
        del self.files[params["uri"]]
        return {"status": "ok", "result": {"uri": params["uri"]}}


def _drain(provider):
    mirror = getattr(provider, "_native_memory_mirror", None)
    deadline = time.monotonic() + 3
    while mirror and mirror._queue.unfinished_tasks:
        assert time.monotonic() < deadline, "Mirror worker did not drain"
        time.sleep(0.01)


@contextmanager
def _bridge(external_provider):
    from agent.memory_manager import MemoryManager
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.memory_tool import MemoryStore, memory_tool

    home, provider, _, _ = external_provider("native-bridge")
    provider._hermes_home = str(home)
    client = _Client()
    provider._ensure_client = provider._new_client = lambda: client
    token = set_hermes_home_override(home)
    store = MemoryStore()
    store.load_from_disk()
    manager = MemoryManager()
    manager.add_provider(provider)

    def invoke(*operations, batch=False, mirror=True):
        args = {"target": "user", **({"operations": list(operations)} if batch else operations[0])}
        result = memory_tool(store=store, **args)
        if mirror:
            manager.notify_memory_tool_write(result, args)
            _drain(provider)
        return json.loads(result)

    try:
        yield home, provider, client, store, invoke
    finally:
        provider.shutdown()
        reset_hermes_home_override(token)


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("action", ["remove", "replace"])
def test_native_exact_entry_does_not_mutate_its_longer_sibling(
    external_provider, mapped, batch, action
):
    with _bridge(external_provider) as (_, _, client, store, invoke):
        assert invoke({"action": "add", "content": "Prefers tea"}, mirror=mapped)["success"]
        exact_uri = next(iter(client.files), None)
        assert invoke({"action": "add", "content": "Prefers tea with milk"})["success"]
        expected = dict(client.files)
        operation = {"action": action, "old_text": "Prefers tea"}
        if action == "replace":
            operation["new_text"] = "Prefers coffee"
            if mapped:
                expected[exact_uri] = "Prefers coffee"
        elif mapped:
            del expected[exact_uri]
        assert invoke(operation, batch=batch)["success"]
        assert store.user_entries == (
            ["Prefers coffee", "Prefers tea with milk"]
            if action == "replace"
            else ["Prefers tea with milk"]
        )
        assert client.files == expected


def test_committed_batch_mirrors_each_intermediate_entry_in_order(external_provider):
    with _bridge(external_provider) as (home, _, client, store, invoke):
        assert invoke(
            {"action": "add", "content": "Prefers tea"},
            {"action": "replace", "old_text": "tea", "new_text": "Prefers coffee"},
            {"action": "remove", "old_text": "coffee"},
            batch=True,
        )["success"]
        assert store.user_entries == []
        assert client.files == {}
        assert [(method, body.get("mode")) for method, body in client.requests] == [
            ("write", "create"),
            ("write", "replace"),
            ("delete", None),
        ]
        assert len({body["uri"] for _, body in client.requests}) == 1
        assert (
            json.loads((home / "openviking/memory_mirror_registry.json").read_text())["entries"]
            == []
        )


@pytest.mark.parametrize("failure", ["missing_entry", "over_budget", "disk_write"])
def test_uncommitted_batch_has_no_remote_effect(external_provider, monkeypatch, failure):
    with _bridge(external_provider) as (home, provider, client, store, invoke):
        assert invoke({"action": "add", "content": "Keep this entry"}, mirror=False)["success"]
        before = store._path_for("user").read_bytes()
        operations = [
            {"action": "add", "content": "Prefers tea"},
            {"action": "replace", "old_text": "tea", "content": "Prefers coffee"},
        ]
        if failure == "missing_entry":
            operations.append({"action": "remove", "old_text": "Absent entry"})
        elif failure == "over_budget":
            operations.append({"action": "add", "content": "x" * 2000})
        else:

            def fail_write(*_args):
                raise OSError("disk unavailable")

            monkeypatch.setattr(store, "_write_file", fail_write)
        if failure == "disk_write":
            with pytest.raises(OSError, match="disk unavailable"):
                invoke(*operations, batch=True)
        else:
            assert not invoke(*operations, batch=True)["success"]
        assert client.requests == []
        assert not getattr(provider, "_native_memory_mirror", None)
        assert not (home / "openviking/memory_mirror_registry.json").exists()
        assert store._path_for("user").read_bytes() == before


@pytest.mark.parametrize("action", ["replace", "remove"])
@pytest.mark.parametrize("old_text", ["Prefers tea", "tea"])
def test_legacy_notifications_never_guess_entry_identity(
    external_provider, caplog, action, old_text
):
    with _bridge(external_provider) as (home, provider, client, _, invoke):
        assert invoke({"action": "add", "content": "Prefers tea"})["success"]
        path = home / "openviking/memory_mirror_registry.json"
        before = path.read_bytes()
        remote_before = dict(client.files)
        with caplog.at_level("WARNING", logger="plugins.memory.openviking"):
            provider.on_memory_write(
                action, "user", "Prefers coffee", metadata={"old_text": old_text}
            )
            _drain(provider)
        assert client.files == remote_before
        assert path.read_bytes() == before
        assert any("authoritative previous_content" in record.message for record in caplog.records)


@pytest.mark.parametrize("failed_status", ["vector_status", "semantic_status"])
def test_index_failure_preserves_mapping_for_next_replace_and_remove(
    external_provider, caplog, failed_status
):
    with _bridge(external_provider) as (home, _, client, _, invoke):
        assert invoke({"action": "add", "content": "Prefers tea"})["success"]
        uri = next(iter(client.files))
        client.refresh_failure = failed_status
        with caplog.at_level("WARNING", logger="plugins.memory.openviking"):
            assert invoke({"action": "replace", "old_text": "tea", "content": "Prefers coffee"})[
                "success"
            ]
        path = home / "openviking/memory_mirror_registry.json"
        entry = json.loads(path.read_text())["entries"][0]
        assert entry["uri"] == uri
        assert entry["content"] == client.files[uri] == "Prefers coffee"
        assert any(
            uri in record.message
            and "indexing failed" in record.message
            and failed_status in record.message
            for record in caplog.records
        )
        client.refresh_failure = None
        assert invoke({"action": "replace", "old_text": "coffee", "content": "Prefers water"})[
            "success"
        ]
        assert client.files == {uri: "Prefers water"}
        assert invoke({"action": "remove", "old_text": "water"})["success"]
        assert client.files == {}
        assert json.loads(path.read_text())["entries"] == []
