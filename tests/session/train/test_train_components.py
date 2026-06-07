# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any

import pytest
from test_fakes import fake_request_context

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.train import (
    ContentHashPolicySnapshotter,
    DryRunPolicyUpdater,
    Experience,
    ExperienceSet,
    ExperienceSetLoader,
    MemoryFilePolicyUpdater,
    PatchMergePolicyOptimizer,
    PatchMergePolicyOptimizerContext,
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

    async def rm(self, uri: str, recursive: bool = False, ctx=None, lock_handle=None):
        del recursive, ctx, lock_handle
        self.files.pop(uri, None)
        return {"estimated_deleted_count": 1}


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


def _memory_file(
    *,
    name: str,
    uri: str | None,
    content: str,
    version: int | None = 1,
    status: str = "production",
) -> MemoryFile:
    fields: dict[str, Any] = {
        "memory_type": "experiences",
        "experience_name": name,
        "status": status,
    }
    if version is not None:
        fields["version"] = version
    return MemoryFile(
        uri=uri,
        content=content,
        memory_type="experiences",
        extra_fields=fields,
    )


def _patch_gradient(
    *,
    name: str = "booking_duplicate_handling",
    uri: str | None = "viking://user/u/memories/experiences/booking_duplicate_handling.md",
    before: str | None = "content",
    after: str = "new content",
    base_version: int | None = 1,
    rationale: str = "r",
    evidence_trajectory_uris: list[str] | None = None,
    confidence: float = 0.8,
    metadata: dict[str, Any] | None = None,
) -> PatchSemanticGradient:
    return PatchSemanticGradient(
        before_file=(
            _memory_file(name=name, uri=uri, content=before, version=base_version)
            if before is not None
            else None
        ),
        after_file=_memory_file(name=name, uri=uri, content=after, version=base_version),
        base_version=base_version,
        rationale=rationale,
        evidence_trajectory_uris=evidence_trajectory_uris or ["traj://1"],
        confidence=confidence,
        metadata=metadata or {},
    )


def _plan_from_gradient(gradient: PatchSemanticGradient) -> PolicyUpdatePlan:
    return PolicyUpdatePlan(
        items=[
            _plan_item_from_gradient(gradient),
        ]
    )


def _plan_item_from_gradient(gradient: PatchSemanticGradient):
    from openviking.session.train import PolicyPlanItem

    return PolicyPlanItem(
        kind="upsert_experience",
        target_experience_name=gradient.target_experience_name,
        target_experience_uri=gradient.target_experience_uri,
        before_content=(
            gradient.before_file.plain_content() if gradient.before_file is not None else None
        ),
        after_content=gradient.after_file.plain_content(),
        base_version=gradient.base_version,
        confidence=gradient.confidence,
        evidence_trajectory_uris=list(gradient.evidence_trajectory_uris),
        metadata={"rationale": gradient.rationale},
    )


def _delete_plan(*, uri: str, before_content: str = "content") -> PolicyUpdatePlan:
    from openviking.session.train import PolicyPlanItem

    return PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="delete_experience",
                target_experience_name="booking_duplicate_handling",
                target_experience_uri=uri,
                before_content=before_content,
                after_content=None,
                base_version=1,
                confidence=0.8,
                evidence_trajectory_uris=["traj://1"],
                metadata={"rationale": "delete duplicate experience"},
            )
        ]
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
async def test_dry_run_policy_updater_simulates_patch_plan_items():
    policy_set = _experience_set()
    gradient = _patch_gradient(uri=policy_set.policies[0].uri, before="content", after="new content")
    plan = _plan_from_gradient(gradient)

    result = await DryRunPolicyUpdater().apply(plan, policy_set)

    assert result.updated_policy_set is not policy_set
    assert result.updated_policy_set.policies[0].content == "new content"
    assert result.updated_policy_set.policies[0].version == 2
    assert result.written_uris == []
    assert result.metadata["dry_run"] is True
    assert result.metadata["simulated"] is True


@pytest.mark.asyncio
async def test_dry_run_policy_updater_simulates_delete_plan_items():
    policy_set = _experience_set()
    plan = _delete_plan(uri=policy_set.policies[0].uri)

    result = await DryRunPolicyUpdater().apply(plan, policy_set)

    assert result.updated_policy_set is not policy_set
    assert result.updated_policy_set.policies == []
    assert result.written_uris == []
    assert result.deleted_uris == []
    assert result.metadata["dry_run"] is True
    assert result.metadata["simulated"] is True


