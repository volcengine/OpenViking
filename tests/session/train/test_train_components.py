# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any

import pytest
from test_fakes import fake_request_context

from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
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
from openviking.session.train.components.policy_optimizer import (
    _operations_to_plan_items,
    _plan_quality_review_metadata,
    _remap_source_trajectory_links,
)
from openviking.session.train.components.trajectory_analyzer import (
    _trajectory_content_validation_issues,
    _trajectory_operation_validation_issues,
    _trajectory_validation_issues,
)
from openviking.session.train.gates import GateDecision, GateRunner


class RejectNamedPlanGate:
    name = "test_reject_named_plan"
    mode = "enforce"

    def applies_to(self, target):
        return target.target_kind == "plan_item"

    async def evaluate(self, target):
        if "invalid" not in target.target_name and "bad" not in target.target_name:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="test candidate requires repair",
            evidence={"target_name": target.target_name},
            retriable=True,
            repair_prompt="Repair the rejected test candidate.",
        )


DEFAULT_TRIGGER_CODE = (
    'def should_trigger(ctx):\n    return ctx.get("candidate_tool") == "test_tool"\n'
)


def _experience_fields(
    name: str,
    *,
    situation: str | None = None,
    reminder: str = "- Include the requested total.",
    procedure: str = (
        "1. Read the requested records and bind the total source.\n"
        "2. Calculate the total before composing the answer."
    ),
    verification: str = (
        "- Recompute the total independently from the source records.\n"
        "- Compare the recomputed value with the candidate answer."
    ),
    fallback: str = (
        "- If the source records are unavailable, request the missing evidence instead of "
        "guessing the total."
    ),
    anti_pattern: str = "- Do not omit the requested total.",
) -> dict[str, str]:
    return {
        "experience_name": name,
        "situation": situation
        or (
            "- Applies when: a requested total must appear in the final answer.\n"
            "- Does not apply when: no total was requested.\n"
            "- Evidence binding: the user's request for the total.\n"
            "- Decision boundary: before final communication."
        ),
        "reminder": reminder,
        "procedure": procedure,
        "verification": verification,
        "fallback": fallback,
        "anti_pattern": anti_pattern,
    }


def test_source_trajectory_link_survives_plan_before_final_uri_assignment():
    link = StoredLink(
        from_uri="viking://user/u/memories/experiences/draft_name.md",
        to_uri="viking://user/u/memories/trajectories/source.md",
        link_type="derived_from",
        weight=1.0,
    )

    result = _remap_source_trajectory_links([link], target_uri="")

    assert result == [link]


def test_trajectory_validation_rejects_experience_generation_sections():
    content = (
        "# bad_trace\n"
        "- Outcome: partial\n"
        "- Counterfactual Ideal Experience:\n"
        "  - Candidate C1:\n"
        "    - Runtime experience content: do something\n"
        "- Experience Repair Signal:\n"
        "  - Recommended operation: create\n"
        "  - Selected candidate: C1\n"
        "- Value/Scope Trace:\n"
        "  - Ambiguous references: none\n"
        "- Diagnostic Hints:\n"
        "  - Possible causes: guessed rule\n"
    )

    issues = _trajectory_content_validation_issues("bad_trace", content)

    assert issues
    assert any(
        issue.reason == "trajectory contains experience-generation sections" for issue in issues
    )
    forbidden_issue = next(
        issue
        for issue in issues
        if issue.reason == "trajectory contains experience-generation sections"
    )
    assert "Counterfactual Ideal Experience" in forbidden_issue.details
    assert "Experience Repair Signal" in forbidden_issue.details
    assert "Ambiguous references" in forbidden_issue.details
    assert "Diagnostic Hints" in forbidden_issue.details


def test_trajectory_validation_requires_exactly_one_output():
    operations = type("Operations", (), {"upsert_operations": []})()

    issues = _trajectory_validation_issues(operations)

    assert any(
        issue.reason == "trajectory extraction must produce exactly one trajectory"
        for issue in issues
    )


def test_trajectory_success_must_match_direct_evaluation():
    fields = {
        "trajectory_name": "incorrect_success",
        "outcome": "success",
        "retrieval_anchor": "Stage: final; Outcome: success.",
        "experience_effects": '{"positive_ids": [], "negative_ids": [], "weak_ids": []}',
        "content": "# Incorrect success\n- Outcome: success\n",
    }
    evidence_sources = {
        "items": [
            {
                "source": "rollout_evaluation",
                "direct": True,
                "passed": False,
                "score": 0.8,
            }
        ]
    }

    issues = _trajectory_operation_validation_issues(
        "incorrect_success",
        fields,
        evidence_sources=evidence_sources,
    )

    assert any(
        issue.reason == "trajectory outcome disagrees with direct evaluation" for issue in issues
    )


def test_trajectory_anchor_allows_trailing_semicolon_after_outcome():
    fields = {
        "trajectory_name": "partial_run",
        "outcome": "partial",
        "retrieval_anchor": (
            "Stage: execution; Boundary: final_write; Capability: spreadsheet; "
            "Target: report; Outcome: partial;"
        ),
        "experience_effects": '{"positive_ids": [], "negative_ids": [], "weak_ids": []}',
        "content": "# Partial run\n- Outcome: partial\n",
    }

    issues = _trajectory_operation_validation_issues("partial_run", fields)

    assert not any(
        issue.reason == "trajectory retrieval_anchor has invalid structure" for issue in issues
    )


class FakeVikingFS:
    def __init__(self, files: dict[str, str]):
        self.files = files
        # Older component tests use an in-memory policy snapshot as storage.
        # Production VikingFS never enables this compatibility escape hatch.
        self._allow_policy_snapshot_fallback = True
        self.rm_lock_handles = []
        self.write_lock_handles = []

    async def ls(self, uri: str, output: str = "original", ctx=None, **kwargs):
        del kwargs
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

    async def write_file(
        self,
        uri: str,
        content: str,
        ctx=None,
        lock_handle=None,
        lease_ref=None,
    ):
        self.write_lock_handles.append((uri, lease_ref if lease_ref is not None else lock_handle))
        self.files[uri] = content

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        ctx=None,
        lock_handle=None,
        lease_ref=None,
    ):
        del recursive, ctx
        self.rm_lock_handles.append(lease_ref if lease_ref is not None else lock_handle)
        self.files.pop(uri, None)
        return {"estimated_deleted_count": 1}


class _RecordingPathLock:
    def __init__(self, events: list[tuple[str, Any]] | None = None):
        self.acquired_paths: list[list[str]] = []
        self.released: list[Any] = []
        self.events = events if events is not None else []

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=None):
        del timeout_secs
        self.acquired_paths.append(list(paths))
        lease = {"lease_ref": f"apply-{len(self.acquired_paths)}"}
        self.events.append(("acquire", lease))
        return lease

    async def pathlock_release(self, lease):
        self.released.append(lease)
        self.events.append(("release", lease))


