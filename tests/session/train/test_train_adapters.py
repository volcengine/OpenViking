# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from test_fakes import fake_request_context

from openviking.session.train import (
    ContentHashPolicySnapshotter,
    DryRunPolicyUpdater,
    Experience,
    ExperienceContentPatch,
    ExperienceSet,
    ExperienceSetLoader,
    GroupingPolicyOptimizer,
    MemoryFilePolicyUpdater,
    MergeAwarePolicyOptimizer,
    MergeAwarePolicyOptimizerContext,
    PatchSemanticGradient,
    PolicyUpdatePlan,
)


class FakeVikingFS:
    def __init__(self, files: dict[str, str]):
        self.files = files

    async def ls(self, uri: str, output: str = "original", ctx=None):
        assert output == "original"
        prefix = uri.rstrip("/") + "/"
        return [
            {
                "name": path.removeprefix(prefix),
                "uri": path,
                "isDir": False,
            }
            for path in sorted(self.files)
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]

    async def read_file(self, uri: str, ctx=None):
        return self.files[uri]

    async def write_file(self, uri: str, content: str, ctx=None):
        self.files[uri] = content


@dataclass
class DummyGradient:
    target_experience_name: str
    target_experience_uri: str | None
    base_version: int | None
    rationale: str
    evidence_trajectory_uris: list[str]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _experience_set() -> ExperienceSet:
    return ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
                version=1,
                status="production",
                content="content",
            )
        ],
    )


@pytest.mark.asyncio
async def test_experience_set_loader_reads_memory_files():
    root = "viking://user/u/memories/experiences"
    fs = FakeVikingFS(
        {
            f"{root}/booking_duplicate_handling.md": '## Situation\n- test\n\n<!-- MEMORY_FIELDS\n{"memory_type": "experiences", "experience_name": "booking_duplicate_handling", "version": 3, "status": "staging"}\n-->',
            f"{root}/.overview.md": "hidden",
        }
    )

    ctx = fake_request_context()
    loaded = await ExperienceSetLoader(viking_fs=fs).load(root, ctx=ctx)

    assert loaded.root_uri == root
    assert loaded.viking_fs is fs
    assert loaded.request_context is ctx
    assert len(loaded.policies) == 1
    policy = loaded.policies[0]
    assert policy.name == "booking_duplicate_handling"
    assert policy.version == 3
    assert policy.status == "staging"
    assert policy.content == "## Situation\n- test"
    assert policy.metadata["memory_type"] == "experiences"


@pytest.mark.asyncio
async def test_experience_set_loader_requires_request_context():
    root = "viking://user/u/memories/experiences"
    fs = FakeVikingFS({})

    with pytest.raises(ValueError, match="requires request_context ctx"):
        await ExperienceSetLoader(viking_fs=fs).load(root)


@pytest.mark.asyncio
async def test_content_hash_snapshotter_is_deterministic():
    snapshotter = ContentHashPolicySnapshotter()
    policy_set = _experience_set()

    first = await snapshotter.snapshot(policy_set)
    second = await snapshotter.snapshot(policy_set)

    assert first == second
    assert first.startswith("policy-snapshot:")


@pytest.mark.asyncio
async def test_grouping_policy_optimizer_groups_gradients():
    policy_set = _experience_set()
    gradients = [
        DummyGradient(
            target_experience_name="booking_duplicate_handling",
            target_experience_uri=policy_set.policies[0].uri,
            base_version=1,
            rationale="improve safety",
            evidence_trajectory_uris=["traj://1"],
            confidence=0.8,
        ),
        DummyGradient(
            target_experience_name="new_policy",
            target_experience_uri=None,
            base_version=None,
            rationale="new behavior",
            evidence_trajectory_uris=["traj://2"],
            confidence=0.7,
        ),
    ]

    plan = await GroupingPolicyOptimizer().plan(gradients, policy_set)

    assert plan.metadata["gradient_count"] == 2
    assert [g["target"] for g in plan.metadata["groups"]] == [
        "new:new_policy",
        policy_set.policies[0].uri,
    ]


@pytest.mark.asyncio
async def test_dry_run_policy_updater_does_not_mutate_policy_set():
    policy_set = _experience_set()
    plan = PolicyUpdatePlan(metadata={"hello": "world"})

    result = await DryRunPolicyUpdater().apply(plan, policy_set)

    assert result.updated_policy_set is policy_set
    assert result.written_uris == []
    assert result.deleted_uris == []
    assert result.metadata["dry_run"] is True
    assert result.metadata["simulated"] is True
    assert result.metadata["plan"] == {"hello": "world"}


