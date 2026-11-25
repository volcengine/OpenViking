# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import tempfile
import zipfile
from pathlib import Path

import pytest

from openviking_sdk.uploads import zip_directory


@pytest.mark.parametrize("placement", ["outside", "same", "nested", "relative"])
def test_directory_zip_never_contains_its_own_output(tmp_path, monkeypatch, placement):
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.txt").write_text("hello", encoding="utf-8")
    (source / "existing.zip").write_bytes(b"existing input")

    if placement == "outside":
        temp_dir = tmp_path / "temporary"
    elif placement == "same":
        temp_dir = source
    else:
        temp_dir = source / "temporary"
    temp_dir.mkdir(exist_ok=True)
    if temp_dir != source and placement != "outside":
        (temp_dir / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_dir))

    if placement == "relative":
        monkeypatch.chdir(tmp_path)
        source_argument = "source"
    else:
        source_argument = str(source)

    archive = Path(zip_directory(source_argument))
    with zipfile.ZipFile(archive) as result:
        names = set(result.namelist())
        expected = {"note.txt", "existing.zip"}
        if placement in ("nested", "relative"):
            expected.add("temporary/keep.txt")
        assert names == expected
        assert result.read("note.txt") == b"hello"
        assert result.read("existing.zip") == b"existing input"


def test_empty_directory_with_own_temp_produces_empty_archive(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(source))

    archive = Path(zip_directory(str(source)))
    with zipfile.ZipFile(archive) as result:
        assert result.namelist() == []