@pytest.mark.asyncio
async def test_memory_file_policy_updater_writes_experience_files():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    gradient = _patch_gradient(uri=policy_set.policies[0].uri, before="content", after="new content")
    plan = _plan_from_gradient(gradient)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(plan, policy_set)

    assert result.errors == []
    assert result.written_uris == [policy_set.policies[0].uri]
    written = fs.files[policy_set.policies[0].uri]
    assert written.startswith("new content")
    assert '"memory_type": "experiences"' in written
    assert '"experience_name": "booking_duplicate_handling"' in written
    assert '"version": 2' in written


@pytest.mark.asyncio
async def test_memory_file_policy_updater_deletes_experience_files():
    policy_set = _experience_set()
    uri = policy_set.policies[0].uri
    fs = FakeVikingFS({uri: "content"})
    plan = _delete_plan(uri=uri)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(plan, policy_set)

    assert result.errors == []
    assert result.written_uris == []
    assert result.deleted_uris == [uri]
    assert result.updated_policy_set.policies == []
    assert uri not in fs.files


@pytest.mark.asyncio
async def test_memory_file_policy_updater_detects_base_content_mismatch():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri,
        before="stale content",
        after="new content",
    )
    plan = _plan_from_gradient(gradient)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(plan, policy_set)

    assert result.written_uris == []
    assert result.errors == [
        "base content mismatch for booking_duplicate_handling: expected gradient before_content"
    ]
    assert policy_set.policies[0].uri not in fs.files


@pytest.mark.asyncio
async def test_patch_merge_policy_optimizer_runs_patch_merge_extract_loop(monkeypatch):
    from openviking.session.memory.dataclass import (
        MemoryFile,
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = _experience_set()
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri,
        before="stale content",
        after="merged content",
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

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert plan.metadata["optimizer"] == "patch_merge"
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
async def test_patch_merge_policy_optimizer_merges_all_patch_gradients_once(monkeypatch):
    from openviking.session.memory.dataclass import (
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = _experience_set()
    root = policy_set.root_uri
    gradients = [
        _patch_gradient(
            name="重复预订处理",
            uri=f"{root}/重复预订处理.md",
            before=None,
            after="核对订单后只取消重复订单",
            base_version=None,
            rationale="r1",
            evidence_trajectory_uris=["traj://1"],
            confidence=0.8,
        ),
        _patch_gradient(
            name="处理酒店重复预订",
            uri=f"{root}/处理酒店重复预订.md",
            before=None,
            after="识别有效订单并取消重复订单",
            base_version=None,
            rationale="r2",
            evidence_trajectory_uris=["traj://2"],
            confidence=0.9,
        ),
    ]
    captured = {"constructed": 0}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured["constructed"] += 1
            captured.update(kwargs)

        async def run(self):
            provider = captured["context_provider"]
            captured["prefetch_messages"] = await provider.prefetch()
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields={
                                "experience_name": "重复预订处理",
                                "content": "合并后的重复预订处理经验",
                            },
                            memory_type="experiences",
                            uris=[f"{root}/重复预订处理.md"],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr("openviking.session.train.optimizers.ExtractLoop", FakeExtractLoop)

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        gradients,
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert captured["constructed"] == 1
    provider = captured["context_provider"]
    assert provider.required_file_uris == [
        f"{root}/重复预订处理.md",
        f"{root}/处理酒店重复预订.md",
    ]
    assert len(provider.patches) == 2
    assert captured["prefetch_messages"][-1]["content"].count("## Memory Patch") == 2
    assert plan.metadata["optimizer"] == "patch_merge"
    assert plan.metadata["patch_gradient_count"] == 2
    assert len(plan.items) == 1
    assert plan.items[0].target_experience_name == "重复预订处理"
    assert plan.items[0].evidence_trajectory_uris == ["traj://1", "traj://2"]


@pytest.mark.asyncio
async def test_patch_merge_policy_optimizer_runs_llm_for_single_patch(monkeypatch):
    from openviking.session.memory.dataclass import (
        MemoryFile,
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = _experience_set()
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri,
        before="content",
        after="merged update",
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

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert captured["constructed"] is True
    assert plan.metadata["patch_gradient_count"] == 1
    assert plan.items[0].after_content == "merged update"