@pytest.mark.asyncio
async def test_grouping_policy_optimizer_creates_patch_plan_items():
    policy_set = _experience_set()
    gradients = [
        PatchSemanticGradient(
            target_experience_name="booking_duplicate_handling",
            target_experience_uri=policy_set.policies[0].uri,
            base_version=1,
            patch=ExperienceContentPatch(
                before_content="content",
                after_content="improved content",
                metadata={"supersedes": []},
            ),
            rationale="improve safety",
            evidence_trajectory_uris=["traj://1"],
            confidence=0.8,
        )
    ]

    plan = await GroupingPolicyOptimizer().plan(gradients, policy_set)

    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.kind == "upsert_experience"
    assert item.target_experience_name == "booking_duplicate_handling"
    assert item.target_experience_uri == policy_set.policies[0].uri
    assert item.before_content == "content"
    assert item.after_content == "improved content"
    assert item.metadata["rationale"] == "improve safety"
    assert plan.metadata["conflicts"] == []


@pytest.mark.asyncio
async def test_dry_run_policy_updater_simulates_patch_plan_items():
    policy_set = _experience_set()
    gradient = PatchSemanticGradient(
        target_experience_name="booking_duplicate_handling",
        target_experience_uri=policy_set.policies[0].uri,
        base_version=1,
        patch=ExperienceContentPatch(before_content="content", after_content="new content"),
        rationale="r",
        evidence_trajectory_uris=["traj://1"],
        confidence=0.8,
    )
    plan = await GroupingPolicyOptimizer().plan([gradient], policy_set)

    result = await DryRunPolicyUpdater().apply(plan, policy_set)

    assert result.updated_policy_set is not policy_set
    assert result.updated_policy_set.policies[0].content == "new content"
    assert result.updated_policy_set.policies[0].version == 2
    assert result.written_uris == []
    assert result.metadata["dry_run"] is True
    assert result.metadata["simulated"] is True


@pytest.mark.asyncio
async def test_memory_file_policy_updater_writes_experience_files():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    gradient = PatchSemanticGradient(
        target_experience_name="booking_duplicate_handling",
        target_experience_uri=policy_set.policies[0].uri,
        base_version=1,
        patch=ExperienceContentPatch(before_content="content", after_content="new content"),
        rationale="r",
        evidence_trajectory_uris=["traj://1"],
        confidence=0.8,
    )
    plan = await GroupingPolicyOptimizer().plan([gradient], policy_set)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(plan, policy_set)

    assert result.errors == []
    assert result.written_uris == [policy_set.policies[0].uri]
    written = fs.files[policy_set.policies[0].uri]
    assert written.startswith("new content")
    assert '"memory_type": "experiences"' in written
    assert '"experience_name": "booking_duplicate_handling"' in written
    assert '"version": 2' in written


@pytest.mark.asyncio
async def test_memory_file_policy_updater_detects_base_content_mismatch():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    gradient = PatchSemanticGradient(
        target_experience_name="booking_duplicate_handling",
        target_experience_uri=policy_set.policies[0].uri,
        base_version=1,
        patch=ExperienceContentPatch(before_content="stale content", after_content="new content"),
        rationale="r",
        evidence_trajectory_uris=["traj://1"],
        confidence=0.8,
    )
    plan = await GroupingPolicyOptimizer().plan([gradient], policy_set)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(plan, policy_set)

    assert result.written_uris == []
    assert result.errors == [
        "base content mismatch for booking_duplicate_handling: expected gradient before_content"
    ]
    assert policy_set.policies[0].uri not in fs.files