class LockedFakeVikingFS(FakeVikingFS):
    def __init__(self, files: dict[str, str]):
        super().__init__(files)
        self.events: list[tuple[str, Any]] = []
        self._async_agfs = _RecordingPathLock(self.events)
        self.read_counts: dict[str, int] = {}
        self.vector_deletes: list[list[str]] = []

    def _uri_to_path(self, uri: str, ctx=None):
        del ctx
        return "/" + uri.removeprefix("viking://")

    async def read_file(self, uri: str, ctx=None):
        self.read_counts[uri] = self.read_counts.get(uri, 0) + 1
        return await super().read_file(uri, ctx=ctx)

    async def _delete_from_vector_store(self, uris, ctx=None):
        del ctx
        self.vector_deletes.append(list(uris))
        self.events.append(("vector_delete", list(uris)))

    async def write_file(self, uri: str, content: str, ctx=None, **kwargs):
        self.events.append(("write", uri))
        return await super().write_file(uri, content, ctx=ctx, **kwargs)


class FakeVikingDB:
    def __init__(self):
        self.embedding_messages = []

    async def enqueue_embedding_msg(self, embedding_msg):
        self.embedding_messages.append(embedding_msg)
        return True


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
                metadata={"trigger_code": DEFAULT_TRIGGER_CODE},
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
    trigger_code: str = DEFAULT_TRIGGER_CODE,
) -> MemoryFile:
    fields: dict[str, Any] = {
        "memory_type": "experiences",
        "status": status,
        "trigger_code": trigger_code,
        **_experience_fields(
            name,
            situation=content,
            reminder=content,
            procedure=content,
            anti_pattern=content,
        ),
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
    links: list[StoredLink] | None = None,
    confidence: float = 0.8,
    metadata: dict[str, Any] | None = None,
    trigger_code: str = DEFAULT_TRIGGER_CODE,
) -> PatchSemanticGradient:
    return PatchSemanticGradient(
        before_file=(
            _memory_file(
                name=name,
                uri=uri,
                content=before,
                version=base_version,
                trigger_code=trigger_code,
            )
            if before is not None
            else None
        ),
        after_file=_memory_file(
            name=name,
            uri=uri,
            content=after,
            version=base_version,
            trigger_code=trigger_code,
        ),
        base_version=base_version,
        rationale=rationale,
        links=links
        or [
            StoredLink(
                from_uri=uri or "",
                to_uri="viking://user/u/memories/trajectories/traj1.md",
                link_type="derived_from",
                weight=1.0,
            )
        ],
        confidence=confidence,
        metadata=metadata or {},
    )


def _plan_from_gradient(gradient: PatchSemanticGradient) -> PolicyUpdatePlan:
    return PolicyUpdatePlan(
        items=[
            _plan_item_from_gradient(gradient),
        ]
    )


def test_plan_quality_review_skips_unchanged_single_candidate():
    gradient = _patch_gradient(before=None, after="candidate body")

    metadata = _plan_quality_review_metadata(
        memory_type="experiences",
        before_content=None,
        after_content="candidate body",
        links=gradient.links,
        gradients=[gradient],
    )

    assert metadata == {
        "plan_quality_review_required": False,
        "plan_quality_review_reason": "single_candidate_unchanged",
    }


def test_plan_quality_review_checks_existing_update_and_multi_source_merge():
    first = _patch_gradient(before="old", after="first candidate")
    second = _patch_gradient(
        name="second",
        uri="viking://user/u/memories/experiences/second.md",
        before=None,
        after="second candidate",
        links=[
            StoredLink(
                from_uri="viking://user/u/memories/experiences/second.md",
                to_uri="viking://user/u/memories/trajectories/traj2.md",
                link_type="derived_from",
                weight=1.0,
            )
        ],
    )

    update = _plan_quality_review_metadata(
        memory_type="experiences",
        before_content="old",
        after_content="first candidate",
        links=first.links,
        gradients=[first],
    )
    merged = _plan_quality_review_metadata(
        memory_type="experiences",
        before_content=None,
        after_content="merged candidate",
        links=[*first.links, *second.links],
        gradients=[first, second],
    )

    assert update["plan_quality_review_required"] is True
    assert update["plan_quality_review_reason"] == "existing_experience_changed"
    assert merged["plan_quality_review_required"] is True
    assert merged["plan_quality_review_reason"] == "multiple_sources_merged"


@pytest.mark.asyncio
async def test_split_experiences_keep_single_trajectory_provenance():
    from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations

    policy_set = _experience_set()
    trajectory_uri = "viking://user/u/memories/trajectories/literature_review.md"
    gradient = _patch_gradient(
        name="repair_literature_review",
        uri=f"{policy_set.root_uri}/repair_literature_review.md",
        before=None,
        links=[
            StoredLink(
                from_uri=f"{policy_set.root_uri}/repair_literature_review.md",
                to_uri=trajectory_uri,
                link_type="derived_from",
                weight=1.0,
            )
        ],
    )
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields=_experience_fields(
                    name,
                    situation=name,
                    reminder="R",
                    procedure="P",
                    anti_pattern="A",
                ),
                memory_type="experiences",
                uris=[f"{policy_set.root_uri}/{name}.md"],
            )
            for name in ("repair_definition", "repair_measurement")
        ],
        delete_file_contents=[],
        errors=[],
    )

    items = await _operations_to_plan_items(
        operations=operations,
        gradients=[gradient],
        policy_set=policy_set,
        memory_type="experiences",
        schema=PatchMergePolicyOptimizer()._get_schema(),
    )

    assert len(items) == 2
    assert all(
        any(
            link.link_type == "derived_from" and link.to_uri == trajectory_uri
            for link in item.links
        )
        for item in items
    )


@pytest.mark.asyncio
async def test_experience_delete_operation_is_preserved_as_archived():
    from openviking.session.memory.dataclass import ResolvedOperations

    policy_set = _experience_set()
    old_file = _memory_file(
        name="booking_duplicate_handling",
        uri=policy_set.policies[0].uri,
        content="content",
        status="promoted",
    )
    operations = ResolvedOperations(
        upsert_operations=[],
        delete_file_contents=[old_file],
        errors=[],
    )

    items = await _operations_to_plan_items(
        operations=operations,
        gradients=[_patch_gradient()],
        policy_set=policy_set,
        memory_type="experiences",
        schema=PatchMergePolicyOptimizer()._get_schema(),
    )

    assert len(items) == 1
    item = items[0]
    assert item.kind == "upsert"
    assert item.after_content == "content"
    assert item.metadata["merge_memory_fields"]["status"] == "archived"
    assert item.metadata["merge_memory_fields"]["promotion_reason"] == "superseded_or_obsolete"
    applied = await DryRunPolicyUpdater().apply(
        PolicyUpdatePlan(items=items),
        policy_set,
    )
    archived = applied.updated_policy_set.policies[0]
    assert archived.status == "archived"
    assert archived.metadata["status"] == "archived"


