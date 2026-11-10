# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from collections import Counter
from urllib.parse import quote

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import (
    freshness_metadata,
    parse_abstract_overview,
    parse_overview_file_summaries,
    render_abstract_overview,
)
from openviking.storage.queuefs import semantic_executor as executor_module
from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
from openviking_cli.session.user_id import UserIdentifier
from tests.storage.test_semantic_executor_incremental import _FakeProcessor, _FakeVikingFS


class _ObservedFS(_FakeVikingFS):
    def __init__(self, names, *, overview=None, pending=4):
        self.root = "viking://resources/rules"
        self.reads = Counter()
        self.body_reads = Counter()
        contents = {f"{self.root}/{name}": f"body for {name}" for name in names}
        if overview is not None:
            metadata = {"freshness": freshness_metadata(len(names), len(names), pending)}
            contents[f"{self.root}/.overview.md"] = render_abstract_overview(
                ContextLevel.OVERVIEW, self.root, overview, metadata
            )
            contents[f"{self.root}/.abstract.md"] = render_abstract_overview(
                ContextLevel.ABSTRACT, self.root, "old abstract", metadata
            )
        super().__init__(
            tree={self.root: [{"name": name, "isDir": False} for name in names]},
            file_contents=contents,
        )

    async def read_file(self, path, ctx=None):
        self.reads[path] += 1
        if self._norm(path) not in self._file_contents:
            raise FileNotFoundError(path)
        return await super().read_file(path, ctx=ctx)

    async def read_file_bytes(self, path, ctx=None):
        self.body_reads[path] += 1
        if self._norm(path) not in self._file_contents:
            raise FileNotFoundError(path)
        return await super().read_file_bytes(path, ctx=ctx)

    async def exists(self, uri, ctx=None):
        return self._norm(uri) in self._tree or self._norm(uri) in self._file_contents


class _ObservedProcessor(_FakeProcessor):
    def __init__(self, fs, abstracts=None, *, lookup_error=None, summary_error=None):
        super().__init__(fs, transfer_summaries=abstracts)
        self.lookup_error = lookup_error
        self.summary_error = summary_error
        self.lookup_contexts = []
        self.overview_inputs = []
        self.before_overview = None

    def _parse_overview_md(self, content):
        # Exercise the production parser, not the simplified legacy fake format.
        return parse_overview_file_summaries(content)

    async def _load_transfer_file_summaries(self, paths, ctx=None):
        self.lookup_contexts.append(ctx)
        if self.lookup_error:
            self.transfer_summary_calls.append(list(paths))
            raise self.lookup_error
        return await super()._load_transfer_file_summaries(paths, ctx=ctx)

    async def _generate_single_file_summary(self, path, **kwargs):
        await super()._generate_single_file_summary(path, **kwargs)
        if self.summary_error:
            raise self.summary_error
        return {"name": path.rsplit("/", 1)[-1], "summary": "new summary"}

    async def _generate_overview(self, uri, file_summaries, children_abstracts, **kwargs):
        self.generated_overviews.append(uri)
        self.overview_inputs.append({item["name"]: item["summary"] for item in file_summaries})
        if self.before_overview:
            await self.before_overview()
        return _overview(uri, self.overview_inputs[-1])


def _overview(root, summaries):
    sections = ["# rules", "", "## Detailed Description", ""]
    for name, summary in summaries.items():
        sections.extend([f"### [{name}]({root}/{quote(name, safe='')})", summary, ""])
    return "\n".join(sections)


def _executor(monkeypatch, fs, processor, *, changed=(), trigger="parent_refresh", **kwargs):
    monkeypatch.setattr(executor_module, "get_viking_fs", lambda: fs)
    return SemanticTreeExecutor(
        processor=processor,
        context_type=kwargs.pop("context_type", "resource"),
        max_concurrent_llm=4,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=fs.root,
        recursive=kwargs.pop("recursive", False),
        changes={"modified": list(changed)},
        generation_trigger=trigger,
        **kwargs,
    )


def _capture_logs(monkeypatch):
    logs = []
    monkeypatch.setattr(
        executor_module.logger, "info", lambda message, *args, **kwargs: logs.append(message % args)
    )
    return logs


