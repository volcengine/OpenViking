# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.semantic_sidecar import (
    SemanticSidecarFormatError,
    body_for_embedding,
    body_for_preview,
    deterministic_sample,
    freshness_metadata,
    mark_semantic_sidecars_pending,
    parse_semantic_sidecar,
    prepare_semantic_sidecar_write,
    render_semantic_sidecar,
    write_semantic_sidecars,
)


def _metadata():
    return {
        "source": {"kind": "http", "uri": "https://example.com/demo.pdf"},
        "generated_by": {
            "component": "SemanticProcessor",
            "trigger": "resource_ingest",
        },
        "freshness": freshness_metadata(161, 32),
    }


def test_render_and_parse_semantic_sidecar_are_deterministic():
    raw = render_semantic_sidecar(
        ContextLevel.ABSTRACT,
        "viking://resources/images_2",
        "Visible summary.",
        _metadata(),
    )

    document = parse_semantic_sidecar(raw)

    assert not document.legacy
    assert document.body == "Visible summary.\n"
    assert document.metadata == {
        "directory": "viking://resources/images_2/",
        **_metadata(),
    }
    assert (
        render_semantic_sidecar(
            ContextLevel.ABSTRACT,
            "viking://resources/images_2",
            document.body,
            document.metadata,
        )
        == raw
    )


def test_legacy_sidecar_is_body_only():
    raw = "# Legacy\n\nNo frontmatter."
    document = parse_semantic_sidecar(raw)
    assert document.legacy
    assert document.metadata == {}
    assert body_for_preview(raw) == raw
    assert body_for_embedding(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "---\ndirectory: [broken\n---\nbody",
        "---\n- not\n- an\n- object\n---\nbody",
        "---\ndirectory: viking://resources/demo/\nbody",
        "---\ngenerated_by:\n  component: test\n  trigger: test\n---\nbody",
        "---\ndirectory: /local/demo\n---\nbody",
    ],
)
def test_malformed_generated_sidecar_fails_loudly(raw):
    with pytest.raises(SemanticSidecarFormatError):
        body_for_embedding(raw)


def test_embedding_uses_only_directory_metadata():
    raw = render_semantic_sidecar(
        ContextLevel.OVERVIEW,
        "viking://resources/images_2",
        "# Images\n\nVisible overview.",
        _metadata(),
    )
    embedded = body_for_embedding(raw)
    assert "directory: viking://resources/images_2/" in embedded
    assert "# Images" in embedded
    assert "source:" not in embedded
    assert "generated_by:" not in embedded
    assert "freshness:" not in embedded


def test_unknown_metadata_fields_are_silently_discarded():
    raw = """---
directory: viking://resources/demo/
secret: must-not-survive
future_feature:
  enabled: true
---

Visible body.
"""

    document = parse_semantic_sidecar(raw)
    embedded = body_for_embedding(raw)

    assert document.metadata == {"directory": "viking://resources/demo/"}
    assert document.body == "Visible body.\n"
    assert "directory: viking://resources/demo/" in embedded
    assert "secret" not in embedded
    assert "future_feature" not in embedded


def test_render_silently_discards_unknown_metadata_fields():
    raw = render_semantic_sidecar(
        ContextLevel.ABSTRACT,
        "viking://resources/demo",
        "Visible body.",
        {"secret": "must-not-survive", "future_feature": {"enabled": True}},
    )

    assert parse_semantic_sidecar(raw).metadata == {"directory": "viking://resources/demo/"}
    assert "secret" not in raw
    assert "future_feature" not in raw


def test_unknown_nested_metadata_fields_are_silently_discarded():
    raw = """---
directory: viking://resources/demo/
source:
  kind: http
  uri: https://example.com/demo.pdf
  checksum: ignored
generated_by:
  component: SemanticProcessor
  trigger: ingest
  version: ignored
freshness:
  total_entries: 1
  sampled_entries: 1
  unsampled_entries: 0
  pending_child_changes: 0
  generation: ignored
---

Visible body.
"""

    document = parse_semantic_sidecar(raw)

    assert document.metadata["source"] == {
        "kind": "http",
        "uri": "https://example.com/demo.pdf",
    }
    assert document.metadata["generated_by"] == {
        "component": "SemanticProcessor",
        "trigger": "ingest",
    }
    assert document.metadata["freshness"] == {
        "total_entries": 1,
        "sampled_entries": 1,
        "unsampled_entries": 0,
        "pending_child_changes": 0,
    }