def _plan_item_from_gradient(gradient: PatchSemanticGradient):
    from openviking.session.train import PolicyPlanItem

    return PolicyPlanItem(
        kind="upsert",
        memory_type="experiences",
        target_name=gradient.target_name,
        target_uri=gradient.target_uri,
        before_content=(
            gradient.before_file.plain_content() if gradient.before_file is not None else None
        ),
        after_content=gradient.after_file.plain_content(),
        base_version=gradient.base_version,
        confidence=gradient.confidence,
        links=list(gradient.links),
        metadata={
            "rationale": gradient.rationale,
            "merge_memory_fields": {
                key: value
                for key, value in (gradient.after_file.extra_fields or {}).items()
                if key != "content"
            },
        },
    )


def _delete_plan(*, uri: str, before_content: str = "content") -> PolicyUpdatePlan:
    from openviking.session.train import PolicyPlanItem

    return PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="delete",
                memory_type="experiences",
                target_name="booking_duplicate_handling",
                target_uri=uri,
                before_content=before_content,
                after_content=None,
                base_version=1,
                confidence=0.8,
                links=[
                    StoredLink(
                        from_uri=uri,
                        to_uri="viking://user/u/memories/trajectories/traj1.md",
                        link_type="derived_from",
                        weight=1.0,
                    )
                ],
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
    assert policy.status == "draft"
    assert policy.metadata["status"] == "draft"
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
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri, before="content", after="new content"
    )
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
    assert len(result.updated_policy_set.policies) == 1
    archived = result.updated_policy_set.policies[0]
    assert archived.status == "archived"
    assert archived.content == "content"
    assert archived.version == 2
    assert result.written_uris == []
    assert result.deleted_uris == []
    assert result.metadata["dry_run"] is True
    assert result.metadata["simulated"] is True


@pytest.mark.asyncio
async def test_memory_file_policy_updater_writes_experience_files():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri, before="content", after="new content"
    )
    plan = _plan_from_gradient(gradient)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    assert result.written_uris == [policy_set.policies[0].uri]
    written = fs.files[policy_set.policies[0].uri]
    assert written.startswith("## Situation\nnew content")
    assert '"memory_type": "experiences"' in written
    assert '"experience_name": "booking_duplicate_handling"' in written
    assert '"version": 2' in written


@pytest.mark.asyncio
async def test_memory_file_policy_updater_reuses_transaction_lock_for_experience_writes():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    lock_handle = object()
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri,
        before="content",
        after="new content",
    )
    plan = _plan_from_gradient(gradient)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        fake_request_context(),
        transaction_handle=lock_handle,
    )

    assert result.errors == []
    assert result.written_uris == [policy_set.policies[0].uri]
    assert (policy_set.policies[0].uri, lock_handle) in fs.write_lock_handles


@pytest.mark.asyncio
async def test_memory_file_policy_updater_vectorizes_written_experience_files():
    policy_set = _experience_set()
    fs = FakeVikingFS({})
    vikingdb = FakeVikingDB()
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri, before="content", after="new content"
    )
    plan = _plan_from_gradient(gradient)

    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    result = await MemoryFilePolicyUpdater(viking_fs=fs, vikingdb=vikingdb).apply(
        plan,
        policy_set,
        RequestContext(user=UserIdentifier("default", "u"), role=Role.USER),
    )

    assert result.errors == []
    assert result.written_uris == [policy_set.policies[0].uri]
    assert len(vikingdb.embedding_messages) == 1
    embedding_msg = vikingdb.embedding_messages[0]
    assert embedding_msg.context_data["uri"] == policy_set.policies[0].uri
    assert embedding_msg.context_data["context_type"] == "memory"
    assert "new content" in embedding_msg.message


@pytest.mark.asyncio
async def test_memory_file_policy_updater_writes_v2_compatible_source_trajectory_links():
    policy_set = _experience_set()
    exp_uri = policy_set.policies[0].uri
    traj_uri = "viking://user/u/memories/trajectories/booking_duplicate.md"
    fs = FakeVikingFS(
        {
            traj_uri: MemoryFileUtils.write(
                MemoryFile(
                    uri=traj_uri,
                    content="trajectory content",
                    memory_type="trajectories",
                    extra_fields={
                        "memory_type": "trajectories",
                        "trajectory_name": "booking_duplicate",
                    },
                )
            )
        }
    )
    gradient = _patch_gradient(
        uri=exp_uri,
        before="content",
        after="new content",
        links=[
            StoredLink(
                from_uri=exp_uri,
                to_uri=traj_uri,
                link_type="derived_from",
                weight=1.0,
            )
        ],
    )
    plan = _plan_from_gradient(gradient)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(plan, policy_set)

    assert result.errors == []
    exp_mf = MemoryFileUtils.read(fs.files[exp_uri], uri=exp_uri)
    assert any(
        link.get("from_uri") == exp_uri
        and link.get("to_uri") == traj_uri
        and link.get("link_type") == "derived_from"
        and link.get("match_text") is None
        and link.get("description") == ""
        for link in exp_mf.links
    )

    traj_mf = MemoryFileUtils.read(fs.files[traj_uri], uri=traj_uri)
    assert any(
        link.get("from_uri") == exp_uri
        and link.get("to_uri") == traj_uri
        and link.get("link_type") == "derived_from"
        and link.get("match_text") is None
        and link.get("description") == ""
        for link in traj_mf.backlinks
    )


