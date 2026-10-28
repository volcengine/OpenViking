from pathlib import Path

from scripts.build_support.system_assets_manifest import build_system_assets_manifest


def _write_managed_assets(assets_dir: Path) -> None:
    compile_source = assets_dir / "compile/example-skills/demo/SKILL.md"
    memory_source = assets_dir / "memory/memory_templates/builtin/profile.yaml"
    compile_source.parent.mkdir(parents=True)
    memory_source.parent.mkdir(parents=True)
    compile_source.write_bytes(b"skill")
    memory_source.write_bytes(b"profile")


def test_system_asset_manifest_is_stable_and_tracks_managed_assets(tmp_path):
    _write_managed_assets(tmp_path)

    first = build_system_assets_manifest(tmp_path)
    assert build_system_assets_manifest(tmp_path) == first

    (tmp_path / "manifest.json").write_text("stale", encoding="utf-8")
    assert build_system_assets_manifest(tmp_path) == first

    compile_source = tmp_path / "compile/example-skills/demo/SKILL.md"
    compile_source.write_bytes(b"updated skill")
    content_changed = build_system_assets_manifest(tmp_path)
    assert content_changed != first

    compile_source.rename(compile_source.with_name("RENAMED.md"))
    assert build_system_assets_manifest(tmp_path) != content_changed


def test_checked_in_system_asset_manifest_is_current():
    assets_dir = Path(__file__).parents[2] / "openviking" / "system_assets"

    assert (assets_dir / "manifest.json").read_bytes() == build_system_assets_manifest(assets_dir)