@pytest.mark.asyncio
async def test_pending_three_sources_do_not_read_reused_bodies(monkeypatch):
    names = [f"file-{i:03}.md" for i in range(40)]
    root = "viking://resources/rules"
    old = {name: f"old summary {name}" for name in names}
    fs = _ObservedFS(names, overview=_overview(root, old))
    abstracts = {f"{root}/{name}": f"L2 summary {name}" for name in names[:35]}
    processor = _ObservedProcessor(fs, abstracts)
    changed = f"{root}/{names[-1]}"
    logs = _capture_logs(monkeypatch)
    times = iter([10.0, 12.5])
    monkeypatch.setattr(executor_module, "perf_counter", lambda: next(times))
    monkeypatch.setattr(
        executor_module,
        "content_md5",
        lambda content: pytest.fail("summary reuse must not hash sibling contents"),
    )
    executor = _executor(
        monkeypatch, fs, processor, changed=[changed], file_md5s={changed: "known"}
    )

    await executor.run(root)

    assert processor.summarized_files == [changed]
    assert processor.vectorized_files == [changed]
    assert fs.body_reads == {changed: 1}
    assert processor.transfer_summary_calls == [sorted(f"{root}/{name}" for name in names[:-1])]
    assert processor.lookup_contexts == [executor._ctx]
    assert processor.overview_inputs == [
        {
            name: f"L2 summary {name}" if i < 35 else "new summary" if i == 39 else old[name]
            for i, name in enumerate(names)
        }
    ]
    # Two reads while snapshotting pending, two during atomic sidecar writeback.
    # The old-overview fallback must not perform a fifth read.
    assert fs.reads[f"{root}/.overview.md"] == 4
    summary_logs = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summary_logs) == 1
    assert "files=40 dirs=0 abstract=35 overview=4 generated=1 failed_files=0" in summary_logs[0]
    assert "total_ms=2500.000 status=completed" in summary_logs[0]
    assert "task_id=" in summary_logs[0] and "telemetry_id=" in summary_logs[0]


@pytest.mark.asyncio
async def test_only_missing_summaries_read_bodies_and_keep_new_pending(monkeypatch):
    names = ["a.md", "b.md", "c.md", "d.md"]
    root = "viking://resources/rules"
    fs = _ObservedFS(names, overview=_overview(root, {"b.md": "from overview"}))
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "from L2", f"{root}/c.md": ""})
    changed = f"{root}/d.md"

    async def add_pending():
        for level, filename in [
            (ContextLevel.OVERVIEW, ".overview.md"),
            (ContextLevel.ABSTRACT, ".abstract.md"),
        ]:
            uri = f"{root}/{filename}"
            document = parse_abstract_overview(fs._file_contents[uri])
            metadata = dict(document.metadata)
            metadata["freshness"] = {**metadata["freshness"], "pending_child_changes": 7}
            fs._file_contents[uri] = render_abstract_overview(level, root, document.body, metadata)

    processor.before_overview = add_pending
    executor = _executor(
        monkeypatch, fs, processor, changed=[changed], file_md5s={changed: "known"}
    )
    await executor.run(root)

    assert set(processor.summarized_files) == {f"{root}/c.md", changed}
    assert fs.body_reads == {f"{root}/c.md": 1, changed: 1}
    assert processor.vectorized_files == [changed]
    document = parse_abstract_overview(fs._file_contents[f"{root}/.overview.md"])
    assert document.metadata["freshness"]["pending_child_changes"] == 3


@pytest.mark.asyncio
async def test_failed_lookup_falls_back_to_overview(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md", "b.md"], overview=_overview(root, {"a.md": "old A", "b.md": "old B"}))
    processor = _ObservedProcessor(fs, lookup_error=RuntimeError("lookup unavailable"))
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)

    await executor.run(root)

    assert not processor.summarized_files
    assert not fs.body_reads
    assert processor.overview_inputs == [{"a.md": "old A", "b.md": "old B"}]
    assert "abstract=0 overview=2 generated=0" in next(
        line for line in logs if "[ParentAggregation]" in line
    )