@pytest.mark.asyncio
async def test_memory_file_policy_updater_archives_experience_files_with_full_content():
    from openviking.session.memory.memory_type_registry import create_default_registry

    policy_set = _experience_set()
    uri = policy_set.policies[0].uri
    experience_schema = create_default_registry().get("experiences")
    fs = FakeVikingFS(
        {
            uri: MemoryFileUtils.write(
                _memory_file(
                    name="booking_duplicate_handling",
                    uri=uri,
                    content="content",
                    status="promoted",
                ),
                content_template=experience_schema.content_template,
                persist_content=False,
            )
        }
    )
    original_content = MemoryFileUtils.read(fs.files[uri], uri=uri).plain_content()
    plan = _delete_plan(uri=uri)
    lock_handle = object()

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        transaction_handle=lock_handle,
    )

    assert result.errors == []
    assert result.written_uris == [uri]
    assert result.deleted_uris == []
    assert result.updated_policy_set.policies[0].status == "archived"
    archived = MemoryFileUtils.read(fs.files[uri], uri=uri)
    assert archived.plain_content() == original_content
    assert archived.extra_fields["status"] == "archived"
    assert archived.extra_fields["version"] == 2
    assert fs.rm_lock_handles == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot_has_backlink", "expected_acquisitions"),
    [(True, 1), (False, 2)],
)
async def test_memory_file_policy_updater_archives_and_unlinks_case_under_stable_short_lease(
    snapshot_has_backlink,
    expected_acquisitions,
):
    from openviking.session.memory.memory_type_registry import create_default_registry

    root = "viking://user/u/memories/experiences"
    experience_uri = f"{root}/booking_duplicate_handling.md"
    case_uri = "viking://user/u/memories/cases/duplicate_booking.md"
    link = StoredLink(
        from_uri=case_uri,
        to_uri=experience_uri,
        link_type="related_to",
        weight=1.0,
    ).model_dump()
    experience_file = _memory_file(
        name="booking_duplicate_handling",
        uri=experience_uri,
        content="content",
        status="promoted",
    )
    experience_file.backlinks = [link]
    experience_schema = create_default_registry().get("experiences")
    case_file = MemoryFile(
        uri=case_uri,
        content="case body",
        links=[link],
        memory_type="cases",
        extra_fields={
            "memory_type": "cases",
            "case_name": "duplicate_booking",
            "task_signature": "handle duplicate booking",
            "input": "{}",
            "rubric": "{}",
            "case_status": "promoted",
            "version": 3,
        },
    )
    fs = LockedFakeVikingFS(
        {
            experience_uri: MemoryFileUtils.write(
                experience_file,
                content_template=experience_schema.content_template,
                persist_content=False,
            ),
            case_uri: MemoryFileUtils.write(case_file),
        }
    )
    original_experience_body = MemoryFileUtils.read(
        fs.files[experience_uri], uri=experience_uri
    ).plain_content()
    policy_set = ExperienceSet(
        root_uri=root,
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri=experience_uri,
                version=1,
                status="promoted",
                content="content",
                metadata=dict(experience_file.extra_fields),
                backlinks=[link] if snapshot_has_backlink else [],
            )
        ],
    )

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        _delete_plan(uri=experience_uri),
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    archived = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
    assert archived.plain_content() == original_experience_body
    assert archived.extra_fields["status"] == "archived"
    assert archived.extra_fields["archived_case_uris"] == [case_uri]
    assert all(backlink.get("from_uri") != case_uri for backlink in archived.backlinks)
    updated_case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    assert all(item.get("to_uri") != experience_uri for item in updated_case.links)
    assert fs.vector_deletes == [[experience_uri]]
    assert fs.read_counts == {experience_uri: expected_acquisitions, case_uri: 1}
    assert len(fs._async_agfs.acquired_paths) == expected_acquisitions
    assert fs._async_agfs.acquired_paths[-1] == sorted(
        [
            fs._uri_to_path(case_uri),
            fs._uri_to_path(experience_uri),
        ]
    )
    if not snapshot_has_backlink:
        assert fs._async_agfs.acquired_paths[0] == [fs._uri_to_path(experience_uri)]
    assert len(fs._async_agfs.released) == expected_acquisitions
    lease = fs._async_agfs.released[-1]
    assert {uri for uri, write_lease in fs.write_lock_handles if write_lease is lease} == {
        experience_uri,
        case_uri,
    }
    assert fs.events.index(("release", lease)) < fs.events.index(
        ("vector_delete", [experience_uri])
    )


@pytest.mark.parametrize("replacement_status", ["draft", "degraded", "archived", "promoted"])
async def test_archive_replacement_visibility_preserves_applied_snapshot(
    replacement_status,
):
    from openviking.session.train import PolicyPlanItem

    root = "viking://user/u/memories/experiences"
    old_uri = f"{root}/booking_duplicate_handling.md"
    new_uri = f"{root}/replacement.md"
    case_uri = "viking://user/u/memories/cases/duplicate_booking.md"
    link = StoredLink(
        from_uri=case_uri, to_uri=old_uri, link_type="related_to", weight=1.0
    ).model_dump()
    old_file = _memory_file(
        name="booking_duplicate_handling", uri=old_uri, content="content", status="promoted"
    )
    old_file.backlinks = [link]
    fs = LockedFakeVikingFS(
        {
            old_uri: MemoryFileUtils.write(old_file),
            case_uri: MemoryFileUtils.write(
                MemoryFile(
                    uri=case_uri,
                    memory_type="cases",
                    content="case body",
                    links=[link],
                    extra_fields={"case_name": "duplicate_booking", "version": 1},
                )
            ),
        }
    )
    policy_set = ExperienceSet(
        root_uri=root,
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri=old_uri,
                version=1,
                status="promoted",
                content="content",
                metadata=dict(old_file.extra_fields),
                backlinks=[link],
            )
        ],
    )
    plan = _delete_plan(uri=old_uri)
    plan.items[0].metadata["superseded_by"] = [new_uri]
    plan.items.insert(
        0,
        PolicyPlanItem(
            kind="upsert",
            memory_type="experiences",
            target_name="replacement",
            target_uri=new_uri,
            before_content=None,
            after_content="replacement content",
            metadata={
                "merge_memory_fields": {
                    **_experience_fields("replacement"),
                    "status": replacement_status,
                }
            },
        ),
    )

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan, policy_set, fake_request_context()
    )

    assert not result.errors
    assert {policy.uri: policy.status for policy in result.updated_policy_set.policies} == {
        old_uri: "archived",
        new_uri: replacement_status,
    }
    assert MemoryFileUtils.read(fs.files[old_uri], uri=old_uri).extra_fields["status"] == "archived"
    assert MemoryFileUtils.read(fs.files[new_uri], uri=new_uri).extra_fields["status"] == (
        replacement_status
    )
    expected_links = [{**link, "to_uri": new_uri}] if replacement_status == "promoted" else []
    assert MemoryFileUtils.read(fs.files[case_uri], uri=case_uri).links == expected_links
    assert MemoryFileUtils.read(fs.files[new_uri], uri=new_uri).backlinks == expected_links
    assert set(result.written_uris) >= {old_uri, new_uri, case_uri}
    assert len(fs._async_agfs.acquired_paths) == len(fs._async_agfs.released)


@pytest.mark.parametrize("missing_result", ["empty", "error"])
async def test_archive_replacement_read_failure_is_still_reported(missing_result):
    from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
    from openviking.session.memory.memory_type_registry import create_default_registry
    from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult

    old_uri = "viking://user/u/memories/experiences/old.md"
    replacement_uri = "viking://user/u/memories/experiences/replacement.md"
    case_uri = "viking://user/u/memories/cases/case.md"
    old_link = StoredLink(
        from_uri=case_uri, to_uri=old_uri, link_type="related_to", weight=1.0
    ).model_dump()
    fs = FakeVikingFS(
        {
            case_uri: MemoryFileUtils.write(
                MemoryFile(
                    uri=case_uri,
                    memory_type="cases",
                    links=[old_link],
                    extra_fields={"case_name": "case", "version": 1},
                )
            )
        }
    )
    original_read = fs.read_file

    async def read_file(uri, ctx=None):
        if uri == replacement_uri:
            if missing_result == "error":
                raise OSError("replacement read unavailable")
            return ""
        return await original_read(uri, ctx=ctx)

    fs.read_file = read_file
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type="experiences",
                memory_fields={},
                uris=[old_uri],
                lifecycle_action="archive",
                archive_replacement_uri=replacement_uri,
                archive_case_uris_by_uri={old_uri: [case_uri]},
            )
        ],
        delete_file_contents=[],
        errors=[],
    )
    result = MemoryUpdateResult()
    result.add_archived(old_uri)
    updater = MemoryUpdater(registry=create_default_registry())
    updater._viking_fs = fs

    await updater._unlink_archived_experience_cases(operations, result, fake_request_context())

    assert len(result.errors) == 1
    assert result.errors[0][0] == replacement_uri
    assert isinstance(result.errors[0][1], OSError)
    assert not MemoryFileUtils.read(fs.files[case_uri], uri=case_uri).links
    assert replacement_uri not in fs.files


