# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""#3029 cross-layer regression: the `ownership_tracked` flag must thread from
the parse `source_format` all the way to the enqueued ``SemanticMsg``.

PR #3055 stops Feishu resync from deleting user-managed files. After the rebase
the flag flows through a refactored path:

    process_resource(defer_post_processing=True)   # discriminator -> prepared dict
        -> result["_post_process"]["ownership_tracked"]
    finish_prepared_resource(prepared)             # prepared -> summarize kwarg
        -> Summarizer.summarize(..., ownership_tracked=...)
            -> SemanticMsg(ownership_tracked=...)  # the enqueued message

A single-doc Feishu source (source_format "feishu" or "feishu_<doctype>") must
carry ownership_tracked=True; every legacy-mirror source (git "repository",
"directory", "pdf", empty, None) must carry False. This proves it at each hop
by driving the REAL production code (no logic duplication of the discriminator
expression) and asserting the observed value — not by re-deriving it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.queuefs import SemanticMsg
from openviking.utils import summarizer as summarizer_mod
from openviking.utils.resource_processor import ResourceProcessor
from openviking.utils.summarizer import Summarizer

# source_format -> expected ownership_tracked. Feishu (bare + doctype-suffixed)
# is tracked; every legacy-mirror source is not. None exercises the
# `parse_result.source_format or ""` guard in the discriminator.
CASES = [
    ("feishu", True),
    ("feishu_docx", True),
    ("feishu_sheet", True),
    ("repository", False),
    ("directory", False),
    ("pdf", False),
    ("", False),
    (None, False),
]


# --------------------------------------------------------------------------- #
# Minimal in-memory harness (mirrors tests/misc/test_resource_processor_mv.py) #
# --------------------------------------------------------------------------- #
class _DummyVikingDB:
    def get_embedder(self):
        return None


class _Measure:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _DummyTelemetry:
    telemetry_id = ""

    def set(self, *a, **k):
        return None

    def set_error(self, *a, **k):
        return None

    def measure(self, *a, **k):
        return _Measure()


class _CtxMgr:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeLockManager:
    """Grants every lock; process_resource only needs a live handle here."""

    def __init__(self):
        from openviking.storage.transaction.lock_handle import LockHandle

        self._cls = LockHandle
        self._handles = {}

    def create_handle(self):
        h = self._cls()
        self._handles[h.id] = h
        return h

    async def acquire_exact_path_batch(self, handle, paths, timeout=None):
        for p in paths:
            handle.add_lock(f"exact:{p}")
        return True

    async def acquire_tree(self, handle, path, timeout=None):
        handle.add_lock(f"tree:{path}")
        return True

    async def release_selected(self, handle, lock_paths):
        for p in lock_paths:
            handle.remove_lock(p)

    async def release(self, handle):
        for p in list(handle.locks):
            handle.remove_lock(p)
        self._handles.pop(handle.id, None)

    def get_handle(self, handle_id):
        h = self._handles.get(handle_id)
        return h if h and h.locks else None


class _FakeVikingFS:
    def __init__(self):
        self.agfs = SimpleNamespace(write=MagicMock(return_value={"status": "ok"}))

    def bind_request_context(self, ctx):
        return _CtxMgr()

    async def exists(self, uri, ctx=None):
        return False

    async def delete_temp(self, temp_dir_path, ctx=None):
        return None

    async def persist_temp_tree(self, temp_uri, target_uri, ctx=None):
        return None

    async def glob(self, pattern, uri=None, ctx=None):
        return {"matches": []}

    def _uri_to_path(self, uri, ctx=None):
        return f"/mock/{uri.replace('viking://', '')}"


