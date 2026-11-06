# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""An unreadable metadata file must not be rewritten as empty.

``PersistentDict`` treats ``FileStore.get() is None`` as "the file has no
data" and immediately persists the merged dictionary.  A read failure that
is converted to ``None`` therefore wipes ``collection_meta.json`` during a
normal load.
"""

import builtins
import json
import os

import pytest

from openviking.storage.vectordb.meta.local_dict import PersistentDict
from openviking.storage.vectordb.store.file_store import FileStore


def _deny_reads_of(path, monkeypatch):
    real_open = builtins.open
    target = os.path.normcase(os.path.abspath(str(path)))

    def guarded_open(file, *args, **kwargs):
        candidate = file
        if isinstance(candidate, (str, bytes, os.PathLike)):
            normalized = os.path.normcase(os.path.abspath(str(candidate)))
            mode = args[0] if args else kwargs.get("mode", "r")
            if normalized == target and "r" in mode and "+" not in mode:
                raise PermissionError(f"locked: {file}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)


def test_file_store_get_propagates_read_failure(tmp_path, monkeypatch):
    path = tmp_path / "collection_meta.json"
    path.write_text("{}", encoding="utf-8")
    _deny_reads_of(path, monkeypatch)

    with pytest.raises(PermissionError, match="locked"):
        FileStore().get(str(path))


def test_persistent_dict_does_not_overwrite_unreadable_file(tmp_path, monkeypatch):
    path = tmp_path / "collection_meta.json"
    original = {"CollectionName": "demo", "PrimaryKey": "id"}
    path.write_text(json.dumps(original), encoding="utf-8")
    _deny_reads_of(path, monkeypatch)

    with pytest.raises(PermissionError, match="locked"):
        PersistentDict(str(path), {})

    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_persistent_dict_still_creates_missing_file(tmp_path):
    path = tmp_path / "collection_meta.json"

    meta = PersistentDict(str(path), {"CollectionName": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"CollectionName": "new"}
    assert meta.get_raw() == {"CollectionName": "new"}