@pytest.mark.asyncio
async def test_memory_file_policy_updater_rejects_stale_version_before_any_write():
    uri = "viking://user/u/memories/experiences/booking_duplicate_handling.md"
    current_file = _memory_file(
        name="booking_duplicate_handling",
        uri=uri,
        content="content",
        version=8,
        status="promoted",
    )
    fs = LockedFakeVikingFS({uri: MemoryFileUtils.write(current_file)})
    stale_policy_set = ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri=uri,
                version=7,
                status="promoted",
                content="content",
            )
        ],
    )
    plan = _plan_from_gradient(
        _patch_gradient(
            uri=uri,
            before="content",
            after="stale write",
            base_version=7,
        )
    )

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        stale_policy_set,
        fake_request_context(),
    )

    assert result.written_uris == []
    assert result.metadata["version_conflict"] is True
    assert result.metadata["conflicts"] == [
        {
            "uri": uri,
            "expected_version": 7,
            "actual_version": 8,
            "expected_absent": False,
        }
    ]
    assert fs.read_counts == {uri: 1}
    assert fs.write_lock_handles == []
    assert len(fs._async_agfs.acquired_paths) == 1
    assert len(fs._async_agfs.released) == 1


@pytest.mark.asyncio
async def test_updating_archived_content_does_not_rescan_historical_cases():
    from openviking.session.train import PolicyPlanItem

    root = "viking://user/u/memories/experiences"
    uri = f"{root}/retired_rule.md"
    historical_case_uri = "viking://user/u/memories/cases/old_case.md"
    archived_file = MemoryFile(
        uri=uri,
        content="old archived content",
        memory_type="experiences",
        extra_fields={
            "memory_type": "experiences",
            "experience_name": "retired_rule",
            "status": "archived",
            "version": 4,
            "archived_case_uris": [historical_case_uri],
        },
    )
    fs = LockedFakeVikingFS({uri: MemoryFileUtils.write(archived_file)})
    policy_set = ExperienceSet(
        root_uri=root,
        policies=[
            Experience(
                name="retired_rule",
                uri=uri,
                version=4,
                status="archived",
                content="old archived content",
                metadata=dict(archived_file.extra_fields),
            )
        ],
    )
    plan = PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="upsert",
                memory_type="experiences",
                target_name="retired_rule",
                target_uri=uri,
                before_content="old archived content",
                after_content="adjusted archived content",
                base_version=4,
                metadata={
                    "merge_memory_fields": _experience_fields(
                        "retired_rule",
                        reminder="adjusted archived content",
                    )
                },
            )
        ]
    )

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    updated = MemoryFileUtils.read(fs.files[uri], uri=uri)
    assert updated.extra_fields["status"] == "archived"
    assert updated.extra_fields["version"] == 5
    assert fs.read_counts == {uri: 1}
    assert fs.vector_deletes == []
    assert historical_case_uri not in fs.read_counts
    assert fs._async_agfs.acquired_paths == [[fs._uri_to_path(uri)]]


