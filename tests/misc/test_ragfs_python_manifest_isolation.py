import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _array_items(text: str, key: str) -> set[str]:
    match = re.search(rf"{key}\s*=\s*\[(.*?)\]", text, flags=re.DOTALL)
    assert match is not None, f"{key} array not found"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"^\[{re.escape(name)}\]\s*$(.*?)(?=^\[|\Z)", text, flags=re.DOTALL | re.MULTILINE
    )
    assert match is not None, f"[{name}] section not found"
    return match.group(1)


def test_default_workspace_excludes_native_cache_providers():
    manifest = _read(ROOT / "Cargo.toml")
    workspace = _section(manifest, "workspace")
    members = _array_items(workspace, "members")
    excludes = _array_items(workspace, "exclude")

    assert "crates/ragfs-cache-redis" in members
    assert "crates/ragfs-cache-mooncake" not in members
    assert "crates/ragfs-cache-yuanrong" not in members
    assert "crates/ragfs-cache-yuanrong-sys" not in members

    assert "crates/ragfs-cache-mooncake" in excludes
    assert "crates/ragfs-cache-yuanrong" in excludes
    assert "crates/ragfs-cache-yuanrong-sys" in excludes
    assert "crates/ragfs-python-native" in excludes


def test_default_ragfs_python_manifest_depends_only_on_redis_provider():
    manifest = _read(ROOT / "crates/ragfs-python/Cargo.toml")
    features = _section(manifest, "features")
    dependencies = _section(manifest, "dependencies")

    assert "cache-redis" in features
    assert "mooncake-native" not in features
    assert "yuanrong-native" not in features

    assert "ragfs-cache-redis" in dependencies
    assert "ragfs-cache-mooncake" not in dependencies
    assert "ragfs-cache-yuanrong" not in dependencies


def test_native_ragfs_python_manifest_is_explicit_provider_entrypoint():
    manifest = _read(ROOT / "crates/ragfs-python-native/Cargo.toml")
    lib = _section(manifest, "lib")
    features = _section(manifest, "features")
    dependencies = _section(manifest, "dependencies")

    assert 'path = "../ragfs-python/src/lib.rs"' in lib
    assert "cache-redis" in features
    assert "mooncake-native" in features
    assert "yuanrong-native" in features

    assert "ragfs-cache-redis" in dependencies
    assert "ragfs-cache-mooncake" in dependencies
    assert "ragfs-cache-yuanrong" in dependencies
