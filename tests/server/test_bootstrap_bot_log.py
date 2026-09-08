# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""The gateway failure report must survive the bytes the gateway wrote.

`vikingbot.log` is filled by the child process through the file descriptor, so
its contents are whatever that process emitted — not something this process
encoded. Reading it back with the locale codec made the report platform
dependent, and it is read on exactly one path: the one that runs when the
gateway has already failed. A decode error there replaces the failure it was
called to explain.
"""

from pathlib import Path

from openviking.server.bootstrap import _handle_vikingbot_failure, _read_bot_log


def test_a_byte_no_codec_accepts_does_not_sink_the_report(tmp_path: Path):
    """0x81 is invalid UTF-8 and undefined in cp1252, so a strict read of it
    raises on Linux and on Windows alike. The report must still arrive."""
    log = tmp_path / "vikingbot.log"
    log.write_bytes(b"Traceback (most recent call last):\n\x81\nModuleNotFoundError: no bot\n")

    output = _read_bot_log(str(log))

    assert "ModuleNotFoundError" in output
    assert "Traceback" in output


def test_non_ascii_output_is_read_as_utf8(tmp_path: Path):
    """The child writes UTF-8. Read under a console codepage instead, the text
    comes back as mojibake and the report quietly misleads."""
    log = tmp_path / "vikingbot.log"
    log.write_bytes("ModuleNotFoundError: 缺少依赖 café\n".encode("utf-8"))

    output = _read_bot_log(str(log))

    assert "缺少依赖" in output
    assert "café" in output


def test_missing_log_reports_itself_instead_of_raising(tmp_path: Path):
    output = _read_bot_log(str(tmp_path / "absent.log"))

    assert "absent.log" in output


def test_dependency_hint_still_fires_through_undecodable_bytes(tmp_path: Path, capsys):
    """The end-to-end point: the hint is what the user needs, and it is chosen by
    searching the decoded text. If the decode raises, the hint never prints."""
    log = tmp_path / "vikingbot.log"
    log.write_bytes(b"\x81\x82 ModuleNotFoundError: No module named 'vikingbot'\n")

    _handle_vikingbot_failure(_read_bot_log(str(log)), 1)

    stderr = capsys.readouterr().err
    assert "Missing dependencies detected!" in stderr
    assert 'pip install "openviking[bot]"' in stderr
