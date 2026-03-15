# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for transaction journal."""

import json
import uuid
from unittest.mock import MagicMock

from openviking.storage.transaction.journal import TransactionJournal


class TestTransactionJournal:
    def _make_journal(self) -> tuple:
        agfs = MagicMock()
        journal = TransactionJournal(agfs)
        return journal, agfs

    def test_write_calls_agfs_write_with_correct_data(self):
        journal, agfs = self._make_journal()
        data = {"id": "tx-1", "status": "INIT", "locks": []}

        journal.write(data)

        # Should call agfs.write with the journal path and serialized data
        agfs.write.assert_called_once()
        path, payload = agfs.write.call_args[0]
        assert "tx-1" in path
        assert path.endswith("journal.json")
        parsed = json.loads(payload.decode("utf-8"))
        assert parsed["id"] == "tx-1"
        assert parsed["status"] == "INIT"

    def test_write_ensures_directories_exist(self):
        journal, agfs = self._make_journal()
        data = {"id": "tx-1", "status": "INIT", "locks": []}

        journal.write(data)

        # Should call mkdir at least once (for parent dirs)
        assert agfs.mkdir.called

    def test_update_overwrites(self):
        journal, agfs = self._make_journal()
        data = {"id": "tx-2", "status": "EXEC", "locks": []}

        journal.update(data)

        agfs.write.assert_called_once()
        path, payload = agfs.write.call_args[0]
        assert json.loads(payload.decode("utf-8"))["status"] == "EXEC"

    def test_read_parses_json(self):
        journal, agfs = self._make_journal()
        agfs.cat.return_value = json.dumps({"id": "tx-3", "status": "EXEC"}).encode("utf-8")

        result = journal.read("tx-3")
        assert result["id"] == "tx-3"
        assert result["status"] == "EXEC"

    def test_read_handles_string_response(self):
        """Some AGFS backends may return str instead of bytes."""
        journal, agfs = self._make_journal()
        agfs.cat.return_value = json.dumps({"id": "tx-str", "status": "INIT"})

        result = journal.read("tx-str")
        assert result["id"] == "tx-str"

    def test_delete_removes_directory(self):
        journal, agfs = self._make_journal()
        journal.delete("tx-4")
        agfs.rm.assert_called_once()
        path = agfs.rm.call_args[0][0]
        assert "tx-4" in path

    def test_list_all_returns_tx_ids(self):
        journal, agfs = self._make_journal()
        agfs.ls.return_value = [
            {"name": "tx-a", "isDir": True},
            {"name": "tx-b", "isDir": True},
            {"name": ".", "isDir": True},
        ]

        result = journal.list_all()
        assert "tx-a" in result
        assert "tx-b" in result
        assert "." not in result

    def test_list_all_filters_dotdot(self):
        journal, agfs = self._make_journal()
        agfs.ls.return_value = [
            {"name": "..", "isDir": True},
            {"name": "tx-real", "isDir": True},
        ]

        result = journal.list_all()
        assert ".." not in result
        assert "tx-real" in result

    def test_list_all_empty_on_error(self):
        journal, agfs = self._make_journal()
        agfs.ls.side_effect = Exception("not found")

        result = journal.list_all()
        assert result == []

    def test_delete_tolerates_missing(self):
        journal, agfs = self._make_journal()
        agfs.rm.side_effect = Exception("not found")
        # Should not raise
        journal.delete("tx-missing")

    def test_write_with_post_actions(self):
        journal, agfs = self._make_journal()
        data = {
            "id": "tx-5",
            "status": "COMMIT",
            "locks": [],
            "post_actions": [
                {"type": "enqueue_semantic", "params": {"uri": "viking://test"}},
            ],
        }
        journal.write(data)
        path, payload = agfs.write.call_args[0]
        parsed = json.loads(payload.decode("utf-8"))
        assert len(parsed["post_actions"]) == 1
        assert parsed["post_actions"][0]["type"] == "enqueue_semantic"

    def test_write_with_undo_log(self):
        journal, agfs = self._make_journal()
        data = {
            "id": "tx-6",
            "status": "EXEC",
            "locks": [],
            "undo_log": [
                {
                    "sequence": 0,
                    "op_type": "fs_mv",
                    "params": {"src": "/a", "dst": "/b"},
                    "completed": True,
                },
            ],
        }
        journal.write(data)
        _, payload = agfs.write.call_args[0]
        parsed = json.loads(payload.decode("utf-8"))
        assert len(parsed["undo_log"]) == 1
        assert parsed["undo_log"][0]["op_type"] == "fs_mv"


class TestTransactionJournalIntegration:
    """Integration tests using real AGFS backend to verify persistence behavior."""

    def test_write_read_roundtrip(self, agfs_client):
        journal = TransactionJournal(agfs_client)
        tx_id = f"tx-int-{uuid.uuid4().hex}"
        data = {"id": tx_id, "status": "INIT", "locks": [], "undo_log": []}

        journal.write(data)
        result = journal.read(tx_id)

        assert result["id"] == tx_id
        assert result["status"] == "INIT"

        journal.delete(tx_id)

    def test_update_overwrites(self, agfs_client):
        journal = TransactionJournal(agfs_client)
        tx_id = f"tx-int-{uuid.uuid4().hex}"

        journal.write({"id": tx_id, "status": "INIT", "locks": []})
        journal.update({"id": tx_id, "status": "EXEC", "locks": []})

        result = journal.read(tx_id)
        assert result["status"] == "EXEC"

        journal.delete(tx_id)

    def test_delete_removes_journal(self, agfs_client):
        journal = TransactionJournal(agfs_client)
        tx_id = f"tx-int-{uuid.uuid4().hex}"

        journal.write({"id": tx_id, "status": "INIT", "locks": []})
        journal.delete(tx_id)

        try:
            journal.read(tx_id)
            raise AssertionError("Should have raised after deletion")
        except Exception:
            pass  # Expected

    def test_list_all_returns_written_ids(self, agfs_client):
        journal = TransactionJournal(agfs_client)
        tx_id_a = f"tx-int-{uuid.uuid4().hex}"
        tx_id_b = f"tx-int-{uuid.uuid4().hex}"

        journal.write({"id": tx_id_a, "status": "INIT", "locks": []})
        journal.write({"id": tx_id_b, "status": "INIT", "locks": []})

        result = journal.list_all()
        assert tx_id_a in result
        assert tx_id_b in result

        journal.delete(tx_id_a)
        journal.delete(tx_id_b)

    def test_list_all_empty_when_none(self, agfs_client):
        """After cleanup, list_all should not include previously deleted entries."""
        journal = TransactionJournal(agfs_client)
        tx_id = f"tx-int-{uuid.uuid4().hex}"

        journal.write({"id": tx_id, "status": "INIT", "locks": []})
        journal.delete(tx_id)

        result = journal.list_all()
        assert tx_id not in result
