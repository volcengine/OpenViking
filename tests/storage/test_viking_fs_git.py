# tests/storage/test_viking_fs_git.py
import pytest

pytestmark = pytest.mark.asyncio


@pytest.mark.skip(reason="needs git-enabled VikingFS fixture")
async def test_show_blob_raw_returns_envelope(viking_fs_with_two_commits):
    """show_blob_raw must return the full {oid, size, bytes} dict, not strip it."""
    vfs, _account, commit_oid, sample_path, sample_bytes = viking_fs_with_two_commits

    raw = await vfs.show_blob_raw(commit_oid, path=sample_path)

    assert isinstance(raw, dict)
    assert raw["bytes"] == sample_bytes
    assert raw["size"] == len(sample_bytes)
    assert isinstance(raw["oid"], str) and len(raw["oid"]) == 40
