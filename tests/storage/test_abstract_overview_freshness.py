# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest
import yaml

from openviking.core.context import ContextLevel
from openviking.storage.abstract_overview import (
    body_for_preview,
    freshness_metadata,
    parse_abstract_overview,
    plan_abstract_overview_refresh,
    read_abstract_overview_pending_snapshot,
    render_abstract_overview,
    write_abstract_overview,
)
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction


class _FakeFS:
    def __init__(self, files):
        self.files = dict(files)
        self._async_agfs = self
        self._lock = asyncio.Lock()
        self.lock_timeouts = []

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise KeyError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
        self.lock_timeouts.append(timeout_secs)
        await self._lock.acquire()
        return {"paths": paths}

    async def pathlock_release(self, lease):
        self._lock.release()
        return None


def _files(dir_uri, *, total=161, pending=3):
    metadata = {"freshness": freshness_metadata(total, min(total, 32), pending)}
    return {
        f"{dir_uri}/.overview.md": render_abstract_overview(
            ContextLevel.OVERVIEW, dir_uri, "overview", metadata
        ),
        f"{dir_uri}/.abstract.md": render_abstract_overview(
            ContextLevel.ABSTRACT, dir_uri, "abstract", metadata
        ),
    }


@pytest.mark.asyncio
async def test_pending_increment_and_threshold_decision_share_one_snapshot():
    dir_uri = "viking://resources/wide"
    fs = _FakeFS(_files(dir_uri, pending=15))

    first = await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri=dir_uri,
        changed_entries=1,
        ctx=None,
        overview_sample_limit=32,
        refresh_ratio=0.10,
        lock_timeout_secs=1.0,
    )
    second = await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri=dir_uri,
        changed_entries=1,
        ctx=None,
        overview_sample_limit=32,
        refresh_ratio=0.10,
    )

    assert first.action is FreshnessAction.MARK_PENDING
    assert second.action is FreshnessAction.REFRESH_NOW
    assert fs.lock_timeouts == [1.0, 0.0]
    for raw in fs.files.values():
        assert parse_abstract_overview(raw).metadata["freshness"]["pending_child_changes"] == 17


@pytest.mark.asyncio
async def test_write_result_ignores_metadata_only_changes_and_preserves_new_pending():
    dir_uri = "viking://resources/wide"
    fs = _FakeFS(_files(dir_uri, pending=5))

    result = await write_abstract_overview(
        viking_fs=fs,
        dir_uri=dir_uri,
        overview="overview",
        abstract="abstract",
        ctx=None,
        is_stale=lambda: False,
        metadata={"freshness": freshness_metadata(160, 32)},
        consume_pending=3,
    )

    assert result.wrote
    assert not result.overview_body_changed
    assert not result.abstract_body_changed
    for raw in fs.files.values():
        freshness = parse_abstract_overview(raw).metadata["freshness"]
        assert freshness == freshness_metadata(160, 32, pending=2)


@pytest.mark.asyncio
async def test_concurrent_pending_marks_do_not_overwrite_each_other():
    dir_uri = "viking://resources/wide"
    fs = _FakeFS(_files(dir_uri, pending=3))

    await asyncio.gather(
        *(
            plan_abstract_overview_refresh(
                viking_fs=fs,
                dir_uri=dir_uri,
                changed_entries=1,
                ctx=None,
                overview_sample_limit=32,
                refresh_ratio=1.0,
            )
            for _ in range(10)
        )
    )

    for raw in fs.files.values():
        assert parse_abstract_overview(raw).metadata["freshness"]["pending_child_changes"] == 13


@pytest.mark.asyncio
async def test_partial_sidecar_baseline_refreshes_immediately():
    dir_uri = "viking://resources/wide"
    files = _files(dir_uri, pending=3)
    files[f"{dir_uri}/.abstract.md"] = "---\n"
    fs = _FakeFS(files)

    decision = await plan_abstract_overview_refresh(
        viking_fs=fs,
        dir_uri=dir_uri,
        changed_entries=1,
        ctx=None,
        overview_sample_limit=32,
        refresh_ratio=1.0,
    )

    assert decision.action is FreshnessAction.REFRESH_NOW
    assert decision.pending_after == 1


@pytest.mark.asyncio
async def test_pending_snapshot_of_missing_directory_does_not_lock():
    """Locking sidecars of a deleted directory would recreate it; return 0 instead."""
    fs = _FakeFS({})
    assert (
        await read_abstract_overview_pending_snapshot(
            viking_fs=fs, dir_uri="viking://resources/gone", ctx=None
        )
        == 0
    )
    assert fs.lock_timeouts == []


@pytest.mark.asyncio
async def test_pending_snapshot_reads_existing_sidecars_under_lock():
    dir_uri = "viking://resources/present"
    fs = _FakeFS(_files(dir_uri, pending=3))
    assert (
        await read_abstract_overview_pending_snapshot(viking_fs=fs, dir_uri=dir_uri, ctx=None) == 3
    )
    assert fs.lock_timeouts == [0.0]


_PRIVACY_REFERENCE = "{{ov_privacy:alice:viking://~/privacy/skill/skill-a#/values/host}}"


def test_legacy_preview_with_leading_reference_is_quoted_and_parses():
    raw = (
        f"name: securing-helm-chart-{_PRIVACY_REFERENCE}ments\n"
        f"description: Secures Helm charts\n"
        "tags:\n"
        "- helm\n"
        f"- {_PRIVACY_REFERENCE}ment\n"
    )

    # The raw text is flow-mapping syntax to YAML and cannot be parsed.
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(raw)

    document = parse_abstract_overview(raw)
    assert document.legacy is True

    parsed = yaml.safe_load(body_for_preview(raw))
    assert parsed["tags"][-1] == f"{_PRIVACY_REFERENCE}ment"
    assert parsed["name"] == f"securing-helm-chart-{_PRIVACY_REFERENCE}ments"


def test_legacy_preview_with_reference_mid_string_is_untouched():
    raw = (
        "name: backblaze\n"
        f"description: 'uploads to {_PRIVACY_REFERENCE} every night'\n"
        "tags:\n"
        "- backup\n"
    )

    assert body_for_preview(raw) == raw
    assert yaml.safe_load(body_for_preview(raw))["name"] == "backblaze"


def test_legacy_preview_without_references_is_untouched():
    raw = "name: plain\ntags:\n- a\n- b\n"
    assert body_for_preview(raw) == raw
