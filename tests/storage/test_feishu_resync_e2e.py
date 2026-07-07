# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""#3029 end-to-end resync flows for the Feishu safe-sync fix.

Where this differs from ``test_feishu_resync_manifest.py`` (the 16-row table):
those rows drive one isolated decision each and *hand-seed* the manifest with
``seed_manifest``. Here we chain the issue's actual repro as multi-step flows and
NEVER hand-build a manifest — every manifest is produced by the REAL
``SemanticProcessor._sync_topdown_recursive`` -> ``flush_manifest`` ->
``write_manifest_atomic`` on one sync and read back by the REAL ``read_manifest``
on the next. So each step exercises the true manifest read/write/divergence
round-trip and the true ``sync_dir`` tree walk, and we assert on real observable
state (files present/absent, sidecar written, manifest JSON, DiffResult.warnings).

Fidelity — HONEST statement of what is real vs stubbed:
  * REAL: the whole ``_sync_topdown_recursive`` / ``sync_dir`` tree diff, the
    delete-policy selection (GUARDED / MERGE_ONLY / LEGACY_MIRROR), and the
    ``sync_manifest`` module (read_manifest / write_manifest_atomic / divergent /
    manifest_entry / hashing) round-tripped across syncs.
  * STUBBED: the viking_fs itself is the in-memory ``FakeFS`` from the sibling
    test (no Rust RAGFS binding / no on-disk encryption / no real locks), and
    ``_rewrite_target_image_uris`` is a no-op. A real local binding-backed
    VikingFS (``create_agfs_client(RagfsBindingConfig(...backend="local"))``)
    is what a true disk E2E would use, but the ``ragfs_python`` native library
    is not built in this environment ("Rust binding not available"), so the
    highest-fidelity feasible substrate is this in-memory VFS driving the real
    sync/manifest code paths.
