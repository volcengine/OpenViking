from pathlib import Path


def test_pyagfs_loader_prefers_abi3_artifact(monkeypatch, tmp_path: Path):
    import openviking.pyagfs as pyagfs

    cpython_artifact = tmp_path / "ragfs_python.cpython-312-darwin.so"
    abi3_artifact = tmp_path / "ragfs_python.abi3.so"
    cpython_artifact.write_bytes(b"old")
    abi3_artifact.write_bytes(b"new")

    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)
    assert pyagfs._find_ragfs_so() == str(abi3_artifact)


def test_pyagfs_loader_ignores_cpython_specific_artifact(monkeypatch, tmp_path: Path):
    import openviking.pyagfs as pyagfs

    cpython_artifact = tmp_path / "ragfs_python.cpython-312-darwin.so"
    cpython_artifact.write_bytes(b"old")

    monkeypatch.setattr(pyagfs, "_LIB_DIR", tmp_path)

    assert pyagfs._find_ragfs_so() is None