@pytest.mark.asyncio
async def test_repeated_plan_target_keeps_original_ordered_update_behavior():
    policy_set = _experience_set()
    uri = policy_set.policies[0].uri
    current_file = _memory_file(
        name=policy_set.policies[0].name,
        uri=uri,
        content="content",
        version=1,
    )
    fs = LockedFakeVikingFS({uri: MemoryFileUtils.write(current_file)})
    first = _plan_from_gradient(
        _patch_gradient(uri=uri, before="content", after="first update")
    ).items[0]
    second = _plan_from_gradient(
        _patch_gradient(uri=uri, before="content", after="second update")
    ).items[0]

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        PolicyUpdatePlan(items=[first, second]),
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    updated = MemoryFileUtils.read(fs.files[uri], uri=uri)
    assert updated.extra_fields["version"] == 3
    assert "second update" in updated.plain_content()
    assert fs.read_counts[uri] == 2


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
                            memory_fields=_experience_fields(
                                "booking_duplicate_handling",
                                situation="merged content",
                                reminder="merged content",
                                procedure="merged content",
                                anti_pattern="merged content",
                            ),
                            memory_type="experiences",
                            uris=[policy_set.policies[0].uri],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert plan.metadata["optimizer"] == "patch_merge"
    assert plan.items[0].kind == "upsert"
    assert plan.items[0].target_uri == policy_set.policies[0].uri
    assert plan.items[0].before_content == "content"
    assert "## Situation\nmerged content" in plan.items[0].after_content
    assert [link.to_uri for link in plan.items[0].links] == [
        "viking://user/u/memories/trajectories/traj1.md"
    ]
    assert captured["context_provider"].__class__.__name__ == "PatchMergeContextProvider"
    assert captured["context_provider"].get_tools() == []
    assert "Patch 1" in captured["prefetch_messages"][-1]["content"]
    assert "  situation:" in captured["prefetch_messages"][-1]["content"]
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
            links=[
                StoredLink(
                    from_uri=f"{root}/重复预订处理.md",
                    to_uri="viking://user/u/memories/trajectories/traj1.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
            confidence=0.8,
        ),
        _patch_gradient(
            name="处理酒店重复预订",
            uri=f"{root}/处理酒店重复预订.md",
            before=None,
            after="识别有效订单并取消重复订单",
            base_version=None,
            rationale="r2",
            links=[
                StoredLink(
                    from_uri=f"{root}/处理酒店重复预订.md",
                    to_uri="viking://user/u/memories/trajectories/traj2.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
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
                            memory_fields=_experience_fields(
                                "重复预订处理",
                                situation="合并后的重复预订处理经验",
                                reminder="合并后的重复预订处理经验",
                                procedure="合并后的重复预订处理经验",
                                anti_pattern="合并后的重复预订处理经验",
                            ),
                            memory_type="experiences",
                            uris=[f"{root}/重复预订处理.md"],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

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
    assert captured["prefetch_messages"][-1]["content"].count("\nPatch ") == 2
    assert plan.metadata["optimizer"] == "patch_merge"
    assert plan.metadata["patch_gradient_count"] == 2
    assert len(plan.items) == 1
    assert plan.items[0].target_name == "重复预订处理"
    assert [link.to_uri for link in plan.items[0].links] == [
        "viking://user/u/memories/trajectories/traj1.md",
        "viking://user/u/memories/trajectories/traj2.md",
    ]
    assert {link.from_uri for link in plan.items[0].links} == {f"{root}/重复预订处理.md"}


@pytest.mark.asyncio
async def test_patch_merge_policy_optimizer_recovers_source_link_by_unique_trigger_code(
    monkeypatch,
):
    from openviking.session.memory.dataclass import (
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[])
    root = policy_set.root_uri
    flight_trigger = (
        "def should_trigger(ctx):\n"
        '    return ctx.get("candidate_tool") == "update_reservation_flights"\n'
    )
    baggage_trigger = (
        "def should_trigger(ctx):\n"
        '    return ctx.get("candidate_tool") == "update_reservation_baggages"\n'
    )
    gradients = [
        _patch_gradient(
            name="原始航班支付经验",
            uri=f"{root}/原始航班支付经验.md",
            before=None,
            after="航班修改时使用用户确认的 payment_id",
            base_version=None,
            trigger_code=flight_trigger,
            links=[
                StoredLink(
                    from_uri=f"{root}/原始航班支付经验.md",
                    to_uri="viking://user/u/memories/trajectories/traj_flight.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
        ),
        _patch_gradient(
            name="原始行李支付经验",
            uri=f"{root}/原始行李支付经验.md",
            before=None,
            after="行李修改时使用用户确认的 payment_id",
            base_version=None,
            trigger_code=baggage_trigger,
            links=[
                StoredLink(
                    from_uri=f"{root}/原始行李支付经验.md",
                    to_uri="viking://user/u/memories/trajectories/traj_baggage.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
        ),
    ]

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields={
                                **_experience_fields(
                                    "update_flights支付方式验证",
                                    situation="改名后的航班支付经验",
                                ),
                                "trigger_code": flight_trigger,
                            },
                            memory_type="experiences",
                            uris=[f"{root}/update_flights支付方式验证.md"],
                        ),
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields={
                                **_experience_fields(
                                    "update_baggages支付方式验证",
                                    situation="改名后的行李支付经验",
                                ),
                                "trigger_code": baggage_trigger,
                            },
                            memory_type="experiences",
                            uris=[f"{root}/update_baggages支付方式验证.md"],
                        ),
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        gradients,
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    links_by_name = {item.target_name: {link.to_uri for link in item.links} for item in plan.items}
    assert links_by_name == {
        "update_flights支付方式验证": {"viking://user/u/memories/trajectories/traj_flight.md"},
        "update_baggages支付方式验证": {"viking://user/u/memories/trajectories/traj_baggage.md"},
    }


async def test_patch_merge_policy_optimizer_keeps_distinct_output_source_links_scoped(monkeypatch):
    from openviking.session.memory.dataclass import (
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[])
    root = policy_set.root_uri
    gradients = [
        _patch_gradient(
            name="取消资格核验",
            uri=f"{root}/取消资格核验.md",
            before=None,
            after="取消前核验资格",
            base_version=None,
            links=[
                StoredLink(
                    from_uri=f"{root}/取消资格核验.md",
                    to_uri="viking://user/u/memories/trajectories/traj_cancel.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
        ),
        _patch_gradient(
            name="退款总额传达",
            uri=f"{root}/退款总额传达.md",
            before=None,
            after="多笔退款后传达总额",
            base_version=None,
            links=[
                StoredLink(
                    from_uri=f"{root}/退款总额传达.md",
                    to_uri="viking://user/u/memories/trajectories/traj_refund.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
        ),
    ]

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields=_experience_fields(
                                "取消资格核验",
                                situation="取消前核验资格",
                            ),
                            memory_type="experiences",
                            uris=[f"{root}/取消资格核验.md"],
                        ),
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields=_experience_fields(
                                "退款总额传达",
                                situation="多笔退款后传达总额",
                            ),
                            memory_type="experiences",
                            uris=[f"{root}/退款总额传达.md"],
                        ),
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        gradients,
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    links_by_name = {item.target_name: {link.to_uri for link in item.links} for item in plan.items}
    assert links_by_name == {
        "取消资格核验": {"viking://user/u/memories/trajectories/traj_cancel.md"},
        "退款总额传达": {"viking://user/u/memories/trajectories/traj_refund.md"},
    }


@pytest.mark.asyncio
async def test_patch_merge_policy_optimizer_single_canonical_output_inherits_all_source_links(
    monkeypatch,
):
    from openviking.session.memory.dataclass import (
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[])
    root = policy_set.root_uri
    gradients = [
        _patch_gradient(
            name="重复预订处理",
            uri=f"{root}/重复预订处理.md",
            before=None,
            after="核对订单后只取消重复订单",
            base_version=None,
            links=[
                StoredLink(
                    from_uri=f"{root}/重复预订处理.md",
                    to_uri="viking://user/u/memories/trajectories/traj1.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
        ),
        _patch_gradient(
            name="处理酒店重复预订",
            uri=f"{root}/处理酒店重复预订.md",
            before=None,
            after="识别有效订单并取消重复订单",
            base_version=None,
            links=[
                StoredLink(
                    from_uri=f"{root}/处理酒店重复预订.md",
                    to_uri="viking://user/u/memories/trajectories/traj2.md",
                    link_type="derived_from",
                    weight=1.0,
                )
            ],
        ),
    ]

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[
                        ResolvedOperation(
                            old_memory_file_content=None,
                            memory_fields=_experience_fields(
                                "重复预订处理",
                                situation="合并后的重复预订处理经验",
                            ),
                            memory_type="experiences",
                            uris=[f"{root}/重复预订处理.md"],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        gradients,
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert len(plan.items) == 1
    assert {link.to_uri for link in plan.items[0].links} == {
        "viking://user/u/memories/trajectories/traj1.md",
        "viking://user/u/memories/trajectories/traj2.md",
    }


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
                            memory_fields=_experience_fields(
                                "booking_duplicate_handling",
                                situation="merged update",
                                reminder="merged update",
                                procedure="merged update",
                                anti_pattern="merged update",
                            ),
                            memory_type="experiences",
                            uris=[policy_set.policies[0].uri],
                        )
                    ],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert captured["constructed"] is True
    assert plan.metadata["patch_gradient_count"] == 1
    assert "## Situation\nmerged update" in plan.items[0].after_content


@pytest.mark.asyncio
async def test_patch_merge_instruction_requires_skill_experience_sections(monkeypatch):
    from openviking.session.memory.dataclass import ResolvedOperations

    policy_set = _experience_set()
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri,
        before="content",
        after="## Situation\n- Applies when: test.\n",
    )
    captured = {}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            provider = captured["context_provider"]
            captured["instruction"] = provider.instruction()
            return ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]), []

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        PatchMergePolicyOptimizerContext(request_context=fake_request_context()),
    )

    assert (
        "`situation`, `reminder`, `procedure`, `verification`, `fallback`, `anti_pattern`"
    ) in captured["instruction"]
    assert "storage template adds the Markdown structure" in captured["instruction"]
    assert "canonical value/source-field" in captured["instruction"]


@pytest.mark.asyncio
async def test_patch_merge_post_plan_retry_includes_latest_draft(monkeypatch):
    from openviking.session.memory.dataclass import (
        ResolvedOperation,
        ResolvedOperations,
    )

    policy_set = _experience_set()
    root = policy_set.root_uri
    gradient = _patch_gradient(
        name="bad_experience",
        uri=f"{root}/bad_experience.md",
        before=None,
        after="# bad_experience\n\n## 规则\n1. incomplete production reminder",
    )
    captured = {}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            operations = ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields(
                            "bad_experience",
                            situation="# bad_experience",
                            reminder="",
                            procedure="",
                            anti_pattern="",
                        ),
                        memory_type="experiences",
                        uris=[f"{root}/bad_experience.md"],
                    )
                ],
                delete_file_contents=[],
                errors=[],
            )
            decision = await captured["post_validation_hook"](
                operations,
                0,
                messages=[{"role": "user", "content": "merge"}],
                latest_draft=operations,
            )
            captured["decision"] = decision
            return ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]), []

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient],
        policy_set,
        PatchMergePolicyOptimizerContext(
            request_context=fake_request_context(),
            gate_runner=GateRunner([RejectNamedPlanGate()]),
        ),
    )

    assert captured["decision"].retry is True
    assert captured["decision"].include_latest_draft is True
    assert "Repair the rejected test candidate." in captured["decision"].instruction


