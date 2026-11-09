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


@pytest.mark.parametrize(("platform", "binary_name"), [("win32", "ov.exe"), ("linux", "ov")])
def test_main_executes_development_binary(monkeypatch, tmp_path, platform, binary_name):
    launcher = tmp_path / "openviking_cli" / "rust_cli.py"
    binary = tmp_path / "target" / "release" / binary_name
    binary.parent.mkdir(parents=True)
    binary.write_text("development executable", encoding="utf-8")
    binary.chmod(0o755)
    executed = []

    def execute(path, argv):
        executed.append((path, argv))
        raise SystemExit(17)

    with monkeypatch.context() as patch:
        patch.setattr(rust_cli, "__file__", str(launcher))
        patch.setattr(rust_cli.sys, "platform", platform)
        patch.setattr(rust_cli.sys, "argv", [str(launcher), "--version"])
        patch.setattr(rust_cli, "_exec_binary", execute)
        with pytest.raises(SystemExit) as result:
            rust_cli.main()

    assert result.value.code == 17
    assert executed == [(str(binary), ["--version"])]
