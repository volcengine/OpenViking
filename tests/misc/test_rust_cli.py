import pytest

from openviking_cli import rust_cli


def test_main_executes_npm_shebang_wrapper(monkeypatch, tmp_path):
    wrapper = tmp_path / "ov"
    wrapper.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    class Executed(Exception):
        pass

    monkeypatch.setattr(rust_cli.Path, "exists", lambda _path: False)
    monkeypatch.setattr(rust_cli, "which", lambda _name: str(wrapper))
    monkeypatch.setattr(rust_cli, "_exec_binary", lambda *_args: (_ for _ in ()).throw(Executed))
    monkeypatch.setattr(rust_cli.sys, "argv", [str(tmp_path / "openviking"), "--version"])

    with pytest.raises(Executed):
        rust_cli.main()
