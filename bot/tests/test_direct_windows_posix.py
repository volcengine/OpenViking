# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for POSIX command handling in the Windows direct backend."""

import pytest
from vikingbot.sandbox.backends import direct


@pytest.mark.asyncio
async def test_mkdir_p_is_translated(tmp_path):
    target = tmp_path / "credentials" / "nested"

    result = await direct._translate_windows_posix(f"mkdir -p {target}", str(tmp_path))

    assert result == "(no output)"
    assert target.is_dir()


@pytest.mark.asyncio
async def test_env_prefix_is_stripped_before_translation(tmp_path):
    target = tmp_path / "credentials"
    command = (
        "unset OPENVIKING_CLI_CONFIG_FILE; "
        f"export OPENVIKING_CLI_CONFIG_FILE={tmp_path / 'ov.conf'}; "
        f"mkdir -p {target}"
    )

    result = await direct._translate_windows_posix(command, str(tmp_path))

    assert result == "(no output)"
    assert target.is_dir()


@pytest.mark.asyncio
async def test_restricted_mkdir_uses_acl(tmp_path, monkeypatch):
    calls = []

    async def fake_restrict(path, *, directory):
        calls.append((path, directory))
        return None

    monkeypatch.setattr(direct, "_restrict_windows_acl", fake_restrict)
    target = tmp_path / "credentials"

    result = await direct._translate_windows_posix(f"mkdir -m 700 -- {target}", str(tmp_path))

    assert result == "(no output)"
    assert target.is_dir()
    assert calls == [(str(target), True)]


@pytest.mark.asyncio
async def test_chmod_missing_file_reports_error(tmp_path):
    result = await direct._translate_windows_posix(
        f"chmod 600 {tmp_path / 'missing.conf'}", str(tmp_path)
    )

    assert result is not None
    assert "chmod: cannot access" in result
    assert "Exit code: 1" in result


@pytest.mark.asyncio
async def test_unrelated_command_falls_back_to_shell(tmp_path):
    assert await direct._translate_windows_posix("python -c 'print(1)'", str(tmp_path)) is None
    assert await direct._translate_windows_posix("mkdir -p a && chmod 600 a", str(tmp_path)) is None


@pytest.mark.asyncio
async def test_windows_backslash_path_is_preserved(monkeypatch):
    seen = []
    monkeypatch.setattr(
        direct, "_resolve_windows_path", lambda target, base: seen.append(target) or target
    )
    monkeypatch.setattr(direct.os, "makedirs", lambda path, exist_ok: None)

    result = await direct._translate_windows_posix(r"mkdir -p D:\data\credentials", r"C:\work")

    assert result == "(no output)"
    assert seen == [r"D:\data\credentials"]


@pytest.mark.asyncio
async def test_quoted_windows_path_is_preserved(monkeypatch):
    seen = []
    monkeypatch.setattr(
        direct, "_resolve_windows_path", lambda target, base: seen.append(target) or target
    )
    monkeypatch.setattr(direct.os, "makedirs", lambda path, exist_ok: None)

    result = await direct._translate_windows_posix(
        r'mkdir -p "D:\Program Data\credentials"', r"C:\work"
    )

    assert result == "(no output)"
    assert seen == [r"D:\Program Data\credentials"]