@pytest.mark.asyncio
async def test_patch_merge_post_plan_rechecks_retry_and_discards_invalid_final_draft(monkeypatch):
    from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations

    policy_set = _experience_set()
    root = policy_set.root_uri
    gradient = _patch_gradient(
        name="bad_experience",
        uri=f"{root}/bad_experience.md",
        before=None,
        after="# bad_experience\n\n## 规则\n1. incomplete production reminder",
    )
    captured = {}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            operations = ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields(
                            "bad_experience",
                            situation="# bad_experience",
                            reminder="",
                            procedure="",
                            anti_pattern="",
                        ),
                        memory_type="experiences",
                        uris=[f"{root}/bad_experience.md"],
                    )
                ],
                delete_file_contents=[],
                errors=[],
            )
            captured["decision"] = await captured["post_validation_hook"](
                operations,
                3,
                messages=[{"role": "user", "content": "retry"}],
                latest_draft=operations,
            )
            return ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]), []

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    context = PatchMergePolicyOptimizerContext(
        request_context=fake_request_context(),
        gate_runner=GateRunner([RejectNamedPlanGate()]),
    )
    await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        [gradient], policy_set, context
    )

    assert captured["decision"].discard is True
    assert context.metadata["post_validation_retries"][-1]["final_outcome"] == (
        "discarded_after_max_retries"
    )


@pytest.mark.asyncio
async def test_patch_merge_post_plan_retains_valid_sibling_after_retry_exhaustion(monkeypatch):
    from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations

    policy_set = _experience_set()
    root = policy_set.root_uri
    valid_content = (
        "## Situation\n"
        "- Applies when: a requested total must appear in the final answer.\n"
        "- Does not apply when: no total was requested.\n"
        "- Evidence binding: the user's request for the total.\n"
        "- Decision boundary: before final communication.\n\n"
        "## Reminder\n- Include the requested total.\n\n"
        "## Procedure\n- Check the candidate answer and add the total when missing.\n\n"
        "## Anti-pattern\n- Do not omit the requested total.\n"
    )
    gradients = [
        _patch_gradient(
            name="valid_experience",
            uri=f"{root}/valid_experience.md",
            before=None,
            after=valid_content,
        ),
        _patch_gradient(
            name="invalid_experience",
            uri=f"{root}/invalid_experience.md",
            before=None,
            after="# invalid\n",
        ),
    ]
    captured = {}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            operations = ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields("valid_experience"),
                        memory_type="experiences",
                        uris=[f"{root}/valid_experience.md"],
                    ),
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields(
                            "invalid_experience",
                            situation="# invalid",
                            reminder="",
                            procedure="",
                            anti_pattern="",
                        ),
                        memory_type="experiences",
                        uris=[f"{root}/invalid_experience.md"],
                    ),
                ],
                delete_file_contents=[],
                errors=[],
            )
            captured["decision"] = await captured["post_validation_hook"](
                operations,
                3,
                messages=[{"role": "user", "content": "retry"}],
                latest_draft=operations,
            )
            captured["retained_names"] = [
                operation.memory_fields.get("experience_name")
                for operation in operations.upsert_operations
            ]
            return operations, []

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    context = PatchMergePolicyOptimizerContext(
        request_context=fake_request_context(),
        gate_runner=GateRunner([RejectNamedPlanGate()]),
    )
    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        gradients,
        policy_set,
        context,
    )

    assert captured["decision"] is None
    assert captured["retained_names"] == ["valid_experience"]
    assert [item.target_name for item in plan.items] == ["valid_experience"]
    assert plan.metadata["gate_report"] == context.metadata["final_gate_report"]
    event = context.metadata["post_validation_retries"][-1]
    assert event["final_outcome"] == "accepted_valid_subset_after_max_retries"
    assert event["retained_count"] == 1


@pytest.mark.asyncio
async def test_patch_merge_post_plan_carries_valid_sibling_across_retry_drafts(monkeypatch):
    from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations

    policy_set = _experience_set()
    root = policy_set.root_uri
    valid_content = (
        "## Situation\n"
        "- Applies when: a requested total must appear in the final answer.\n"
        "- Does not apply when: no total was requested.\n"
        "- Evidence binding: the user's request for the total.\n"
        "- Decision boundary: before final communication.\n\n"
        "## Reminder\n- Include the requested total.\n\n"
        "## Procedure\n- Check the candidate answer and add the total when missing.\n\n"
        "## Anti-pattern\n- Do not omit the requested total.\n"
    )
    gradients = [
        _patch_gradient(
            name="valid_experience",
            uri=f"{root}/valid_experience.md",
            before=None,
            after=valid_content,
        ),
        _patch_gradient(
            name="invalid_experience",
            uri=f"{root}/invalid_experience.md",
            before=None,
            after="# invalid\n",
        ),
    ]
    captured = {}

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self):
            first = ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields("valid_experience"),
                        memory_type="experiences",
                        uris=[f"{root}/valid_experience.md"],
                    ),
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields(
                            "invalid_experience",
                            situation="# invalid",
                            reminder="",
                            procedure="",
                            anti_pattern="",
                        ),
                        memory_type="experiences",
                        uris=[f"{root}/invalid_experience.md"],
                    ),
                ],
                delete_file_contents=[],
                errors=[],
            )
            first_decision = await captured["post_validation_hook"](
                first,
                0,
                messages=[{"role": "user", "content": "initial"}],
                latest_draft=first,
            )
            assert first_decision.retry is True

            final = ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        old_memory_file_content=None,
                        memory_fields=_experience_fields(
                            "combined_invalid_experience",
                            situation="# still invalid",
                            reminder="",
                            procedure="",
                            anti_pattern="",
                        ),
                        memory_type="experiences",
                        uris=[f"{root}/combined_invalid_experience.md"],
                    )
                ],
                delete_file_contents=[],
                errors=[],
            )
            captured["final_decision"] = await captured["post_validation_hook"](
                final,
                3,
                messages=[{"role": "user", "content": "retry"}],
                latest_draft=final,
            )
            captured["retained_names"] = [
                operation.memory_fields.get("experience_name")
                for operation in final.upsert_operations
            ]
            return final, []

    monkeypatch.setattr(
        "openviking.session.train.components.policy_optimizer.ExtractLoop", FakeExtractLoop
    )

    context = PatchMergePolicyOptimizerContext(
        request_context=fake_request_context(),
        gate_runner=GateRunner([RejectNamedPlanGate()]),
    )
    plan = await PatchMergePolicyOptimizer(viking_fs=FakeVikingFS({}), vlm=object()).plan(
        gradients,
        policy_set,
        context,
    )

    assert captured["final_decision"] is None
    assert captured["retained_names"] == ["valid_experience"]
    assert [item.target_name for item in plan.items] == ["valid_experience"]
    event = context.metadata["post_validation_retries"][-1]
    assert event["final_outcome"] == "accepted_valid_subset_after_max_retries"
    assert event["retained_count"] == 1