@pytest.mark.asyncio
async def test_merge_aware_policy_optimizer_runs_patch_merge_extract_loop(monkeypatch):
    from openviking.session.memory.dataclass import (
        MemoryFile,
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = _experience_set()
    gradient = PatchSemanticGradient(
        target_experience_name="booking_duplicate_handling",
        target_experience_uri=policy_set.policies[0].uri,
        base_version=1,
        patch=ExperienceContentPatch(
            before_content="stale content", after_content="merged content"
        ),
        rationale="r",
        evidence_trajectory_uris=["traj://1"],
        confidence=0.8,
    )
    captured = {}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            provider = captured["context_provider"]
            captured["prefetch_messages"] = await provider.prefetch()
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=MemoryFile(
                                uri=policy_set.policies[0].uri,
                                content="content",
                                memory_type="experiences",
                                extra_fields={
                                    "experience_name": "booking_duplicate_handling",
                                    "version": 1,
                                },
                            ),
                            memory_fields={
                                "experience_name": "booking_duplicate_handling",
                                "content": "merged content",
                            },
                            memory_type="experiences",
                            uris=[policy_set.policies[0].uri],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr("openviking.session.train.optimizers.ExtractLoop", FakeExtractLoop)

    plan = await MergeAwarePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        MergeAwarePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert plan.metadata["optimizer"] == "merge_aware"
    assert plan.items[0].kind == "upsert_experience"
    assert plan.items[0].target_experience_uri == policy_set.policies[0].uri
    assert plan.items[0].before_content == "content"
    assert plan.items[0].after_content == "merged content"
    assert plan.items[0].evidence_trajectory_uris == ["traj://1"]
    assert captured["context_provider"].__class__.__name__ == "PatchMergeContextProvider"
    assert captured["context_provider"].get_tools() == []
    assert "```diff" in captured["prefetch_messages"][-1]["content"]
    assert "-stale content" in captured["prefetch_messages"][-1]["content"]
    assert "+merged content" in captured["prefetch_messages"][-1]["content"]


@pytest.mark.asyncio
async def test_merge_aware_policy_optimizer_bypasses_llm_for_single_clean_patch(monkeypatch):
    policy_set = _experience_set()
    gradient = PatchSemanticGradient(
        target_experience_name="booking_duplicate_handling",
        target_experience_uri=policy_set.policies[0].uri,
        base_version=1,
        patch=ExperienceContentPatch(before_content="content", after_content="clean update"),
        rationale="r",
        evidence_trajectory_uris=["traj://1"],
        confidence=0.8,
    )

    class UnexpectedExtractLoop:
        def __init__(self, **kwargs):
            del kwargs
            raise AssertionError("ExtractLoop should not be constructed for single clean patch")

    monkeypatch.setattr("openviking.session.train.optimizers.ExtractLoop", UnexpectedExtractLoop)

    plan = await MergeAwarePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        MergeAwarePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert plan.metadata["optimizer"] == "merge_aware"
    assert plan.metadata["fast_path_groups"] == [
        {
            "target": policy_set.policies[0].uri,
            "reason": "single_clean_patch",
            "gradient_count": 1,
        }
    ]
    assert plan.metadata["merge_errors"] == []
    assert len(plan.items) == 1
    item = plan.items[0]
    assert item.kind == "upsert_experience"
    assert item.target_experience_uri == policy_set.policies[0].uri
    assert item.before_content == "content"
    assert item.after_content == "clean update"
    assert item.metadata["optimizer_fast_path"] == "single_clean_patch"


@pytest.mark.asyncio
async def test_merge_aware_policy_optimizer_uses_llm_when_single_patch_base_differs(monkeypatch):
    from openviking.session.memory.dataclass import (
        MemoryFile,
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = _experience_set()
    gradient = PatchSemanticGradient(
        target_experience_name="booking_duplicate_handling",
        target_experience_uri=policy_set.policies[0].uri,
        base_version=1,
        patch=ExperienceContentPatch(before_content="stale content", after_content="merged update"),
        rationale="r",
        evidence_trajectory_uris=["traj://1"],
        confidence=0.8,
    )
    captured = {"constructed": False}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured["constructed"] = True
            captured.update(kwargs)

        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=MemoryFile(
                                uri=policy_set.policies[0].uri,
                                content="content",
                                memory_type="experiences",
                                extra_fields={
                                    "experience_name": "booking_duplicate_handling",
                                    "version": 1,
                                },
                            ),
                            memory_fields={
                                "experience_name": "booking_duplicate_handling",
                                "content": "merged update",
                            },
                            memory_type="experiences",
                            uris=[policy_set.policies[0].uri],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr("openviking.session.train.optimizers.ExtractLoop", FakeExtractLoop)

    plan = await MergeAwarePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        MergeAwarePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert captured["constructed"] is True
    assert plan.metadata["fast_path_groups"] == []
    assert plan.items[0].after_content == "merged update"