def _build_processor(monkeypatch, source_format):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr("openviking.parse.image_rewrite.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager", lambda: _FakeLockManager()
    )

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    rp._get_media_processor = MagicMock()
    rp._get_media_processor.return_value.process = AsyncMock(
        return_value=SimpleNamespace(
            temp_dir_path="viking://temp/tmpdir",
            source_path="x",
            source_format=source_format,
            meta={},
            warnings=[],
        )
    )
    rp.tree_builder.finalize_from_temp = AsyncMock(
        return_value=SimpleNamespace(
            root=SimpleNamespace(uri="viking://resources/root", temp_uri="viking://temp/root_tmp")
        )
    )
    return rp


# --------------------------------------------------------------------------- #
# Layer 1: discriminator + prepared payload (the defer_post_processing path).  #
# Drives the REAL process_resource with only the parse source_format varied.   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source_format, expected", CASES)
async def test_process_resource_prepared_carries_ownership_tracked(
    monkeypatch, source_format, expected
):
    rp = _build_processor(monkeypatch, source_format)

    result = await rp.process_resource(
        path="x", ctx=object(), defer_post_processing=True, build_index=False
    )

    assert result["status"] == "success"
    prepared = result["_post_process"]
    assert prepared["ownership_tracked"] == expected
    # is_code_repo is an independent legacy discriminator; a git repo is a
    # mirror (not ownership-tracked), which the pairing below confirms.
    assert prepared["is_code_repo"] == (source_format == "repository")


# --------------------------------------------------------------------------- #
# Layer 2: finish_prepared_resource -> Summarizer.summarize kwarg.             #
# The prepared dict's flag must reach the summarizer unchanged.               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source_format, expected", CASES)
async def test_finish_prepared_resource_passes_ownership_tracked(
    monkeypatch, source_format, expected
):
    monkeypatch.setattr(
        "openviking.utils.resource_processor.get_current_telemetry",
        lambda: _DummyTelemetry(),
    )
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    captured = {}
    rp._summarizer = SimpleNamespace(
        summarize=AsyncMock(side_effect=lambda *a, **k: captured.update(k) or {"status": "success"})
    )

    prepared = {
        "root_uri": "viking://resources/root",
        "temp_uri": "viking://resources/root",
        "temp_dir_path": None,
        "source_committed": True,
        "target_preexisting": False,
        "is_code_repo": source_format == "repository",
        "ownership_tracked": expected,
    }
    await rp.finish_prepared_resource(prepared, ctx=object(), summarize=True)

    assert captured["ownership_tracked"] == expected


# --------------------------------------------------------------------------- #
# Layer 3: Summarizer.summarize -> the enqueued SemanticMsg (the core ask).    #
# Runs the REAL summarize with a fake queue and asserts the constructed        #
# SemanticMsg carries the flag, incl. a to_dict/from_dict round-trip.          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source_format, expected", CASES)
async def test_summarize_enqueues_semantic_msg_with_ownership_tracked(
    monkeypatch, source_format, expected
):
    enqueued = []

    async def _enqueue(msg):
        enqueued.append(msg)
        return "enqueue-id"

    fake_queue = SimpleNamespace(enqueue=_enqueue)
    fake_qm = SimpleNamespace(
        SEMANTIC="Semantic",
        get_queue=lambda name, allow_create=False: fake_queue,
    )
    monkeypatch.setattr(summarizer_mod, "get_queue_manager", lambda: fake_qm)
    monkeypatch.setattr(summarizer_mod, "get_current_telemetry", lambda: _DummyTelemetry())

    ctx = SimpleNamespace(
        account_id="acct",
        user=SimpleNamespace(user_id="u"),
        role="root",
    )
    summ = Summarizer(vlm_processor=None)
    res = await summ.summarize(
        resource_uris=["viking://resources/root"],
        ctx=ctx,
        temp_uris=["viking://resources/root"],
        ownership_tracked=expected,
    )

    assert res["status"] == "success"
    assert len(enqueued) == 1
    msg = enqueued[0]
    assert isinstance(msg, SemanticMsg)
    assert msg.ownership_tracked == expected
    # Prove the field survives the queue's serialize/deserialize round-trip.
    assert SemanticMsg.from_dict(msg.to_dict()).ownership_tracked == expected


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
