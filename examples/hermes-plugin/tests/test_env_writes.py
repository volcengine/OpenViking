"""Preserve .env line endings through the external provider's setup writer."""

import os
import stat

import pytest
from dotenv import dotenv_values


def test_lf_file_keeps_lf_on_every_platform(external_provider):
    """Writing one variable must not retype the whole file's line endings.

    Text mode on Windows translates "\n" to CRLF, so an LF .env came back
    with every untouched line rewritten.
    """
    home, _, module, _ = external_provider("env-lf")
    env = home / ".env"
    env.write_bytes(b"A=1\nOPENAI_API_KEY=old\nB=2\n")

    module._write_env_vars(env, {"OPENAI_API_KEY": "new"})

    assert env.read_bytes() == b"A=1\nOPENAI_API_KEY=new\nB=2\n"


def test_crlf_file_keeps_crlf(external_provider):
    """A file saved with CRLF keeps its own line ending."""
    home, _, module, _ = external_provider("env-crlf")
    env = home / ".env"
    env.write_bytes(b"A=1\r\nOPENAI_API_KEY=old\r\nB=2\r\n")

    module._write_env_vars(env, {"OPENAI_API_KEY": "new"})

    assert env.read_bytes() == b"A=1\r\nOPENAI_API_KEY=new\r\nB=2\r\n"


@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_env_updates_preserve_bytes_and_safe_credentials(external_provider, eol):
    home, _, module, _ = external_provider("env-credentials")
    env = home / ".env"
    env.write_bytes(
        b"\xef\xbb\xbf" + eol.join([b"OPENAI_API_KEY=old", b"NAME=caf\xe9", b"REMOVE=old"])
        + eol
    )

    module._write_env_vars(
        env, {"OPENAI_API_KEY": "new", "ADDED": "safe\r\nvalue\x00"},
        remove_keys=("REMOVE", "OPENAI_API_KEY"),
    )

    assert env.read_bytes() == eol.join(
        [b"OPENAI_API_KEY=new", b"NAME=caf\xe9", b"ADDED=safevalue"]
    ) + eol
    if os.name == "posix":
        assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_new_env_file_uses_lf_and_can_be_emptied(external_provider):
    home, _, module, _ = external_provider("env-new")
    env = home / "nested" / ".env"

    module._write_env_vars(env, {"OPENAI_API_KEY": "new"})
    assert env.read_bytes() == b"OPENAI_API_KEY=new\n"
    if os.name == "posix":
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
    module._write_env_vars(env, {}, remove_keys=("OPENAI_API_KEY",))
    assert env.read_bytes() == b""


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize(
    "separator",
    ["\u0085", "\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"],
    ids=["nel", "line-separator", "paragraph-separator", "vt", "ff", "fs", "gs", "rs"],
)
def test_untouched_values_keep_non_newline_characters(external_provider, eol, separator):
    home, _, module, _ = external_provider("env-value-characters")
    env = home / ".env"
    value = f"left{separator}right"
    env.write_bytes(f"OTHER_API_KEY={value}{eol}KEY=old{eol}".encode())
    assert dotenv_values(env)["OTHER_API_KEY"] == value

    module._write_env_vars(env, {"KEY": "new"})

    assert env.read_bytes() == f"OTHER_API_KEY={value}{eol}KEY=new{eol}".encode()
    assert dotenv_values(env)["OTHER_API_KEY"] == value


@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r"], ids=["lf", "crlf", "cr"])
@pytest.mark.parametrize("terminated", [True, False], ids=["terminated", "unterminated"])
def test_physical_line_endings_keep_records_and_blank_lines(external_provider, eol, terminated):
    home, _, module, _ = external_provider("env-physical-lines")
    env = home / ".env"
    env.write_bytes(
        eol.join([b"KEEP=one", b"", b"KEY=old", b"", b"OTHER=two"])
        + (eol if terminated else b"")
    )

    module._write_env_vars(env, {"KEY": "new"})

    output_eol = b"\r\n" if eol == b"\r\n" else b"\n"
    assert env.read_bytes() == output_eol.join(
        [b"KEEP=one", b"", b"KEY=new", b"", b"OTHER=two"]
    ) + output_eol
    assert dotenv_values(env)["OTHER"] == "two"
