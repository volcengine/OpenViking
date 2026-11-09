from unittest.mock import Mock

import pytest

from openviking_cli import rust_cli


def test_main_prefers_windows_development_binary_then_falls_back_to_npm(monkeypatch, tmp_path):
    wrapper = tmp_path / "ov"
    wrapper.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    binary = tmp_path / "target" / "release" / "ov.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("development executable", encoding="utf-8")
    binary.chmod(0o755)
    execute = Mock(side_effect=SystemExit(17))

    monkeypatch.setattr(rust_cli, "__file__", str(tmp_path / "openviking_cli" / "rust_cli.py"))
    monkeypatch.setattr(rust_cli, "which", lambda _name: str(wrapper))
    monkeypatch.setattr(rust_cli, "_exec_binary", execute)
    monkeypatch.setattr(rust_cli.sys, "argv", [str(tmp_path / "openviking"), "--version"])

    with monkeypatch.context() as patch:
        patch.setattr(rust_cli.sys, "platform", "win32")
        with pytest.raises(SystemExit) as result:
            rust_cli.main()
    assert result.value.code == 17
    execute.assert_called_once_with(str(binary), ["--version"])

    binary.unlink()
    with pytest.raises(SystemExit) as result:
        rust_cli.main()
    assert result.value.code == 17
    assert execute.call_args.args == (str(wrapper), ["--version"])