def test_experience_memory_schema_is_skill_readable_without_trigger_fields():
    from openviking.session.memory.memory_type_registry import create_default_registry

    schema = create_default_registry().get("experiences")

    assert schema is not None
    fields = {field.name: field for field in schema.fields}
    assert {
        "situation",
        "reminder",
        "procedure",
        "verification",
        "fallback",
        "anti_pattern",
    } <= fields.keys()
    assert "trigger_code" not in fields
    assert "## Situation" in fields["situation"].description
    assert "skill loader" in fields["situation"].description
    assert schema.content_template is not None
    assert "# Experience Trigger" not in schema.content_template


def test_experience_content_template_renders_skill_readable_markdown_only():
    from openviking.session.memory.dataclass import MemoryFile
    from openviking.session.memory.memory_type_registry import create_default_registry
    from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

    schema = create_default_registry().get("experiences")
    fields = _experience_fields(
        "refund_check",
        situation=(
            "- Applies when: refund request.\n"
            "- Does not apply when: no refund.\n"
            "- Source binding: user request and retrieved ticket."
        ),
        reminder="- Check refund eligibility.",
        procedure=(
            "1. Before refunding, read the applicable policy and request facts.\n"
            "2. Apply the eligibility rule to the requested refund."
        ),
        verification=(
            "- Compare the decision with the authoritative policy source.\n"
            "- Reopen the persisted refund record and verify its status."
        ),
        fallback="- If the policy source is unavailable, ask for clarification before changing state.",
        anti_pattern=("- Do not refund without eligibility.\n- Preserve eligible refunds."),
    )
    rendered = MemoryFileUtils.write(
        MemoryFile(
            uri="viking://user/u/memories/experiences/refund.md",
            content="",
            memory_type="experiences",
            extra_fields={
                "memory_type": "experiences",
                **fields,
            },
        ),
        content_template=schema.content_template,
    )

    assert "# Experience Trigger" not in rendered
    assert "```python" not in rendered
    assert "## Situation" in rendered
    assert '"situation":' in rendered
    assert '"verification":' in rendered
    assert '"fallback":' in rendered
    assert '"anti_pattern":' in rendered
    assert '"content":' not in rendered
    parsed = MemoryFileUtils.read(rendered)
    assert parsed.extra_fields["situation"] == fields["situation"]
    assert parsed.extra_fields["verification"] == fields["verification"]
    assert parsed.extra_fields["fallback"] == fields["fallback"]
    assert parsed.extra_fields["anti_pattern"] == fields["anti_pattern"]


@pytest.mark.asyncio
async def test_memory_file_policy_updater_persists_skill_experience_from_merge_fields():
    from openviking.session.train import PolicyPlanItem

    policy_set = _experience_set()
    fs = FakeVikingFS({})
    plan = PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="upsert",
                memory_type="experiences",
                target_name="booking_duplicate_handling",
                target_uri=policy_set.policies[0].uri,
                before_content="content",
                after_content="new content",
                base_version=1,
                metadata={
                    "merge_memory_fields": {
                        **_experience_fields(
                            "booking_duplicate_handling",
                            reminder="- New content.",
                        ),
                    }
                },
            )
        ]
    )

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    written = fs.files[policy_set.policies[0].uri]
    assert '"reminder": "- New content."' in written
    assert '"situation":' in written
    assert '"trigger_code":' not in written
    assert '"content":' not in written
    assert "New content" in written


@pytest.mark.asyncio
async def test_memory_file_policy_updater_preserves_hidden_feedback_stats_on_update():
    policy_set = ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
                version=1,
                status="production",
                content="content",
                metadata={
                    "trigger_code": DEFAULT_TRIGGER_CODE,
                    "feedback_stats": {
                        "schema_version": 1,
                        "injected_count": 3,
                        "positive_count": 1,
                        "negative_count": 1,
                    },
                },
            )
        ],
    )
    fs = FakeVikingFS({})
    gradient = _patch_gradient(
        uri=policy_set.policies[0].uri,
        before="content",
        after="new content",
    )
    plan = _plan_from_gradient(gradient)

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    written = MemoryFileUtils.read(fs.files[policy_set.policies[0].uri])
    assert written.extra_fields["feedback_stats"] == {
        "schema_version": 1,
        "injected_count": 3,
        "positive_count": 1,
        "negative_count": 1,
    }
    assert "trigger_code" not in written.extra_fields


@pytest.mark.asyncio
async def test_memory_file_policy_updater_allows_experience_without_trigger_code():
    from openviking.session.train import PolicyPlanItem

    policy_set = ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="legacy_experience",
                uri="viking://user/u/memories/experiences/legacy_experience.md",
                version=1,
                status="production",
                content="content",
                metadata={},
            )
        ],
    )
    fs = FakeVikingFS({})
    plan = PolicyUpdatePlan(
        items=[
            PolicyPlanItem(
                kind="upsert",
                memory_type="experiences",
                target_name="legacy_experience",
                target_uri=policy_set.policies[0].uri,
                before_content="content",
                after_content="new content",
                base_version=1,
                metadata={},
            )
        ]
    )

    result = await MemoryFilePolicyUpdater(viking_fs=fs).apply(
        plan,
        policy_set,
        fake_request_context(),
    )

    assert result.errors == []
    assert result.written_uris == [policy_set.policies[0].uri]
    assert policy_set.policies[0].uri in fs.files