@pytest.mark.asyncio
async def test_blank_abstract_falls_back_and_blank_overview_generates(monkeypatch):
    root = "viking://resources/rules"
    overview = _overview(root, {"a.md": "old A", "b.md": ""})
    fs = _ObservedFS(["a.md", "b.md"], overview=overview)
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "   "})
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)
    await executor.run(root)

    assert processor.overview_inputs == [{"a.md": "old A", "b.md": "new summary"}]
    assert processor.summarized_files == [f"{root}/b.md"]
    assert fs.body_reads == {f"{root}/b.md": 1}
    assert "abstract=0 overview=1 generated=1" in next(
        line for line in logs if "[ParentAggregation]" in line
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pending,trigger,aggregate,recursive,context",
    [
        (0, "parent_refresh", True, False, "resource"),
        (4, "parent_refresh", False, False, "resource"),
        (4, "resource_ingest", True, False, "resource"),
        (4, "reindex", True, False, "resource"),
        (4, "parent_refresh", True, True, "resource"),
        (4, "parent_refresh", True, False, "skill"),
    ],
)
async def test_other_paths_do_not_add_l2_queries(
    monkeypatch, pending, trigger, aggregate, recursive, context
):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {"a.md": "old A"}), pending=pending)
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "L2 A"})
    logs = _capture_logs(monkeypatch)
    executor = _executor(
        monkeypatch,
        fs,
        processor,
        trigger=trigger,
        aggregate_directory=aggregate,
        recursive=recursive,
        context_type=context,
    )

    await executor.run(root)

    assert not processor.transfer_summary_calls
    if pending == 0:
        assert not processor.summarized_files
        assert not fs.body_reads
        assert not processor.generated_overviews
        assert executor._overview_cache[root] == {"a.md": "old A"}
        assert "abstract=0 overview=1 generated=0" in next(
            line for line in logs if "[ParentAggregation]" in line
        )
    if (
        not aggregate
        or trigger in {"resource_ingest", "reindex"}
        or recursive
        or context != "resource"
    ):
        assert not [line for line in logs if "[ParentAggregation]" in line]


@pytest.mark.asyncio
async def test_directory_only_keeps_trigger_result_and_uses_overview_fallback(monkeypatch):
    root = "viking://resources/rules"
    old = {"a.md": "old A", "b.md": "old B", "c.md": "old C"}
    fs = _ObservedFS(list(old), overview=_overview(root, old), pending=0)
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "L2 A"})
    changed = f"{root}/c.md"
    logs = _capture_logs(monkeypatch)
    executor = _executor(
        monkeypatch,
        fs,
        processor,
        changed=[changed],
        trigger="content_write_parent_refresh",
        file_abstracts={changed: ""},
    )

    await executor.run(root)

    assert processor.transfer_summary_calls == [[f"{root}/a.md", f"{root}/b.md"]]
    assert not processor.summarized_files and not fs.body_reads and not processor.vectorized_files
    assert processor.overview_inputs == [{"a.md": "L2 A", "b.md": "old B", "c.md": ""}]
    assert executor.root_write_result.wrote
    assert "abstract=2 overview=1 generated=0" in next(
        line for line in logs if "[ParentAggregation]" in line
    )


@pytest.mark.asyncio
async def test_existing_input_abstract_avoids_lookup_and_body_read(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {"a.md": "old A"}))
    processor = _ObservedProcessor(fs, lookup_error=AssertionError("unnecessary lookup"))
    executor = _executor(monkeypatch, fs, processor, file_abstracts={f"{root}/a.md": "supplied A"})

    await executor.run(root)

    assert not processor.transfer_summary_calls and not fs.body_reads
    assert processor.overview_inputs == [{"a.md": "supplied A"}]


@pytest.mark.asyncio
async def test_large_sibling_set_is_loaded_together(monkeypatch):
    names = [f"{i:03}.md" for i in range(121)]
    root = "viking://resources/rules"
    fs = _ObservedFS(names, overview=_overview(root, {}))
    paths = [f"{root}/{name}" for name in names]
    processor = _ObservedProcessor(fs, dict.fromkeys(paths, "L2 summary"))
    executor = _executor(monkeypatch, fs, processor)

    await executor.run(root)

    assert processor.transfer_summary_calls == [paths]
    assert not fs.body_reads and not processor.summarized_files
    assert len(processor.overview_inputs[0]) == 121