def test_unknown_metadata_does_not_affect_write_protection_comparison():
    current = """---
directory: viking://resources/demo/
secret: old-version-only
---

Old body.
"""
    requested = """---
directory: viking://resources/demo/
secret: different-but-ignored
---

New body.
"""

    result = prepare_semantic_sidecar_write(
        "viking://resources/demo/.abstract.md", current, requested
    )

    assert parse_semantic_sidecar(result).body == "New body.\n"
    assert "secret" not in result


def test_ordinary_markdown_frontmatter_is_untouched_without_explicit_parsing():
    raw = "---\ntitle: User document\n---\n\nBody"
    assert raw == "---\ntitle: User document\n---\n\nBody"
    with pytest.raises(SemanticSidecarFormatError):
        parse_semantic_sidecar(raw)


def test_deterministic_sample_preserves_order_and_spans_first_and_last():
    items = list(range(100))
    first = deterministic_sample(items, 5)
    second = deterministic_sample(items, 5)
    assert first == second == [0, 24, 49, 74, 99]


def test_public_sidecar_write_accepts_unchanged_metadata_and_replaces_body():
    uri = "viking://resources/demo/.overview.md"
    current = render_semantic_sidecar(
        ContextLevel.OVERVIEW, "viking://resources/demo", "Old body.", _metadata()
    )
    requested = render_semantic_sidecar(
        ContextLevel.OVERVIEW,
        "viking://resources/demo",
        "New body.",
        parse_semantic_sidecar(current).metadata,
    )

    result = parse_semantic_sidecar(prepare_semantic_sidecar_write(uri, current, requested))

    assert result.body == "New body.\n"
    assert result.metadata == parse_semantic_sidecar(current).metadata


def test_public_sidecar_write_inherits_metadata_for_body_only_request():
    uri = "viking://resources/demo/.abstract.md"
    current = render_semantic_sidecar(
        ContextLevel.ABSTRACT, "viking://resources/demo", "Old body.", _metadata()
    )

    result = parse_semantic_sidecar(
        prepare_semantic_sidecar_write(uri, current, "Body only replacement.")
    )

    assert result.body == "Body only replacement.\n"
    assert result.metadata == parse_semantic_sidecar(current).metadata


def test_public_sidecar_write_does_not_repair_stored_directory_metadata():
    current = render_semantic_sidecar(
        ContextLevel.ABSTRACT,
        "viking://resources/historical-location",
        "Old body.",
        _metadata(),
    )

    result = parse_semantic_sidecar(
        prepare_semantic_sidecar_write(
            "viking://resources/current-location/.abstract.md",
            current,
            "New body.",
        )
    )

    assert result.metadata == parse_semantic_sidecar(current).metadata


def test_public_sidecar_append_preserves_metadata_and_appends_only_body():
    uri = "viking://resources/demo/.overview.md"
    current = render_semantic_sidecar(
        ContextLevel.OVERVIEW, "viking://resources/demo", "First.", _metadata()
    )
    requested = render_semantic_sidecar(
        ContextLevel.OVERVIEW,
        "viking://resources/demo",
        " Second.",
        parse_semantic_sidecar(current).metadata,
    )

    result = parse_semantic_sidecar(
        prepare_semantic_sidecar_write(uri, current, requested, mode="append")
    )

    assert result.body == "First.\nSecond.\n"
    assert result.metadata == parse_semantic_sidecar(current).metadata


def test_public_sidecar_write_rejects_metadata_changes():
    uri = "viking://resources/demo/.abstract.md"
    current = render_semantic_sidecar(
        ContextLevel.ABSTRACT, "viking://resources/demo", "Old body.", _metadata()
    )
    changed = {
        **parse_semantic_sidecar(current).metadata,
        "source": {"kind": "http", "uri": "https://attacker.invalid"},
    }
    requested = render_semantic_sidecar(
        ContextLevel.ABSTRACT, "viking://resources/demo", "New body.", changed
    )

    with pytest.raises(SemanticSidecarFormatError, match="cannot modify protected.*metadata"):
        prepare_semantic_sidecar_write(uri, current, requested)