"""

from __future__ import annotations

import pytest

# Reuse the sibling harness verbatim: FakeFS (in-memory VFS), run_sync (wires
# get_viking_fs + a real SemanticProcessor and calls the real recursive sync),
# sha (shared hash) and read_manifest_raw (reads what the real code wrote).
# pytest inserts tests/storage on sys.path (no __init__.py there), so this
# sibling import resolves; importing the module only defines helpers — its own
# test_* functions are collected from their own file, not re-run here.
from test_feishu_resync_manifest import (  # noqa: E402
    TARGET,
    TEMP,
    FakeFS,
    read_manifest_raw,
    run_sync,
    sha,
)

from openviking.storage.queuefs import semantic_processor as sp
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.sync_manifest import SYNC_MANIFEST_FILENAME
from openviking.storage.transaction import NO_LOCK

MANIFEST_URI = f"{TARGET}/{SYNC_MANIFEST_FILENAME}"


async def run_sync_with_rewrite(fs, monkeypatch, ownership_tracked, rewrite):
    """Like the sibling run_sync, but the image-uri rewrite is a REAL mutating
    step (not a no-op) so we can exercise the hash-ordering path (#3029)."""
    monkeypatch.setattr(sp, "get_viking_fs", lambda: fs)
    proc = SemanticProcessor()
    proc._rewrite_target_image_uris = rewrite
    return await proc._sync_topdown_recursive(
        TEMP, TARGET, lock=NO_LOCK, ownership_tracked=ownership_tracked
    )


# ---------------------------------------------------------------------------
# Regression for the pre-rewrite-hashing bug: _rewrite_target_image_uris mutates
# the generated file in place AFTER sync_dir runs. The manifest must record the
# POST-rewrite bytes, else the NEXT resync sees the rewritten content as
# "user-modified" and writes a spurious sidecar. The other tests miss this
# because they stub the rewrite to a no-op.
# ---------------------------------------------------------------------------
async def test_resync_after_image_rewrite_not_flagged_as_user_edit(monkeypatch):
    fs = FakeFS()

    raw = "# Doc\n\n![x]([[IMG]])\n"
    rewritten = "# Doc\n\n![x](viking://resources/img.png)\n"

    async def rewrite(root_uri, target_uri, ctx=None, lock=None):
        # Simulate image-uri rewrite: mutate the generated file on disk.
        key = f"{TARGET}/content.md"
        if key in fs.files and "[[IMG]]" in fs.files[key]:
            fs.files[key] = fs.files[key].replace("[[IMG]]", "viking://resources/img.png")

    # Target PRE-EXISTS (+ a user file so the dir exists) so the manifest is
    # written through the pre-existing-target path (MERGE_ONLY first run) — the
    # path where the pre-rewrite-hashing bug lived. A fresh-target first sync
    # would not exercise it.
    fs.add_file(f"{TARGET}/content.md", raw)
    fs.add_file(f"{TARGET}/manual-note.md", "user note")
    fs.add_file(f"{TEMP}/content.md", raw)
    await run_sync_with_rewrite(fs, monkeypatch, ownership_tracked=True, rewrite=rewrite)

    assert fs.files[f"{TARGET}/content.md"] == rewritten  # rewrite happened
    m1 = read_manifest_raw(fs)
    entry = next(f for f in m1["files"] if f["relpath"] == "content.md")
    assert entry["sha256"] == sha(rewritten)  # manifest records POST-rewrite bytes

    # Resync: source is byte-identical to the first parse (unchanged upstream).
    fs.add_file(f"{TEMP}/content.md", raw)
    diff = await run_sync_with_rewrite(fs, monkeypatch, ownership_tracked=True, rewrite=rewrite)

    # The file was rewritten by us, not user-edited -> no spurious sidecar/warning.
    assert not any(k.startswith(f"{TARGET}/content.remote-") for k in fs.files)
    assert diff.warnings == []
    assert fs.files[f"{TARGET}/content.md"] == rewritten


def rels_of(manifest: dict) -> set[str]:
    return {f["relpath"] for f in manifest["files"]}


# ---------------------------------------------------------------------------
# Scenarios 1 + 2 chained: first sync (merge-only) then a resync where the user
# has added more files. This is the issue's core repro end to end.
# ---------------------------------------------------------------------------
async def test_first_sync_then_resync_preserves_user_files(monkeypatch):
    fs = FakeFS()
    # Target already holds user-owned content; the Feishu parse (TEMP) yields
    # content.md. No manifest yet -> the real code runs MERGE_ONLY.
    fs.add_file(f"{TARGET}/manual-note.md", "my hand-written note")
    fs.add_file(f"{TARGET}/refs/paper.pdf", "%PDF-1.7 fake bytes")
    fs.add_file(f"{TEMP}/content.md", "# Parsed Doc v1\n")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    # Scenario 1 assertions: generated file placed, user files PRESERVED,
    # a valid manifest written that lists ONLY the generated file.
    assert fs.files[f"{TARGET}/content.md"] == "# Parsed Doc v1\n"
    assert fs.files[f"{TARGET}/manual-note.md"] == "my hand-written note"
    assert fs.files[f"{TARGET}/refs/paper.pdf"] == "%PDF-1.7 fake bytes"
    m1 = read_manifest_raw(fs)
    assert m1 is not None and m1["schema_version"] == 1
    assert rels_of(m1) == {"content.md"}  # user files never recorded as ours
    assert m1["dirs"] == []  # the user's refs/ dir is not ours either

    # Scenario 2: user adds todo.md after the first sync; the next parse still
    # yields content.md but with updated bytes. Manifest now exists -> GUARDED.
    fs.add_file(f"{TARGET}/todo.md", "- [ ] review this doc")
    fs.add_file(f"{TEMP}/content.md", "# Parsed Doc v2 updated\n")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/content.md"] == "# Parsed Doc v2 updated\n"  # updated
    assert fs.files[f"{TARGET}/manual-note.md"] == "my hand-written note"  # preserved
    assert fs.files[f"{TARGET}/refs/paper.pdf"] == "%PDF-1.7 fake bytes"  # preserved
    assert fs.files[f"{TARGET}/todo.md"] == "- [ ] review this doc"  # preserved
    m2 = read_manifest_raw(fs)
    entry = next(f for f in m2["files"] if f["relpath"] == "content.md")
    assert entry["sha256"] == sha("# Parsed Doc v2 updated\n")  # manifest refreshed


# ---------------------------------------------------------------------------
# Scenario 3: a generated file is genuinely dropped upstream on resync. The
# manifest that tracks it is produced by a real prior sync, not hand-built.
# ---------------------------------------------------------------------------
async def test_resync_deletes_only_our_stale_file(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/manual-note.md", "user note")  # user file, throughout
    # First parse yields two generated files -> both recorded as ours by the
    # real sync (this is how the manifest comes to track content.md + old.md).
    fs.add_file(f"{TEMP}/content.md", "doc body")
    fs.add_file(f"{TEMP}/old.md", "legacy section")

    await run_sync(fs, monkeypatch, ownership_tracked=True)
    m = read_manifest_raw(fs)
    assert rels_of(m) == {"content.md", "old.md"}  # both are ours

    # Resync: the new parse omits old.md; content.md is byte-identical (so both
    # tracked files are unchanged since the last sync).
    fs.add_file(f"{TEMP}/content.md", "doc body")

    await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert f"{TARGET}/old.md" not in fs.files  # our stale file, hash matched -> deleted
    assert fs.files[f"{TARGET}/content.md"] == "doc body"  # still present
    assert fs.files[f"{TARGET}/manual-note.md"] == "user note"  # user file preserved
    m2 = read_manifest_raw(fs)
    assert rels_of(m2) == {"content.md"}  # manifest no longer tracks old.md


# ---------------------------------------------------------------------------
# Scenario 4: user edits a generated file locally, then a resync brings fresh
# remote bytes. Real divergence check -> preserve + sidecar + warning.
# ---------------------------------------------------------------------------
async def test_resync_user_edited_generated_file_gets_sidecar(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/manual-note.md", "user note")
    fs.add_file(f"{TEMP}/content.md", "generated original")

    await run_sync(fs, monkeypatch, ownership_tracked=True)  # manifest tracks content.md
    assert fs.files[f"{TARGET}/content.md"] == "generated original"

    # User edits our generated file in place -> its hash now diverges from the
    # manifest. The next parse yields different content.md bytes.
    fs.files[f"{TARGET}/content.md"] = "USER HAND EDIT keep this"
    fs.add_file(f"{TEMP}/content.md", "fresh remote v2")

    diff = await run_sync(fs, monkeypatch, ownership_tracked=True)

    assert fs.files[f"{TARGET}/content.md"] == "USER HAND EDIT keep this"  # NOT overwritten
    short = sha("fresh remote v2")[:8]
    sidecar = f"{TARGET}/content.remote-{short}.md"
    assert fs.files.get(sidecar) == "fresh remote v2"  # remote saved alongside
    assert any("content.md" in w for w in diff.warnings)  # warning recorded


# ---------------------------------------------------------------------------
# Scenario 5: LEGACY regression — a non-ownership-tracked source with no
# manifest still mirrors, i.e. a file absent upstream IS deleted (unchanged).
# ---------------------------------------------------------------------------
async def test_legacy_mirror_still_deletes_dropped_file(monkeypatch):
    fs = FakeFS()
    fs.add_file(f"{TARGET}/content.md", "a")
    fs.add_file(f"{TARGET}/dropped.md", "b")  # upstream will no longer have it
    fs.add_file(f"{TEMP}/content.md", "a")  # source omits dropped.md

    await run_sync(fs, monkeypatch, ownership_tracked=False)

    assert f"{TARGET}/dropped.md" not in fs.files  # legacy mirror still prunes
    assert fs.files[f"{TARGET}/content.md"] == "a"  # unchanged file kept
    assert MANIFEST_URI not in fs.files  # legacy never writes a manifest


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
