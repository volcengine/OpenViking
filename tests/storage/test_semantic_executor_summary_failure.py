# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""A failed file summary must not drop the file's L2 (DETAIL) vector.

Real-world trigger: the configured VLM is unreachable (quota exhausted, network
blocked), so every `_generate_single_file_summary` call raises. The executor used
to treat that as "skip this file's DETAIL vectorization entirely", leaving the
file invisible to semantic search while the import reported success.
"""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _semantic_config(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )


class _FakeVikingFS:
    def __init__(self, file_contents):
        self._file_contents = dict(file_contents)
        self.writes = []
        self._async_agfs = self

    async def ls(self, uri, node_limit=None, ctx=None):
        return []

    async def stat(self, uri, ctx=None, skip_count=False):
        content = self._file_contents.get(uri, "")
        return {"size": len(content)}

    async def read_file(self, path, ctx=None):
        return self._file_contents.get(path, "")

    async def read_file_bytes(self, path, ctx=None):
        content = self._file_contents.get(path, b"")
        return content if isinstance(content, bytes) else content.encode()

    async def abstract(self, uri, ctx=None):
        return self._file_contents.get(f"{uri}/.abstract.md", "")

    async def write_file(self, path, content, ctx=None, lease_ref=None):
        self.writes.append((path, content))

    async def pathlock_acquire_exact_batch(self, paths):
        return {"paths": paths}

    async def pathlock_release(self, lease):
        return None

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FakeProcessor:
    """Summary generation raises ConnectionError, mimicking an unreachable VLM."""

    def __init__(self, summary_error=None, summary_text="summary"):
        self.summary_error = summary_error
        self.summary_text = summary_text
        self.summarized_files = []
        self.vectorized_calls = []
        self.generated_overviews = []

    async def _generate_single_file_summary(
        self, file_path, llm_sem=None, ctx=None, file_content=None
    ):
        self.summarized_files.append(file_path)
        if self.summary_error is not None:
            raise self.summary_error
        return {"name": file_path.split("/")[-1], "summary": self.summary_text}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts, **kwargs):
        self.generated_overviews.append(dir_uri)
        return "overview"

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        use_summary=False,
        ingest_options=None,
        file_md5=None,
        file_content=None,
        materialize_content=False,
        scalar_override=None,
        field_patch=None,
        action="merge",
        telemetry_id=None,
    ):
        self.vectorized_calls.append(
            {
                "file_path": file_path,
                "use_summary": use_summary,
                "summary": (summary_dict or {}).get("summary", ""),
            }
        )
        return True

    async def _vectorize_directory(
        self, uri, context_type, abstract, overview, ctx=None, **kwargs
    ):
        pass

    async def _vectorize_directory_simple(self, uri, context_type, abstract, overview, ctx=None):
        await self._vectorize_directory(uri, context_type, abstract, overview, ctx=ctx)


def _file_root_plan(file_uri: str):
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    return SemanticPlan(
        file_uri,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "file",
                    "added",
                    "generate",
                    md5="fresh-md5",
                    index_slots=(IndexSlot(2, f"{file_uri}-l2", action="upsert"),),
                ),
            )
        ),
    )


def _executor(processor, plan, fake_fs, monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fake_fs
    )
    return SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_plan=plan,
    )


@pytest.mark.asyncio
async def test_unreachable_vlm_still_vectorizes_file_body(monkeypatch):
    """Summary generation fails (VLM unreachable) → the file body is embedded
    as the L2 DETAIL vector instead of being silently dropped."""
    file_uri = "viking://resources/docs/guide.md"
    fake_fs = _FakeVikingFS({file_uri: b"guide body about the five gates"})
    processor = _FakeProcessor(summary_error=ConnectionError("VLM unreachable"))
    executor = _executor(processor, _file_root_plan(file_uri), fake_fs, monkeypatch)

    await executor.run(file_uri)

    assert processor.summarized_files == [file_uri]
    assert len(processor.vectorized_calls) == 1
    call = processor.vectorized_calls[0]
    assert call["file_path"] == file_uri
    # The failed summary must not be used as the embedding source.
    assert call["use_summary"] is False
    assert call["summary"] == ""
    assert executor.get_stats().indexed_records == 1


@pytest.mark.asyncio
async def test_successful_summary_still_vectorizes(monkeypatch):
    """Baseline: a healthy summary path keeps vectorizing (regression guard)."""
    file_uri = "viking://resources/docs/guide.md"
    fake_fs = _FakeVikingFS({file_uri: b"guide body about the five gates"})
    processor = _FakeProcessor(summary_error=None, summary_text="clean summary")
    executor = _executor(processor, _file_root_plan(file_uri), fake_fs, monkeypatch)

    await executor.run(file_uri)

    assert len(processor.vectorized_calls) == 1
    call = processor.vectorized_calls[0]
    assert call["file_path"] == file_uri
    assert executor.get_stats().indexed_records == 1


# ---- additional coverage requested in review ----

def _file_root_plan_with_action(file_uri: str, action: str, file_vector_source=None):
    from openviking.storage.context_update_plan import (
        IndexSlot,
        SemanticPlan,
        SemanticTreeEntry,
        SemanticTreeSnapshot,
    )

    kwargs = {}
    if file_vector_source is not None:
        kwargs["file_vector_source"] = file_vector_source
    return SemanticPlan(
        file_uri,
        "resource",
        SemanticTreeSnapshot(
            (
                SemanticTreeEntry(
                    "",
                    "file",
                    "added",
                    "generate",
                    md5="fresh-md5",
                    index_slots=(IndexSlot(2, f"{file_uri}-l2", action=action),),
                ),
            )
        ),
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["upsert", "merge"])
async def test_summary_failure_vectorizes_for_upsert_and_merge(monkeypatch, action):
    file_uri = "viking://resources/docs/guide.md"
    fake_fs = _FakeVikingFS({file_uri: b"guide body"})
    processor = _FakeProcessor(summary_error=ConnectionError("VLM unreachable"))
    executor = _executor(
        processor, _file_root_plan_with_action(file_uri, action), fake_fs, monkeypatch
    )

    await executor.run(file_uri)

    assert [c["file_path"] for c in processor.vectorized_calls] == [file_uri]
    assert executor.get_stats().indexed_records == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["none"])
async def test_summary_failure_respects_non_index_actions(monkeypatch, action):
    """A failed summary must not widen the set of actions that index. Note:
    delete/update_fields are rejected by SemanticPlan validation itself
    ("semantic index slots only supports none, upsert, or merge") and never
    reach the executor, so only `none` is exercisable here."""
    file_uri = "viking://resources/docs/guide.md"
    fake_fs = _FakeVikingFS({file_uri: b"guide body"})
    processor = _FakeProcessor(summary_error=ConnectionError("VLM unreachable"))
    executor = _executor(
        processor, _file_root_plan_with_action(file_uri, action), fake_fs, monkeypatch
    )

    await executor.run(file_uri)

    assert processor.vectorized_calls == []
    assert executor.get_stats().indexed_records == 0


@pytest.mark.asyncio
async def test_summary_failure_vectorizes_in_summary_when_available_mode(monkeypatch):
    """Code-repo mode: 'summary when available' means the body is the fallback
    source when the (failed) summary is empty — not a reason to skip."""
    file_uri = "viking://resources/docs/impl.py"
    fake_fs = _FakeVikingFS({file_uri: b"def impl():\n    return 1\n"})
    processor = _FakeProcessor(summary_error=ConnectionError("VLM unreachable"))
    executor = _executor(
        processor,
        _file_root_plan_with_action(file_uri, "upsert", "summary_when_available"),
        fake_fs,
        monkeypatch,
    )

    await executor.run(file_uri)

    assert [c["file_path"] for c in processor.vectorized_calls] == [file_uri]
    assert processor.vectorized_calls[0]["use_summary"] is False


@pytest.mark.asyncio
async def test_vectorize_returning_false_is_settled_without_error(monkeypatch):
    """If the body itself is unreadable (vectorize_file returns False), the slot
    stays unindexed but the turn settles cleanly with no false success count."""
    file_uri = "viking://resources/docs/guide.md"
    fake_fs = _FakeVikingFS({file_uri: b"guide body"})
    processor = _FakeProcessor(summary_error=ConnectionError("VLM unreachable"))
    executor = _executor(processor, _file_root_plan(file_uri), fake_fs, monkeypatch)

    async def _fail_vectorize(**kwargs):
        processor.vectorized_calls.append(kwargs)
        return False

    processor._vectorize_single_file = _fail_vectorize

    await executor.run(file_uri)

    assert len(processor.vectorized_calls) == 1
    assert executor.get_stats().indexed_records == 0


