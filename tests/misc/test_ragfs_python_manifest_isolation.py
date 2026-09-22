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


def test_workspace_uses_ragfs_runtime_and_one_python_binding_crate():
    manifest = _read(ROOT / "Cargo.toml")
    workspace = _section(manifest, "workspace")
    members = _array_items(workspace, "members")

    assert "crates/ragfs" in members
    assert "crates/ragfs-python" in members
    assert "crates/ragfs-cache-redis" not in members
    assert "crates/ragfs-python-native" not in members
    assert "crates/ragfs-cache-mooncake" not in members
    assert "crates/ragfs-cache-yuanrong" not in members
    assert "crates/ragfs-cache-yuanrong-sys" not in members


def test_ragfs_python_uses_the_runtime_embedded_in_ragfs():
    manifest = _read(ROOT / "crates/ragfs-python/Cargo.toml")
    features = _section(manifest, "features")
    dependencies = _section(manifest, "dependencies")

    assert "cache-redis" not in features
    assert "mooncake-native" not in features
    assert "yuanrong-native" not in features

    assert 'ragfs = { path = "../ragfs", features = ["cache"] }' in dependencies
    assert "ragfs-cache-redis" not in dependencies
    assert "ragfs-cache-mooncake" not in dependencies
    assert "ragfs-cache-yuanrong" not in dependencies


def test_legacy_provider_and_binding_manifests_are_removed():
    assert not (ROOT / "crates/ragfs-cache-redis/Cargo.toml").exists()
    assert not (ROOT / "crates/ragfs-cache-mooncake/Cargo.toml").exists()
    assert not (ROOT / "crates/ragfs-cache-yuanrong/Cargo.toml").exists()
    assert not (ROOT / "crates/ragfs-cache-yuanrong-sys/Cargo.toml").exists()
    assert not (ROOT / "crates/ragfs-python-native/Cargo.toml").exists()


def test_source_distribution_contains_only_active_ragfs_crates():
    manifest = _read(ROOT / "MANIFEST.in")

    assert "graft crates/ragfs\n" in manifest
    assert "graft crates/ragfs-python\n" in manifest
    assert "graft crates/ragfs-cache-redis" not in manifest
    assert "graft crates/ragfs-cache-mooncake" not in manifest
    assert "graft crates/ragfs-cache-yuanrong" not in manifest
    assert "graft crates/ragfs-cache-yuanrong-sys" not in manifest
    assert "graft crates/ragfs-python-native" not in manifest
