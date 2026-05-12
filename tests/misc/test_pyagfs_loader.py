import sysconfig

import openviking.pyagfs as pyagfs


def test_find_ragfs_so_rejects_mismatched_cpython_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda name: ".cpython-312-x86_64-linux-gnu.so"
    )

    (tmp_path / "ragfs_python.cpython-310-x86_64-linux-gnu.so").touch()

    assert pyagfs._find_ragfs_so() is None


def test_find_ragfs_so_prefers_exact_cpython_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda name: ".cpython-312-x86_64-linux-gnu.so"
    )
    exact = tmp_path / "ragfs_python.cpython-312-x86_64-linux-gnu.so"
    exact.touch()
    (tmp_path / "ragfs_python.cpython-310-x86_64-linux-gnu.so").touch()

    assert pyagfs._find_ragfs_so() == str(exact)


def test_find_ragfs_so_rejects_mismatched_windows_cpython_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: ".cp312-win_amd64.pyd")

    (tmp_path / "ragfs_python.cp310-win_amd64.pyd").touch()

    assert pyagfs._find_ragfs_so() is None


def test_find_ragfs_so_prefers_exact_windows_cpython_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: ".cp312-win_amd64.pyd")
    exact = tmp_path / "ragfs_python.cp312-win_amd64.pyd"
    exact.touch()
    (tmp_path / "ragfs_python.cp310-win_amd64.pyd").touch()

    assert pyagfs._find_ragfs_so() == str(exact)


def test_find_ragfs_so_allows_stable_abi_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda name: ".cpython-312-x86_64-linux-gnu.so"
    )
    abi3 = tmp_path / "ragfs_python.abi3.so"
    abi3.touch()

    assert pyagfs._find_ragfs_so() == str(abi3)