class _FakeFS:
    def __init__(self):
        self.files = {}
        self.writes = []
        self._async_agfs = self

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_exact_batch(self, paths):
        return {"paths": paths}

    async def pathlock_release(self, lease):
        return None

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content
        self.writes.append((uri, content))


@pytest.mark.asyncio
async def test_stable_write_does_not_rewrite_and_preserves_source():
    fs = _FakeFS()
    kwargs = {
        "viking_fs": fs,
        "dir_uri": "viking://resources/demo",
        "overview": "# Demo\n\nOverview.",
        "abstract": "Overview.",
        "ctx": None,
        "is_stale": lambda: False,
        "metadata": _metadata(),
    }
    assert await write_semantic_sidecars(**kwargs)
    assert len(fs.writes) == 2
    fs.writes.clear()
    assert await write_semantic_sidecars(**kwargs)
    assert fs.writes == []

    refreshed = dict(kwargs)
    refreshed["metadata"] = {
        "generated_by": {
            "component": "SemanticProcessor",
            "trigger": "manual_refresh",
        },
        "freshness": freshness_metadata(161, 32),
    }
    assert await write_semantic_sidecars(**refreshed)
    assert all(
        parse_semantic_sidecar(content).metadata["source"] == _metadata()["source"]
        for _, content in fs.writes
    )


@pytest.mark.asyncio
async def test_pending_changes_update_metadata_without_changing_body():
    fs = _FakeFS()
    await write_semantic_sidecars(
        viking_fs=fs,
        dir_uri="viking://resources/demo",
        overview="# Demo\n\nOverview.",
        abstract="Overview.",
        ctx=None,
        is_stale=lambda: False,
        metadata=_metadata(),
    )
    before = {uri: parse_semantic_sidecar(content) for uri, content in fs.files.items()}
    fs.writes.clear()

    await mark_semantic_sidecars_pending(
        viking_fs=fs,
        dir_uri="viking://resources/demo",
        changed_entries=2,
        ctx=None,
    )

    assert len(fs.writes) == 2
    for uri, content in fs.files.items():
        after = parse_semantic_sidecar(content)
        assert after.body == before[uri].body
        assert after.metadata["freshness"]["pending_child_changes"] == 2


@pytest.mark.asyncio
async def test_completed_refresh_resets_pending_changes():
    fs = _FakeFS()
    await write_semantic_sidecars(
        viking_fs=fs,
        dir_uri="viking://resources/demo",
        overview="# Demo\n\nOverview.",
        abstract="Overview.",
        ctx=None,
        is_stale=lambda: False,
        metadata=_metadata(),
    )
    await mark_semantic_sidecars_pending(
        viking_fs=fs,
        dir_uri="viking://resources/demo",
        changed_entries=3,
        ctx=None,
    )
    fs.writes.clear()

    await write_semantic_sidecars(
        viking_fs=fs,
        dir_uri="viking://resources/demo",
        overview="# Demo\n\nRefreshed.",
        abstract="Refreshed.",
        ctx=None,
        is_stale=lambda: False,
        metadata={
            "generated_by": {
                "component": "SemanticProcessor",
                "trigger": "content_write",
            },
            "freshness": freshness_metadata(4, 4),
        },
    )

    assert len(fs.writes) == 2
    for content in fs.files.values():
        document = parse_semantic_sidecar(content)
        assert document.metadata["source"] == _metadata()["source"]
        assert document.metadata["freshness"] == {
            "total_entries": 4,
            "sampled_entries": 4,
            "unsampled_entries": 0,
            "pending_child_changes": 0,
        }


@pytest.mark.asyncio
async def test_pending_update_treats_mapping_key_error_as_missing_sidecar():
    class MappingFS:
        async def read_file(self, uri, ctx=None):
            del ctx
            raise KeyError(uri)

    await mark_semantic_sidecars_pending(
        viking_fs=MappingFS(),
        dir_uri="viking://resources/new",
        changed_entries=1,
        ctx=None,
    )