@pytest.mark.asyncio
async def test_generation_failure_is_counted_once(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    processor = _ObservedProcessor(fs, summary_error=RuntimeError("model unavailable"))
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)

    await executor.run(root)

    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 1
    assert "abstract=0 overview=0 generated=1 failed_files=1" in summaries[0]


@pytest.mark.asyncio
async def test_read_failure_logs_partial_aggregation(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    del fs._file_contents[f"{root}/a.md"]
    processor = _ObservedProcessor(fs)
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)

    with pytest.raises(FileNotFoundError):
        await executor.run(root)

    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 1
    assert "generated=0 failed_files=1" in summaries[0]
    assert "status=failed" in summaries[0]


@pytest.mark.asyncio
async def test_cancelled_aggregation_logs_once_after_active_work_finishes(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    processor = _ObservedProcessor(fs)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_summary(path, **kwargs):
        entered.set()
        await release.wait()
        return {"name": "a.md", "summary": "new summary"}

    processor._generate_single_file_summary = blocked_summary
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)
    task = asyncio.create_task(executor.run(root))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 1
    assert "generated=1" in summaries[0]
    assert "status=cancelled" in summaries[0]


@pytest.mark.asyncio
async def test_directory_generation_error_is_logged_as_failed(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "L2 A"})

    async def fail_overview(*args, **kwargs):
        raise RuntimeError("overview generation failed")

    processor._generate_overview = fail_overview
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)
    await executor.run(root)

    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 1
    assert "abstract=1 overview=0 generated=0" in summaries[0]
    assert "status=failed" in summaries[0]
    assert (
        parse_abstract_overview(fs._file_contents[f"{root}/.overview.md"]).metadata["freshness"][
            "pending_child_changes"
        ]
        == 4
    )


@pytest.mark.asyncio
async def test_stale_aggregation_logs_once_without_publishing(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "L2 A"})
    logs = _capture_logs(monkeypatch)
    executor = _executor(monkeypatch, fs, processor)
    monkeypatch.setattr(executor, "_is_stale", lambda: True)

    await executor.run(root)

    assert executor.stale
    assert not fs.writes and not processor.vectorized_dirs
    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 1
    assert "abstract=1 overview=0 generated=0" in summaries[0]
    assert "status=stale" in summaries[0]


@pytest.mark.asyncio
async def test_retried_aggregation_has_independent_source_counts(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "L2 A"})
    logs = _capture_logs(monkeypatch)
    original_overview = processor._generate_overview
    attempts = 0

    async def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary overview failure")
        return await original_overview(*args, **kwargs)

    processor._generate_overview = fail_once
    await _executor(monkeypatch, fs, processor).run(root)
    await _executor(monkeypatch, fs, processor).run(root)

    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 2
    assert all("abstract=1 overview=0 generated=0" in line for line in summaries)
    assert "status=failed" in summaries[0]
    assert "status=completed" in summaries[1]


@pytest.mark.asyncio
async def test_total_time_includes_initial_dag_scheduler_wait(monkeypatch):
    root = "viking://resources/rules"
    fs = _ObservedFS(["a.md"], overview=_overview(root, {}))
    processor = _ObservedProcessor(fs, {f"{root}/a.md": "L2 A"})
    logs = _capture_logs(monkeypatch)
    clock = [0.0]
    scheduler = executor_module.get_semantic_tree_scheduler(4)

    class DelayedScheduler:
        def submit(self, executor, work):
            if work.kind == "dir":
                clock[0] = 10.0
            scheduler.submit(executor, work)

    monkeypatch.setattr(
        executor_module, "get_semantic_tree_scheduler", lambda limit: DelayedScheduler()
    )
    monkeypatch.setattr(executor_module, "perf_counter", lambda: clock[0])
    executor = _executor(monkeypatch, fs, processor)
    await executor.run(root)

    summaries = [line for line in logs if "[ParentAggregation]" in line]
    assert len(summaries) == 1
    assert "total_ms=10000.000" in summaries[0]
