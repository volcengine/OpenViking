import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
import yaml

from openviking.server.identity import RequestContext, Role
from openviking.server.resource_ingest import AddResourceRequest
from openviking.service.rule_jev import RuleJevScore
from openviking.service.rule_service import (
    RULE_SCHEMA,
    SCENARIO_SCHEMA,
    RuleCompilePlan,
    RuleCompileRequest,
    RuleMutation,
    RuleRecord,
    RuleScenarioRef,
    RuleSearchRequest,
    RuleService,
    StoredRule,
    StoredScenario,
    _build_material_batches,
    _render_markdown,
    _render_rules,
)
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import (
    TaskRecord,
    TaskStatus,
    TaskTracker,
    set_task_tracker,
)
from openviking.service.task_work_index import bind_task_context, get_task_context
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    ProcessingError,
    VLMFailedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.jev_config import JevConfig
from openviking_cli.utils.config.rules_config import RulesConfig
from openviking_cli.utils.config.vlm_config import VLMConfig
from tests.test_task_tracker import _FakeAgfs


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)


def _record(name: str = "confirm-delete") -> RuleRecord:
    return RuleRecord(
        name=name,
        description="Confirm before deleting files.",
        content="Ask for confirmation before deleting files.",
        applies_when="when a task requires deleting files",
        exception="when the user already explicitly confirmed this deletion",
        body="# Confirm deletion\n\nNever delete files before confirmation.",
    )


def test_rule_record_is_an_implicit_user_constraint():
    record = _record()
    dumped = record.model_dump()

    assert record.confidence == "user"
    assert {
        "type",
        "domain",
        "scope",
        "scope_anchor",
        "condition",
    }.isdisjoint(dumped)
    assert record.content == "Ask for confirmation before deleting files."
    assert record.exception == "when the user already explicitly confirmed this deletion"
    assert RuleRecord.model_validate({**dumped, "confidence": "high"}).confidence == "high"


@pytest.mark.parametrize("applies_when", ["none", "(none)", "[]"])
def test_rule_record_rejects_placeholder_applies_when(applies_when):
    with pytest.raises(ValueError, match="applies_when"):
        RuleRecord.model_validate({**_record().model_dump(), "applies_when": applies_when})


@pytest.mark.parametrize("applies_when", ["", " \n"])
def test_rule_record_accepts_empty_applies_when(applies_when):
    record = RuleRecord.model_validate({**_record().model_dump(), "applies_when": applies_when})
    assert record.applies_when == ""
    assert record.exception == _record().exception
    dumped = record.model_dump()
    del dumped["applies_when"]
    assert RuleRecord.model_validate(dumped).applies_when == ""


def test_compile_prompt_does_not_expose_memory_preference_fields():
    removed_comparison_field = "_".join(("nearest", "existing"))
    prompt = RuleService._render_compile_prompt(
        instruction="Always confirm deletion.",
        source_text="",
        scenario_data=[],
        rule_data=[],
    )

    assert '"confidence": "user"' in prompt
    assert '"content": "complete actionable constraint"' in prompt
    assert '"exception": "explicit exceptions or empty"' in prompt
    assert '"condition":' not in prompt
    assert '"type": "preference' not in prompt
    assert '"domain":' not in prompt
    assert '"scope":' not in prompt
    assert "Preference-like statements" in prompt
    assert "REPLACE" in prompt
    assert "Infer the single semantic target" in prompt
    assert "use UNCERTAIN instead of" in prompt
    assert "conditional branches into one\n  RULE record" in prompt
    assert "Only NEW may include a non-null `scenario`" in prompt
    assert "SUPERSEDE" not in prompt
    assert "SPLIT" not in prompt
    assert '"children":' not in prompt
    assert '"applicability_evidence":' in prompt
    assert f'"{removed_comparison_field}":' not in prompt
    assert "internally compare it" in prompt
    assert '"carried_over":' in prompt
    assert "Prefer a faithful, non-empty" in prompt
    assert "Never invent a trigger" in prompt
    assert "A rule is an explicit durable constraint" in prompt
    assert "RULE records have higher priority than Memories" in prompt
    assert "Empty means the rule applies in every situation" in prompt
    assert "UPDATE must target exactly one active or paused RULE record" in prompt
    assert "Keep its atomic_id, lifecycle status" in prompt
    assert "An UPDATE may target additional" not in prompt
    assert "Archived\n  RULE records are logically deleted" in prompt
    assert "When deletion intent is absent or ambiguous, use PAUSE rather than ARCHIVE" in prompt
    assert "cannot be restored through compilation" in prompt
    assert "An active RULE record cannot be restored" in prompt
    assert "policy" not in prompt.casefold()


def test_rule_mutations_are_flat_and_update_and_replace_have_one_target():
    removed_comparison_field = "_".join(("nearest", "existing"))
    replacement = _record("replacement")

    mutation = RuleMutation(
        operation="REPLACE",
        target_ids=["old-rule"],
        record=replacement,
    )

    assert mutation.operation == "REPLACE"
    assert "children" not in mutation.model_dump()
    assert RuleMutation(operation="UNCERTAIN").operation == "UNCERTAIN"
    legacy_uncertain_operation = "_".join(("NEEDS", "USER"))
    with pytest.raises(ValueError, match="unsupported rule operation"):
        RuleMutation(operation=legacy_uncertain_operation)
    with pytest.raises(ValueError, match="unsupported rule operation"):
        RuleMutation(operation="SUPERSEDE", target_ids=["old-rule"], record=replacement)
    with pytest.raises(ValueError, match="unsupported rule operation"):
        RuleMutation(operation="SPLIT")
    with pytest.raises(ValueError, match="exactly one target_id"):
        RuleMutation(
            operation="REPLACE",
            target_ids=["old-rule", "other-rule"],
            record=replacement,
        )
    with pytest.raises(ValueError, match="UPDATE requires exactly one target_id"):
        RuleMutation(
            operation="UPDATE",
            target_ids=["old-rule", "other-rule"],
            record=_record("updated"),
        )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        RuleMutation.model_validate(
            {
                "operation": "NOOP",
                "target_ids": ["old-rule"],
                "children": [],
            }
        )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        RuleMutation.model_validate(
            {
                "operation": "UNCERTAIN",
                removed_comparison_field: None,
            }
        )


@pytest.mark.asyncio
async def test_load_rule_accepts_missing_applies_when():
    uri = "viking://user/alice/memories/rules/file-operations/invalid.md"
    metadata = {
        "schema": RULE_SCHEMA,
        "atomic_id": "invalid",
        "revision": 1,
        "status": "active",
        "scenario_id": "file-operations",
        "source_uris": [],
        "evidence": [],
        "updated_at": "",
        "name": "invalid",
        "description": "Invalid rule without applicability.",
        "confidence": "high",
        "content": "Confirm before deleting files.",
    }
    markdown = f"---\n{yaml.safe_dump(metadata, sort_keys=False)}---\n"
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(read=AsyncMock(return_value=markdown)),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )

    rule = await service._load_rule(uri, _ctx())
    assert rule.record.applies_when == ""


def _stored_rule(
    atomic_id: str,
    *,
    scenario_id: str = "file-operations",
    status: Literal["active", "paused", "archived"] = "active",
    archived_at: str | None = None,
) -> StoredRule:
    record = _record(atomic_id)
    uri = f"viking://user/alice/memories/rules/{scenario_id}/{atomic_id}.md"
    return StoredRule(
        schema=RULE_SCHEMA,
        atomic_id=atomic_id,
        revision=1,
        status=status,
        scenario_id=scenario_id,
        source_uris=[],
        evidence=["delete files"],
        archived_at=archived_at,
        record=record,
        uri=uri,
        content=record.body,
    )


class _FS:
    def __init__(self):
        self.batch = None
        self.batch_task_context = None
        self.documents = {}

    async def glob(self, pattern, ctx, uri):
        return {"matches": [path for path in self.documents if path.startswith(f"{uri}/")]}

    async def read(self, uri, ctx):
        if uri not in self.documents:
            raise NotFoundError(uri, "file")
        return self.documents[uri]

    async def batch_write(self, **kwargs):
        self.batch_task_context = get_task_context()
        self.batch = kwargs
        return {
            "created": [operation["uri"] for operation in kwargs["operations"]],
            "updated": [],
            "unchanged": [],
        }


class _CleanupFS:
    def __init__(self, documents: dict[str, str]):
        self.documents = documents
        self.removed: list[str] = []

    async def glob(self, pattern, ctx, uri):
        assert pattern == "*/*.md"
        return {"matches": sorted(path for path in self.documents if path.startswith(f"{uri}/"))}

    async def read(self, uri, ctx):
        if uri not in self.documents:
            raise NotFoundError(uri, "file")
        return self.documents[uri]

    async def batch_write(self, **kwargs):
        for operation in kwargs["operations"]:
            self.documents[operation["uri"]] = operation["content"]
        return {"created": [], "updated": list(self.documents), "unchanged": []}

    async def rm(self, uri, ctx, wait):
        assert wait is True
        self.removed.append(uri)
        self.documents.pop(uri, None)


@pytest.mark.asyncio
async def test_rule_compiler_prefers_dedicated_vlm_config():
    compiler = VLMConfig(provider="litellm", model="rule-compiler")
    resolver = SimpleNamespace(get_vlm=AsyncMock())
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=resolver,
        rules_config=RulesConfig(compiler=compiler),
    )

    assert await service._compiler_vlm(_ctx()) is compiler
    resolver.get_vlm.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_rule_model_configs_use_account_default_vlm():
    default_vlm = object()
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=default_vlm))
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=resolver,
        rules_config=RulesConfig(compiler=VLMConfig()),
    )

    assert await service._compiler_vlm(_ctx()) is default_vlm
    assert resolver.get_vlm.await_count == 1


@pytest.mark.parametrize(
    "rules_config,vlm,expected",
    [
        (
            RulesConfig(compiler_context_window=32768),
            SimpleNamespace(model="custom", max_tokens=4096),
            27034,
        ),
        (
            RulesConfig(),
            SimpleNamespace(
                model="custom",
                max_tokens=2048,
                extra_request_body={"num_ctx": 12000},
            ),
            9352,
        ),
        (
            RulesConfig(),
            SimpleNamespace(model="gpt-4o-mini", max_tokens=None),
            105216,
        ),
        (
            RulesConfig(),
            SimpleNamespace(model="unknown-custom", max_tokens=None),
            11469,
        ),
    ],
)
def test_compiler_prompt_limit_uses_context_metadata(rules_config, vlm, expected):
    service = RuleService()
    service._rules_config = rules_config

    assert service._compiler_prompt_token_limit(vlm) == expected


@pytest.mark.asyncio
async def test_jev_rule_matcher_scores_independent_applicability(monkeypatch):
    rules = [
        _stored_rule("rule-a").model_copy(
            update={"content": "---\ncondition: legacy hidden value\n---\n"}
        ),
        _stored_rule("rule-b"),
        _stored_rule("rule-c"),
        _stored_rule("no-exception").model_copy(
            update={"record": _record("no-exception").model_copy(update={"exception": "(none)"})}
        ),
    ]

    class _JevClient:
        def __init__(self):
            self.closed = False
            self.calls = []

        def judge_rules(self, task_context, inputs):
            assert task_context == "delete generated files"
            assert len(inputs) == 4
            self.calls.append((task_context, inputs))
            assert inputs[0].name == "rule-a"
            assert inputs[0].content == "Ask for confirmation before deleting files."
            assert "legacy hidden value" not in inputs[0].content
            assert inputs[0].applies_when == "when a task requires deleting files"
            assert inputs[0].exception == (
                "when the user already explicitly confirmed this deletion"
            )
            assert inputs[3].applies_when == "when a task requires deleting files"
            assert inputs[3].exception == ""
            return [
                RuleJevScore(applies_when=0.9, exception=0.1),
                RuleJevScore(applies_when=0.8, exception=0.7),
                RuleJevScore(applies_when=0.6, exception=0.8),
                RuleJevScore(applies_when=0.95, exception=0.0),
            ]

        def close(self):
            self.closed = True

    client = _JevClient()

    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock()),
        rules_config=RulesConfig(
            jev_rule_matcher=JevConfig(provider="typesafe", api_key="key"),
        ),
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.RuleJevClient",
        SimpleNamespace(from_config=lambda config: client),
    )

    scores, backend, warning = await service._score_rules(
        "delete generated files",
        rules,
    )

    assert scores == pytest.approx([0.9, 0.8, 0.0, 0.95])
    assert backend == "jev_noul"
    assert warning is None
    assert client.closed is True
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_jev_global_rule_exception_vetoes_default_applicability(monkeypatch):
    client = SimpleNamespace(
        judge_rules=lambda task, inputs: [
            RuleJevScore(applies_when=1.0, exception=probability)
            for probability in (0.9, 0.4, 0.65)
        ],
        close=lambda: None,
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.RuleJevClient",
        SimpleNamespace(from_config=lambda config: client),
    )
    rules = [
        _stored_rule(f"global-{index}").model_copy(
            update={"record": _record().model_copy(update={"applies_when": ""})}
        )
        for index in range(3)
    ]
    scores = await RuleService()._score_rules_with_jev(
        "Any task", rules, None, exception_veto_threshold=0.65
    )
    assert scores == [0.0, 1.0, 0.0]


@pytest.mark.asyncio
async def test_jev_rule_matcher_failure_passes_all_candidates(monkeypatch):
    class _FailingJevClient:
        def __init__(self):
            self.closed = False

        def judge_rules(self, task_context, inputs):
            raise RuntimeError("Jev unavailable")

        def close(self):
            self.closed = True

    client = _FailingJevClient()
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock()),
        rules_config=RulesConfig(
            jev_rule_matcher=JevConfig(provider="typesafe", api_key="key"),
        ),
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.RuleJevClient",
        SimpleNamespace(from_config=lambda config: client),
    )

    scores, backend, warning = await service._score_rules(
        "delete generated files",
        [
            _stored_rule("rule-a").model_copy(
                update={"record": _record("rule-a").model_copy(update={"exception": ""})}
            )
        ],
    )

    assert scores == [1.0]
    assert backend == "pass"
    assert "passed without judgment" in warning
    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [None, [], [RuleJevScore(applies_when=float("nan"), exception=0.0)], ["malformed"]],
)
async def test_invalid_jev_rule_responses_pass_all_rules_without_loading_vlm(monkeypatch, response):
    service = RuleService()
    resolver = SimpleNamespace(get_vlm=AsyncMock(side_effect=AssertionError("Unexpected VLM")))
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=resolver,
        rules_config=RulesConfig(
            jev_rule_matcher=JevConfig(provider="typesafe", api_key="key"),
        ),
    )
    client = SimpleNamespace(judge_rules=lambda *_: response, close=lambda: None)
    monkeypatch.setattr(
        "openviking.service.rule_service.RuleJevClient",
        SimpleNamespace(from_config=lambda _: client),
    )
    rules = [_stored_rule("first"), _stored_rule("second")]
    # For a malformed single result, test its contents as well as length validation.
    if response and len(response) == 1:
        response = response * 2
    scores, backend, warning = await service._score_rules("task", rules)
    assert scores == [1.0, 1.0]
    assert backend == "pass"
    assert "passed without judgment" in warning
    resolver.get_vlm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("rules_config", [None, RulesConfig()])
async def test_missing_jev_config_passes_rules_without_reading_vlm_config(rules_config):
    service = RuleService()
    service._rules_config = rules_config
    rules = [_stored_rule(f"rule-{index}") for index in range(45)]
    scores, backend, warning = await service._score_rules("task", rules)
    assert scores == [1.0] * len(rules)
    assert backend == "pass"
    assert "passed without judgment" in warning


@pytest.mark.asyncio
async def test_search_passes_all_loaded_rules_at_threshold_one_when_jev_is_missing(monkeypatch):
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=None,
    )
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id="file-operations",
        name="Files",
        description="File constraints.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="",
    )
    rules = [_stored_rule("first"), _stored_rule("second")]
    monkeypatch.setattr(service, "_recall_scenarios", AsyncMock(return_value=[scenario]))
    monkeypatch.setattr(service, "_load_rules_for_scenarios", AsyncMock(return_value=rules))
    result = await service.search_rules(
        RuleSearchRequest(task_context="delete files", rule_probability_threshold=1.0),
        ctx=_ctx(),
    )
    assert {rule["atomic_id"] for rule in result["rules"]} == {"first", "second"}
    assert all(rule["probability"] == 1.0 for rule in result["rules"])
    assert result["judge"] == "pass"
    assert "passed without judgment" in result["warning"]
    assert result["scenario_selection"]["passed_scenario_ids"] == ["file-operations"]
    assert result["delivery"]["complete"] is True


@pytest.mark.asyncio
async def test_jev_rule_client_initialization_failure_passes_rules(monkeypatch):
    service = RuleService()
    service._rules_config = RulesConfig(
        jev_rule_matcher=JevConfig(provider="typesafe", api_key="key"),
    )

    def fail_initialization(_config):
        raise RuntimeError("init failed")

    monkeypatch.setattr(
        "openviking.service.rule_service.RuleJevClient",
        SimpleNamespace(from_config=fail_initialization),
    )
    scores, backend, warning = await service._score_rules("task", [_stored_rule("first")])
    assert scores == [1.0] and backend == "pass"
    assert "passed without judgment" in warning


@pytest.mark.asyncio
@pytest.mark.parametrize("initialization", ["missing", "unavailable", "raises"])
async def test_jev_scenario_initialization_passes_every_candidate(monkeypatch, initialization):
    service = RuleService()
    service._rules_config = RulesConfig(
        jev_rule_matcher=(
            None if initialization == "missing" else JevConfig(provider="typesafe", api_key="key")
        )
    )

    def factory(*_args, **_kwargs):
        if initialization == "raises":
            raise RuntimeError("init failed")
        return None

    monkeypatch.setattr(
        "openviking.service.rule_service.ScenarioJevClient",
        SimpleNamespace(from_config=factory),
    )
    scenarios = [
        StoredScenario(
            schema=SCENARIO_SCHEMA,
            scenario_id="example",
            name="Example",
            description="Example.",
            uri="viking://user/alice/memories/rules/example/abstract.md",
            content="",
        )
    ]
    passed, report = await service._filter_unrelated_scenarios_with_jev("task", scenarios)
    assert passed == scenarios
    assert report["skipped_scenario_ids"] == []
    assert all(item["decision"] == "pass" for item in report["decisions"])


@pytest.mark.asyncio
async def test_jev_scenario_filter_skips_only_high_confidence_completely_unrelated(monkeypatch):
    class _JevClient:
        def __init__(self):
            self.closed = False
            self.call = None

        def judge_scenarios(self, task_context, scenarios):
            self.call = (task_context, scenarios)
            return [0.95, 0.4, 0.89]

        def close(self):
            self.closed = True

    client = _JevClient()
    service = RuleService()
    service._rules_config = RulesConfig(
        jev_rule_matcher=JevConfig(provider="typesafe", api_key="key"),
        scenario_irrelevance_threshold=0.9,
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.ScenarioJevClient",
        SimpleNamespace(from_config=lambda config: client),
    )
    scenarios = [
        StoredScenario(
            schema=SCENARIO_SCHEMA,
            scenario_id=scenario_id,
            name=scenario_id,
            description=f"Scenario for {scenario_id}",
            uri=f"viking://user/alice/memories/rules/{scenario_id}/abstract.md",
            content=f"# {scenario_id}\n\nScenario for {scenario_id}",
        )
        for scenario_id in ("unrelated", "relevant", "uncertain")
    ]

    passed, report = await service._filter_unrelated_scenarios_with_jev("current task", scenarios)

    assert [scenario.scenario_id for scenario in passed] == ["relevant", "uncertain"]
    assert report["skipped_scenario_ids"] == ["unrelated"]
    assert report["passed_scenario_ids"] == ["relevant", "uncertain"]
    assert report["irrelevance_threshold"] == 0.9
    assert client.call[0] == "current task"
    assert len(client.call[1]) == 3
    assert all("Scenario for" in scenario for scenario in client.call[1])
    assert client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, [], 0.9, [float("nan")], [True]])
async def test_jev_scenario_filter_passes_all_scenarios_when_judgment_fails(monkeypatch, response):
    class _FailingJevClient:
        def judge_scenarios(self, task_context, scenarios):
            return response

        def close(self):
            pass

    service = RuleService()
    service._rules_config = RulesConfig(
        jev_rule_matcher=JevConfig(provider="typesafe", api_key="key"),
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.ScenarioJevClient",
        SimpleNamespace(from_config=lambda config: _FailingJevClient()),
    )
    scenarios = [
        StoredScenario(
            schema=SCENARIO_SCHEMA,
            scenario_id="uncertain",
            name="Uncertain",
            description="Possibly relevant.",
            uri="viking://user/alice/memories/rules/uncertain/abstract.md",
            content="# Uncertain\n\nPossibly relevant.",
        )
    ]

    passed, report = await service._filter_unrelated_scenarios_with_jev("current task", scenarios)

    assert passed == scenarios
    assert report["skipped_scenario_ids"] == []
    assert report["decisions"][0]["reason"] in {
        "jev_unavailable_or_invalid",
        "invalid_jev_score",
    }


@pytest.mark.asyncio
async def test_apply_new_rule_writes_markdown_item_and_scenario():
    fs = _FS()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(),
                scenario=RuleScenarioRef(
                    scenario_id="destructive-file-operations",
                    name="Destructive file operations",
                    description="Rules for operations that remove or overwrite files.",
                ),
                evidence_spans=["confirm before deleting files"],
            )
        ]
    )

    with bind_task_context(
        "rul_123",
        "acme",
        "alice",
        enqueued_work=[],
        work_id="inline:rule-compile",
    ):
        result = await service._apply_plan(
            plan,
            existing_scenarios=[],
            existing_rules=[],
            source_uris=["viking://user/alice/resources/rules"],
            ctx=_ctx(),
        )

    assert len(result["written_uris"]) == 2
    assert fs.batch_task_context is not None
    assert fs.batch_task_context.task_id == "rul_123"
    assert fs.batch["root_uri"] == "viking://user/alice/memories/rules"
    assert fs.batch["wait"] is False
    by_uri = {operation["uri"]: operation["content"] for operation in fs.batch["operations"]}
    rule_uri = "viking://user/alice/memories/rules/destructive-file-operations/confirm-delete.md"
    scenario_uri = "viking://user/alice/memories/rules/destructive-file-operations/abstract.md"
    rule_frontmatter = yaml.safe_load(by_uri[rule_uri].split("---", 2)[1])
    scenario_frontmatter = yaml.safe_load(by_uri[scenario_uri].split("---", 2)[1])
    assert rule_frontmatter["schema"] == RULE_SCHEMA
    assert rule_frontmatter["status"] == "active"
    assert rule_frontmatter["source_uris"] == ["viking://user/alice/resources/rules"]
    assert rule_frontmatter["confidence"] == "user"
    assert rule_frontmatter["content"] == "Ask for confirmation before deleting files."
    assert {"type", "domain", "scope", "scope_anchor"}.isdisjoint(rule_frontmatter)
    assert scenario_frontmatter["schema"] == SCENARIO_SCHEMA
    assert "rule_uris" not in scenario_frontmatter


class _ContextCapturingFS(_FS):
    """Mirror batch_write's contract: the file-write work owns its own Task.

    The real coordinator reads the ambient Task context to attribute the
    ContentWrite root work. Compile must detach its own Task context first so the
    file write cannot hijack and overwrite the compile Task's result.
    """


@pytest.mark.asyncio
async def test_compile_keeps_result_and_detaches_write_task_context(monkeypatch):
    fs = _ContextCapturingFS()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    monkeypatch.setattr(
        service,
        "_collect_sources",
        AsyncMock(return_value=("confirm before deleting files", ["viking://user/alice/resources/rules"])),
    )
    monkeypatch.setattr(service, "_compiler_vlm", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "_compiler_prompt_token_limit", lambda _: 8_000)
    monkeypatch.setattr(
        service,
        "_find_relevant_rules",
        AsyncMock(return_value=SimpleNamespace(scenarios=[], selected=[])),
    )
    plan = RuleCompilePlan(
        reason="Captured a durable deletion constraint.",
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(),
                scenario=RuleScenarioRef(
                    scenario_id="destructive-file-operations",
                    name="Destructive file operations",
                    description="Rules for operations that remove or overwrite files.",
                ),
                evidence_spans=["confirm before deleting files"],
            )
        ],
    )
    monkeypatch.setattr(service, "_build_compile_plan", AsyncMock(return_value=plan))

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    ctx = _ctx()
    try:
        await service.create_compile(
            RuleCompileRequest(instruction="Confirm before deleting files."),
            ctx=ctx,
        )
        while service._running:
            await asyncio.gather(*tuple(service._running))

        tasks = await tracker.list_tasks(account_id=ctx.account_id, user_id=ctx.user.user_id)
        compile_tasks = [task for task in tasks if task.task_type == "rule_compile"]
        assert len(compile_tasks) == 1
        compile_task = compile_tasks[0]

        # The compile Task stays COMPLETED and retains the compile result rather
        # than being overwritten by the file-write result.
        assert compile_task.status is TaskStatus.COMPLETED
        assert compile_task.result is not None
        assert compile_task.result["plan_reason"] == "Captured a durable deletion constraint."
        assert len(compile_task.result["operations"]) == 1
        assert compile_task.result["source_uris"] == ["viking://user/alice/resources/rules"]
        assert compile_task.result["written_uris"]

        # The file-write work must be detached from the compile Task so it is owned
        # by its own content_batch_write Task instead of hijacking the compile one.
        assert fs.batch_task_context is None or (
            fs.batch_task_context.task_id != compile_task.task_id
        )
    finally:
        for background in tuple(service._running):
            background.cancel()
        if service._running:
            await asyncio.gather(*tuple(service._running), return_exceptions=True)
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_replace_deletes_old_rule_and_creates_new_rule():
    fs = _FS()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    old = _stored_rule("old-rule")
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id=old.scenario_id,
        name="File operations",
        description="Rules for file operations.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="REPLACE",
                target_ids=[old.atomic_id],
                record=_record("replacement"),
                evidence_spans=["replace old rule"],
            )
        ]
    )

    await service._apply_plan(
        plan,
        existing_scenarios=[scenario],
        existing_rules=[old],
        source_uris=[],
        ctx=_ctx(),
    )

    by_uri = {operation["uri"]: operation["content"] for operation in fs.batch["operations"]}
    old_metadata = yaml.safe_load(by_uri[old.uri].split("---", 2)[1])
    replacement_uri = "viking://user/alice/memories/rules/file-operations/replacement.md"
    replacement_metadata = yaml.safe_load(by_uri[replacement_uri].split("---", 2)[1])
    assert old_metadata["status"] == "archived"
    assert old_metadata["archived_at"]
    assert replacement_metadata["status"] == "active"


@pytest.mark.asyncio
async def test_update_preserves_paused_rule_status():
    fs = _FS()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    paused = _stored_rule("paused-rule", status="paused")
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id=paused.scenario_id,
        name="File operations",
        description="Rules for file operations.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )

    await service._apply_plan(
        RuleCompilePlan(
            mutations=[
                RuleMutation(
                    operation="UPDATE",
                    target_ids=[paused.atomic_id],
                    record=_record("paused-rule"),
                    evidence_spans=["reassert paused rule"],
                )
            ]
        ),
        existing_scenarios=[scenario],
        existing_rules=[paused],
        source_uris=[],
        ctx=_ctx(),
    )

    by_uri = {operation["uri"]: operation["content"] for operation in fs.batch["operations"]}
    metadata = yaml.safe_load(by_uri[paused.uri].split("---", 2)[1])
    assert scenario.uri not in by_uri
    assert metadata["status"] == "paused"
    assert metadata["revision"] == paused.revision + 1
    assert metadata["schema"] == RULE_SCHEMA
    assert "archived_at" not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["PAUSE", "ARCHIVE"])
async def test_pause_and_archive_have_distinct_storage_and_restore_semantics(operation):
    fs = _FS()
    service = RuleService()
    service.set_dependencies(
        fs=fs, resources=SimpleNamespace(), search=SimpleNamespace(), vlm_resolver=None
    )
    active = _stored_rule("lifecycle-rule")
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id=active.scenario_id,
        name="File operations",
        description="Rules for file operations.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="",
    )
    changed = await service._apply_plan(
        RuleCompilePlan(
            mutations=[RuleMutation(operation=operation, target_ids=[active.atomic_id])]
        ),
        existing_scenarios=[scenario],
        existing_rules=[active],
        source_uris=[],
        ctx=_ctx(),
    )
    writes = {item["uri"]: item["content"] for item in fs.batch["operations"]}
    assert set(writes) == {active.uri}
    assert scenario.uri not in writes
    fs.documents.update(writes)
    stored = await service._load_rule(active.uri, _ctx())
    assert stored.status == ("paused" if operation == "PAUSE" else "archived")
    assert bool(stored.archived_at) == (operation == "ARCHIVE")
    assert stored.revision == active.revision + 1
    assert changed["operations"][0]["operation"] == operation
    metadata = yaml.safe_load(writes[active.uri].split("---", 2)[1])
    assert metadata["schema"] == RULE_SCHEMA

    if operation == "PAUSE":
        await service._apply_plan(
            RuleCompilePlan(
                mutations=[RuleMutation(operation="RESTORE", target_ids=[stored.atomic_id])]
            ),
            existing_scenarios=[scenario],
            existing_rules=[stored],
            source_uris=[],
            ctx=_ctx(),
        )
        restore_writes = {item["uri"]: item["content"] for item in fs.batch["operations"]}
        assert set(restore_writes) == {active.uri}
        assert scenario.uri not in restore_writes
        fs.documents.update(restore_writes)
        restored = await service._load_rule(active.uri, _ctx())
        assert restored.status == "active"
        assert restored.archived_at is None
        assert restored.revision == stored.revision + 1


@pytest.mark.asyncio
async def test_archive_is_idempotent():
    service = RuleService()
    service.set_dependencies(
        fs=_FS(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    archived_at = "2026-09-01T00:00:00+00:00"
    archived = _stored_rule("old-rule", status="archived", archived_at=archived_at)
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id=archived.scenario_id,
        name="File operations",
        description="Rules for file operations.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )

    unchanged = await service._apply_plan(
        RuleCompilePlan(
            mutations=[
                RuleMutation(
                    operation="ARCHIVE",
                    target_ids=[archived.atomic_id],
                    evidence_spans=["archive old rule"],
                )
            ]
        ),
        existing_scenarios=[scenario],
        existing_rules=[archived],
        source_uris=[],
        ctx=_ctx(),
        persist=False,
    )
    assert unchanged["_rules"][0].archived_at == archived_at
    assert unchanged["written_uris"] == []

@pytest.mark.asyncio
async def test_cleanup_removes_only_rules_archived_for_full_retention_period():
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    old = _stored_rule(
        "old-archived",
        status="archived",
        archived_at=(now - timedelta(days=30)).isoformat(),
    )
    recent = _stored_rule(
        "recent-archived",
        status="archived",
        archived_at=(now - timedelta(days=29)).isoformat(),
    )
    paused = _stored_rule("paused", status="paused")
    active = _stored_rule("active")
    rules = [old, recent, paused, active]

    def rule_markdown(rule: StoredRule) -> str:
        metadata = {
            "schema": RULE_SCHEMA,
            "atomic_id": rule.atomic_id,
            "revision": rule.revision,
            "status": rule.status,
            "scenario_id": rule.scenario_id,
            "source_uris": rule.source_uris,
            "evidence": rule.evidence,
            "updated_at": now.isoformat(),
            **rule.record.model_dump(exclude={"body"}),
        }
        if rule.archived_at:
            metadata["archived_at"] = rule.archived_at
        return _render_markdown(metadata, rule.record.body)

    scenario_uri = "viking://user/alice/memories/rules/file-operations/abstract.md"
    scenario_content = _render_markdown(
        {
            "schema": SCENARIO_SCHEMA,
            "scenario_id": "file-operations",
            "name": "File operations",
            "description": "Rules for file operations.",
            "updated_at": now.isoformat(),
        },
        "# File operations\n\n## Active rules",
    )
    documents = {rule.uri: rule_markdown(rule) for rule in rules}
    documents[scenario_uri] = scenario_content
    fs = _CleanupFS(documents)
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=RulesConfig(archived_retention_days=30),
    )

    result = await service.cleanup_archived_rules(ctx=_ctx(), now=now)

    assert result["deleted"] == [old.uri]
    assert result["failed"] == []
    assert fs.removed == [old.uri]
    scenario_metadata = yaml.safe_load(fs.documents[scenario_uri].split("---", 2)[1])
    assert "rule_uris" not in scenario_metadata
    assert recent.uri in fs.documents
    assert paused.uri in fs.documents
    assert active.uri in fs.documents


@pytest.mark.asyncio
async def test_apply_update_rejects_rule_outside_recalled_scenarios():
    service = RuleService()
    service.set_dependencies(
        fs=_FS(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="UPDATE",
                target_ids=["not-recalled"],
                record=_record(),
            )
        ]
    )

    with pytest.raises(InvalidArgumentError, match="unknown rule"):
        await service._apply_plan(
            plan,
            existing_scenarios=[],
            existing_rules=[],
            source_uris=[],
            ctx=_ctx(),
        )


@pytest.mark.asyncio
async def test_search_returns_only_rules_above_independent_probability_threshold(
    monkeypatch,
):
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id="file-operations",
        name="File operations",
        description="Rules for editing files.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )
    rules = [
        _stored_rule("a").model_copy(
            update={
                "scenario_id": scenario.scenario_id,
                "uri": "viking://user/alice/memories/rules/file-operations/a.md",
                "content": "complete rule a",
            }
        ),
        _stored_rule("b").model_copy(
            update={
                "scenario_id": scenario.scenario_id,
                "revision": 2,
                "content": "complete rule b",
            }
        ),
    ]
    recall_scenarios = AsyncMock(return_value=[scenario])
    load_rules = AsyncMock(return_value=rules)
    monkeypatch.setattr(service, "_recall_scenarios", recall_scenarios)
    monkeypatch.setattr(service, "_load_rules_for_scenarios", load_rules)
    monkeypatch.setattr(
        service,
        "_score_rules",
        AsyncMock(return_value=([0.91, 0.4], "jev_noul", None)),
    )

    result = await service.search_rules(
        RuleSearchRequest(
            task_context="delete a generated file",
            rule_probability_threshold=0.7,
        ),
        ctx=_ctx(),
    )

    assert result["judge"] == "jev_noul"
    assert [rule["atomic_id"] for rule in result["rules"]] == ["a"]
    assert result["rules"][0]["content"] == "complete rule a"
    assert "[a]" in result["rendered"]
    assert "Ask for confirmation before deleting files." in result["rendered"]
    assert "Condition:" not in result["rendered"]
    assert "Rules are behavioral constraints" in result["rendered"]
    assert result["delivery"] == {
        "complete": True,
        "rule_count": 1,
        "rendered_chars": len(result["rendered"]),
    }
    recall_scenarios.assert_awaited_once()
    assert recall_scenarios.await_args.kwargs["limit"] is None
    load_rules.assert_awaited_once_with([scenario], _ctx(), strict=True)


@pytest.mark.asyncio
async def test_search_renders_all_rules_without_persistence_metadata(monkeypatch):
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    rules = []
    for atomic_id in ("first", "second", "third"):
        content = f"Apply {atomic_id}: " + atomic_id * 120
        record = _record(atomic_id).model_copy(
            update={
                "content": content,
                "body": (f'Guidance for {atomic_id}.\n\n<!-- MEMORY_FIELDS\n{{"version": 1}}\n-->'),
            }
        )
        rules.append(
            _stored_rule(atomic_id).model_copy(
                update={
                    "record": record,
                    "content": f"---\nschema: {RULE_SCHEMA}\n---\n\n{record.body}",
                }
            )
        )
    monkeypatch.setattr(
        service,
        "_find_relevant_rules",
        AsyncMock(
            return_value=SimpleNamespace(
                scenarios=[],
                selected=[(rule, 0.9) for rule in rules],
                judge="jev_noul",
                warning=None,
                scenario_selection=None,
            )
        ),
    )

    first_page = await service.search_rules(
        RuleSearchRequest(task_context="apply all relevant rules"),
        ctx=_ctx(),
    )

    assert first_page["delivery"]["complete"] is True
    assert first_page["delivery"]["rule_count"] == len(rules)
    assert "MEMORY_FIELDS" not in first_page["rendered"]
    assert f"schema: {RULE_SCHEMA}" not in first_page["rendered"]
    for rule in rules:
        assert rule.record.content in first_page["rendered"]


@pytest.mark.asyncio
async def test_two_stage_search_scores_all_rules_from_recalled_scenarios_without_rule_limit(
    monkeypatch,
):
    service = RuleService()
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id="all-rules",
        name="All rules",
        description="Every active Rule is validated.",
        uri="viking://user/alice/memories/rules/all-rules/abstract.md",
        content="scenario",
    )
    rules = [
        _stored_rule(f"rule-{index}", scenario_id=scenario.scenario_id) for index in range(125)
    ]
    recall_scenarios = AsyncMock(return_value=[scenario])
    load_rules = AsyncMock(return_value=rules)
    monkeypatch.setattr(service, "_recall_scenarios", recall_scenarios)
    monkeypatch.setattr(service, "_load_rules_for_scenarios", load_rules)
    monkeypatch.setattr(
        service, "_score_rules", AsyncMock(return_value=([0.9] * len(rules), "jev_noul", None))
    )

    matches = await service._find_relevant_rules(
        "validate every applicable rule",
        ctx=_ctx(),
        rule_probability_threshold=0.65,
        allowed_statuses=frozenset({"active"}),
        strict_rule_loading=True,
        filter_scenarios_with_jev=True,
    )

    assert len(matches.selected) == len(rules)
    assert [rule.atomic_id for rule, _ in matches.selected] == [rule.atomic_id for rule in rules]
    recall_scenarios.assert_awaited_once()
    load_rules.assert_awaited_once_with([scenario], _ctx(), strict=True)


@pytest.mark.asyncio
async def test_runtime_scenario_recall_searches_all_scenarios_before_thresholding(monkeypatch):
    scenario_uris = [
        f"viking://user/alice/memories/rules/scenario-{index}/abstract.md" for index in range(7)
    ]
    fs = SimpleNamespace(
        stat=AsyncMock(return_value={}),
        glob=AsyncMock(return_value={"matches": scenario_uris}),
    )
    search = SimpleNamespace(
        find=AsyncMock(
            return_value={
                "memories": [{"uri": uri} for uri in scenario_uris],
                "resources": [],
                "skills": [],
            }
        )
    )
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=search,
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    scenarios = [
        StoredScenario(
            schema=SCENARIO_SCHEMA,
            scenario_id=f"scenario-{index}",
            name=f"Scenario {index}",
            description=f"Scenario {index}",
            uri=uri,
            content="scenario",
        )
        for index, uri in enumerate(scenario_uris)
    ]
    monkeypatch.setattr(service, "_load_scenario", AsyncMock(side_effect=scenarios))

    recalled = await service._recall_scenarios(
        "current task",
        ctx=_ctx(),
        limit=None,
        score_threshold=0.1,
    )

    assert recalled == scenarios
    assert search.find.await_args.kwargs["limit"] == len(scenario_uris)
    assert search.find.await_args.kwargs["target_uri"] == "viking://user/alice/memories/rules"
    assert search.find.await_args.kwargs["filter"] == {
        "op": "or",
        "conds": [
            {"op": "must", "field": "uri", "conds": [uri], "para": "-d=0"}
            for uri in sorted(scenario_uris)
        ],
    }


@pytest.mark.asyncio
async def test_recalled_scenario_rule_loading_fails_closed_on_unreadable_rule(monkeypatch):
    first_uri = "viking://user/alice/memories/rules/file-operations/first.md"
    broken_uri = "viking://user/alice/memories/rules/file-operations/broken.md"
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(glob=AsyncMock(return_value={"matches": [first_uri, broken_uri]})),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    monkeypatch.setattr(
        service,
        "_load_rule",
        AsyncMock(side_effect=[_stored_rule("first"), ValueError("invalid Rule document")]),
    )
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id="file-operations",
        name="File operations",
        description="Rules for file operations.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )

    with pytest.raises(ProcessingError, match="recalled Scenario contains an unreadable Rule"):
        await service._load_rules_for_scenarios([scenario], _ctx(), strict=True)


def test_rule_renderer_keeps_an_oversized_rule_complete():
    content = "Never partially render this constraint. " * 80
    record = _record("oversized").model_copy(update={"content": content})
    rule = _stored_rule("oversized").model_copy(update={"record": record})

    rendered = _render_rules([(rule, 0.99)])

    assert content.strip() in rendered
    assert len(rendered) > 1_000


@pytest.mark.parametrize("applies_when", ["when a task requires deleting files", ""])
def test_rule_renderer_omits_empty_exception(applies_when):
    record = _record("no-exception").model_copy(
        update={"exception": "", "applies_when": applies_when}
    )
    rule = _stored_rule("no-exception").model_copy(update={"record": record})

    rendered = _render_rules([(rule, 0.99)])

    assert f"Applies when: {applies_when or 'Every situation, except as stated below.'}" in rendered
    assert "Exception:" not in rendered


@pytest.mark.asyncio
async def test_run_compile_registers_owned_running_work(monkeypatch):
    tracker = SimpleNamespace(
        register_running_task=AsyncMock(return_value="inline:rule-compile"),
        start=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        unregister_running_task=AsyncMock(),
    )
    service = RuleService()
    compile_rule = AsyncMock(return_value={"rules_root": "viking://user/alice/memories/rules"})
    monkeypatch.setattr(
        "openviking.service.rule_service.get_task_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(service, "_compile", compile_rule)

    await service._run_compile(
        task_id="rul_123",
        request=RuleCompileRequest(instruction="compile these rules"),
        ctx=_ctx(),
    )

    tracker.register_running_task.assert_awaited_once_with(
        "rul_123",
        account_id="acme",
        user_id="alice",
    )
    tracker.start.assert_awaited_once_with(
        "rul_123",
        account_id="acme",
        user_id="alice",
        stage="ingesting_sources",
    )
    tracker.complete.assert_awaited_once_with(
        "rul_123",
        {"rules_root": "viking://user/alice/memories/rules"},
        account_id="acme",
        user_id="alice",
    )
    tracker.fail.assert_not_awaited()
    tracker.unregister_running_task.assert_awaited_once_with("rul_123")


@pytest.mark.asyncio
async def test_compile_uses_search_pipeline_to_select_existing_rules(monkeypatch):
    class _Tracker:
        patch_task = AsyncMock()

    fs = _FS()
    compiler = SimpleNamespace()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=compiler)),
        rules_config=None,
    )
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id="file-operations",
        name="File operations",
        description="Rules for editing files.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )
    relevant = _stored_rule("relevant")
    paused = _stored_rule("paused", status="paused")
    archived = _stored_rule("archived", status="archived")
    unrelated = _stored_rule("unrelated")
    recall_scenarios = AsyncMock(return_value=[scenario])
    score_rules = AsyncMock(return_value=([0.9, 0.8, 0.2], "jev_noul", None))
    build_plan = AsyncMock(
        return_value=RuleCompilePlan(
            mutations=[
                RuleMutation(
                    operation="UNCERTAIN",
                    evidence_spans=["delete files"],
                    reason="ambiguous",
                )
            ]
        )
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.get_task_tracker",
        lambda: _Tracker(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    monkeypatch.setattr(service, "_collect_sources", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(service, "_recall_scenarios", recall_scenarios)
    monkeypatch.setattr(
        service,
        "_load_rules_for_scenarios",
        AsyncMock(return_value=[relevant, paused, archived, unrelated]),
    )
    monkeypatch.setattr(service, "_score_rules", score_rules)
    monkeypatch.setattr(service, "_build_compile_plan", build_plan)
    monkeypatch.setattr(service, "_compiler_prompt_token_limit", lambda _: 8_000)

    await service._compile(
        RuleCompileRequest(instruction="delete files"),
        ctx=_ctx(),
        task_id="rul_match",
    )

    assert recall_scenarios.await_args.kwargs["limit"] == 5
    assert recall_scenarios.await_args.kwargs["score_threshold"] == 0.1
    assert score_rules.await_args.args[0] == "delete files"
    assert score_rules.await_args.args[1] == [relevant, paused, unrelated]
    assert [rule.atomic_id for rule in build_plan.await_args.kwargs["rules"]] == [
        "relevant",
        "paused",
    ]


def test_compile_request_requires_instruction_or_source():
    with pytest.raises(ValueError, match="instruction or sources"):
        RuleCompileRequest()


def test_compile_request_reuses_complete_add_resource_model():
    request = RuleCompileRequest(
        sources=[
            {
                "path": "space:home",
                "add_type": "feishu",
                "to": "viking://resources/team-rules",
                "args": {"feishu_access_token": "token"},
                "strict": True,
                "processing_mode": "vectors_only",
                "tags": ["rules"],
            },
            {"temp_file_id": "upload_rules"},
            {"resource_uri": "viking://resources/existing-rules"},
        ]
    )

    assert isinstance(request.sources[0], AddResourceRequest)
    assert request.sources[0].add_type == "feishu"
    assert request.sources[0].args == {"feishu_access_token": "token"}
    assert isinstance(request.sources[1], AddResourceRequest)
    assert request.sources[1].temp_file_id == "upload_rules"
    assert request.sources[2].resource_uri == "viking://resources/existing-rules"


@pytest.mark.asyncio
async def test_collect_sources_uses_shared_add_resource_ingestion(monkeypatch):
    ingest = AsyncMock(return_value={"root_uri": "viking://user/alice/resources/team-rules"})
    monkeypatch.setattr(
        "openviking.service.rule_service.ingest_resource_request",
        ingest,
    )
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(add_resource=AsyncMock()),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
        config=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_read_source", AsyncMock(return_value="parsed rules"))
    request = RuleCompileRequest(
        sources=[
            {
                "path": "https://example.com/team-rules.md",
                "args": {"header": "value"},
            }
        ]
    )

    text, uris = await service._collect_sources(request, _ctx())

    assert text == "parsed rules"
    assert uris == ["viking://user/alice/resources/team-rules"]
    add_request = ingest.await_args.args[0]
    assert isinstance(add_request, AddResourceRequest)
    assert add_request.args == {"header": "value"}
    assert add_request.instruction == ""
    assert ingest.await_args.kwargs["wait"] is True
    assert ingest.await_args.kwargs["internal_task"] is True


@pytest.mark.asyncio
async def test_collect_sources_waits_for_declared_connector(monkeypatch):
    ingest = AsyncMock(
        return_value={
            "status": "accepted",
            "task_id": "connector-task",
            "resource_id": "viking://user/alice/resources/team-rules",
        }
    )
    tracker = SimpleNamespace(
        wait=AsyncMock(
            return_value=TaskRecord(
                task_id="connector-task",
                task_type="connector_import",
                status=TaskStatus.COMPLETED,
                resource_id="viking://user/alice/resources/team-rules",
            )
        )
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.ingest_resource_request",
        ingest,
    )
    monkeypatch.setattr("openviking.service.rule_service.get_task_tracker", lambda: tracker)
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(add_resource=AsyncMock()),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
        config=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_read_source", AsyncMock(return_value="parsed rules"))
    request = RuleCompileRequest(
        sources=[
            {
                "path": "space:home",
                "add_type": "feishu",
                "to": "viking://resources/team-rules",
                "args": {"feishu_access_token": "token"},
                "instruction": "Summarize this source",
            }
        ]
    )

    text, uris = await service._collect_sources(request, _ctx())

    assert text == "parsed rules"
    assert uris == ["viking://user/alice/resources/team-rules"]
    assert ingest.await_args.args[0].instruction == ""
    assert ingest.await_args.kwargs["wait"] is False
    assert ingest.await_args.kwargs["internal_task"] is True
    tracker.wait.assert_awaited_once_with(
        "connector-task",
        account_id="acme",
        user_id="alice",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_collect_sources_surfaces_declared_connector_failure(monkeypatch):
    monkeypatch.setattr(
        "openviking.service.rule_service.ingest_resource_request",
        AsyncMock(
            return_value={
                "status": "accepted",
                "task_id": "connector-task",
                "resource_id": "viking://user/alice/resources/team-rules",
            }
        ),
    )
    tracker = SimpleNamespace(
        wait=AsyncMock(
            return_value=TaskRecord(
                task_id="connector-task",
                task_type="connector_import",
                status=TaskStatus.FAILED,
                error="source unavailable",
            )
        )
    )
    monkeypatch.setattr("openviking.service.rule_service.get_task_tracker", lambda: tracker)
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
        config=SimpleNamespace(),
    )
    request = RuleCompileRequest(
        sources=[
            {
                "path": "space:home",
                "add_type": "feishu",
                "to": "viking://resources/team-rules",
            }
        ]
    )

    with pytest.raises(ProcessingError, match="source unavailable"):
        await service._collect_sources(request, _ctx())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        {"path": "https://example.com/screenshot.PNG?download=1"},
        {"temp_file_id": "upload_123", "source_name": "meeting.mp3"},
    ],
)
async def test_collect_sources_rejects_obvious_multimodal_inputs_before_ingestion(
    monkeypatch,
    source,
):
    ingest = AsyncMock()
    monkeypatch.setattr(
        "openviking.service.rule_service.ingest_resource_request",
        ingest,
    )
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
        config=SimpleNamespace(),
    )

    with pytest.raises(InvalidArgumentError, match="multimodal"):
        await service._collect_sources(RuleCompileRequest(sources=[source]), _ctx())

    ingest.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "https://example.com/rules.zip",
        "https://github.com/acme/rules.git",
    ],
)
async def test_collect_sources_allows_archive_and_git_inputs(monkeypatch, path):
    ingest = AsyncMock(return_value={"root_uri": "viking://user/alice/resources/rules"})
    monkeypatch.setattr(
        "openviking.service.rule_service.ingest_resource_request",
        ingest,
    )
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
        config=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_read_source", AsyncMock(return_value="parsed rules"))

    text, _ = await service._collect_sources(
        RuleCompileRequest(sources=[{"path": path}]),
        _ctx(),
    )

    assert text == "parsed rules"
    assert ingest.await_args.kwargs["wait"] is True


@pytest.mark.asyncio
async def test_collect_sources_rejects_binary_resource_reference():
    binary_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    fs = SimpleNamespace(
        stat=AsyncMock(return_value={"isDir": False}),
        read_visible=AsyncMock(side_effect=binary_error),
    )
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )

    with pytest.raises(InvalidArgumentError, match="readable text"):
        await service._collect_sources(
            RuleCompileRequest(sources=[{"resource_uri": "viking://resources/screenshot.png"}]),
            _ctx(),
        )


@pytest.mark.asyncio
async def test_collect_sources_validates_sources_after_text_budget_is_exhausted(monkeypatch):
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )
    read_source = AsyncMock(side_effect=["x" * 128_000, ""])
    monkeypatch.setattr(service, "_read_source", read_source)

    with pytest.raises(InvalidArgumentError, match="readable text"):
        await service._collect_sources(
            RuleCompileRequest(
                sources=[
                    {"resource_uri": "viking://resources/large.md"},
                    {"resource_uri": "viking://resources/image.png"},
                ]
            ),
            _ctx(),
        )

    assert read_source.await_args_list[1].args[2] == 1


@pytest.mark.asyncio
async def test_read_source_keeps_text_from_mixed_directory():
    root = "viking://resources/mixed"
    text_uri = f"{root}/rules.md"
    image_uri = f"{root}/screenshot.png"

    async def stat(uri, *_args, **_kwargs):
        return {"isDir": uri == root}

    async def read_visible(uri, *_args, **_kwargs):
        if uri == image_uri:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return "Always confirm destructive operations."

    fs = SimpleNamespace(
        stat=AsyncMock(side_effect=stat),
        ls=AsyncMock(return_value=SimpleNamespace(entries=[image_uri, text_uri])),
        read_visible=AsyncMock(side_effect=read_visible),
    )
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
        rules_config=None,
    )

    text = await service._read_source(root, _ctx(), 10_000)

    assert text_uri in text
    assert "Always confirm destructive operations." in text
    assert image_uri not in text


def test_compile_plan_rejects_evidence_not_present_in_input():
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(),
                scenario=RuleScenarioRef(
                    name="File operations",
                    description="Rules for editing files.",
                ),
                evidence_spans=["invented instruction"],
            )
        ]
    )

    with pytest.raises(VLMFailedError, match="evidence not found"):
        RuleService._validate_plan_evidence(
            plan,
            material="Confirm before deleting files.",
        )


def test_compile_plan_requires_update_preservation_proof():
    old = _stored_rule("old-rule")
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="UPDATE",
                target_ids=[old.atomic_id],
                record=_record("updated"),
                evidence_spans=["confirm before deleting files"],
                applicability_evidence=f"existing:{old.atomic_id}",
            )
        ]
    )

    with pytest.raises(VLMFailedError, match="requires carried_over"):
        RuleService._validate_compile_plan(
            plan,
            material="confirm before deleting files",
            existing_rules=[old],
        )


def test_compile_plan_allows_paused_rule_update_and_replace():
    paused = _stored_rule("paused-rule", status="paused")
    update = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="UPDATE",
                target_ids=[paused.atomic_id],
                record=_record("paused-rule"),
                evidence_spans=["reassert paused rule"],
                applicability_evidence=f"existing:{paused.atomic_id}",
                carried_over={
                    "existing_requirements": [
                        "Ask for confirmation before deleting files.",
                    ]
                },
            )
        ]
    )
    replacement = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="REPLACE",
                target_ids=[paused.atomic_id],
                record=_record("replacement"),
                evidence_spans=["replace paused rule"],
                applicability_evidence="replace paused rule",
            )
        ]
    )

    RuleService._validate_compile_plan(
        update,
        material="reassert paused rule",
        existing_rules=[paused],
    )
    RuleService._validate_compile_plan(
        replacement,
        material="replace paused rule",
        existing_rules=[paused],
    )


@pytest.mark.parametrize("operation", ["NOOP", "UPDATE", "REPLACE", "PAUSE", "RESTORE"])
def test_compile_plan_rejects_non_lifecycle_operations_on_archived_rule(operation):
    archived = _stored_rule("archived-rule", status="archived")
    kwargs = {
        "operation": operation,
        "target_ids": [archived.atomic_id],
        "evidence_spans": ["change archived rule"],
    }
    if operation in {"UPDATE", "REPLACE"}:
        kwargs["record"] = _record("changed")
    if operation == "UPDATE":
        kwargs["carried_over"] = {
            "existing_requirements": ["Ask for confirmation before deleting files."]
        }
    plan = RuleCompilePlan(mutations=[RuleMutation(**kwargs)])

    with pytest.raises(VLMFailedError, match="status archived"):
        RuleService._validate_compile_plan(
            plan,
            material="change archived rule",
            existing_rules=[archived],
        )


def test_compile_plan_rejects_restore_on_active_rule():
    active = _stored_rule("active-rule")
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="RESTORE",
                target_ids=[active.atomic_id],
                evidence_spans=["restore active rule"],
            )
        ]
    )

    with pytest.raises(VLMFailedError, match="status active"):
        RuleService._validate_compile_plan(
            plan,
            material="restore active rule",
            existing_rules=[active],
        )


def test_compile_plan_requires_archive_authorization_in_instruction_text():
    active = _stored_rule("active-rule")
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="ARCHIVE",
                target_ids=[active.atomic_id],
                evidence_spans=["delete active rule"],
            )
        ]
    )

    with pytest.raises(VLMFailedError, match="instruction_text"):
        RuleService._validate_compile_plan(
            plan,
            material="compile this source\ndelete active rule",
            instruction_text="compile this source",
            existing_rules=[active],
        )
    RuleService._validate_compile_plan(
        plan,
        material="delete active rule",
        instruction_text="delete active rule",
        existing_rules=[active],
    )


@pytest.mark.parametrize("exception", ["", "when the user already confirmed"])
def test_compile_plan_empty_applies_when_still_requires_exception_evidence(exception):
    mutation = RuleMutation(
        operation="NEW",
        record=_record().model_copy(update={"applies_when": "", "exception": exception}),
        scenario=RuleScenarioRef(name="All tasks", description="Global constraints."),
        evidence_spans=["Always confirm deletion."],
    )
    plan = RuleCompilePlan(mutations=[mutation])
    material = "Always confirm deletion. Except when the user already confirmed."
    if exception:
        with pytest.raises(VLMFailedError, match="applicability_evidence"):
            RuleService._validate_compile_plan(plan, material=material, existing_rules=[])
        mutation.applicability_evidence = exception
    RuleService._validate_compile_plan(plan, material=material, existing_rules=[])


def test_compile_plan_rejects_unsupported_applicability():
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(),
                scenario=RuleScenarioRef(
                    name="File operations",
                    description="Rules for editing files.",
                ),
                evidence_spans=["Always confirm deletion."],
                applicability_evidence="when deleting production files",
            )
        ]
    )

    with pytest.raises(VLMFailedError, match="applicability_evidence"):
        RuleService._validate_compile_plan(
            plan,
            material="Always confirm deletion.",
            existing_rules=[],
        )


@pytest.mark.asyncio
async def test_compile_plan_repairs_semantically_invalid_response():
    old = _stored_rule("old-rule")
    first = {
        "mutations": [
            {
                "operation": "NEW",
                "record": _record("duplicate").model_dump(),
                "scenario": {
                    "name": "File operations",
                    "description": "Rules for editing files.",
                },
                "evidence_spans": ["confirm before deleting files"],
                "applicability_evidence": "",
            }
        ]
    }
    repaired = {
        "mutations": [
            {
                "operation": "NOOP",
                "target_ids": [old.atomic_id],
                "evidence_spans": ["confirm before deleting files"],
                "reason": "The active rule already entails the instruction.",
            }
        ]
    }
    compiler = SimpleNamespace(
        get_completion_async=AsyncMock(side_effect=[json.dumps(first), json.dumps(repaired)])
    )
    service = RuleService()

    plan = await service._build_compile_plan(
        instruction="confirm before deleting files",
        source_text="",
        scenarios=[],
        rules=[old],
        ctx=_ctx(),
        vlm=compiler,
        prompt_token_limit=20_000,
    )

    assert plan.mutations[0].operation == "NOOP"
    assert compiler.get_completion_async.await_count == 2
    repair_prompt = compiler.get_completion_async.await_args_list[1].kwargs["prompt"]
    assert "failed deterministic validation" in repair_prompt
    assert "applicability_evidence" in repair_prompt


@pytest.mark.asyncio
async def test_compile_plan_rejects_oversized_prompt_without_trimming_state():
    service = RuleService()
    compiler = SimpleNamespace(get_completion_async=AsyncMock())
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id="file-operations",
        name="File operations",
        description="Rules for editing files.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )
    rule = _stored_rule("existing-rule")
    full_prompt = service._render_compile_prompt(
        instruction="confirm before deleting files",
        source_text="",
        scenario_data=[
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "description": scenario.description,
            }
        ],
        rule_data=[
            {
                **rule.record.model_dump(),
                "atomic_id": rule.atomic_id,
                "revision": rule.revision,
                "status": rule.status,
                "scenario_id": rule.scenario_id,
            }
        ],
    )

    with pytest.raises(InvalidArgumentError, match="refusing to omit existing scenarios or rules"):
        await service._build_compile_plan(
            instruction="confirm before deleting files",
            source_text="",
            scenarios=[scenario],
            rules=[rule],
            ctx=_ctx(),
            vlm=compiler,
            prompt_token_limit=estimate_text_tokens(full_prompt) - 1,
        )

    compiler.get_completion_async.assert_not_awaited()


def test_compile_repair_prompt_rejects_overflow_without_truncation():
    response = "previous response " * 500
    validation_error = "validation error " * 200
    full_repair = RuleService._render_compile_repair_prompt(
        "base prompt",
        response=response,
        validation_error=validation_error,
        prompt_token_limit=None,
    )

    assert response in full_repair
    assert validation_error in full_repair
    with pytest.raises(
        InvalidArgumentError,
        match="refusing to truncate validation diagnostics or compiler state",
    ):
        RuleService._render_compile_repair_prompt(
            "base prompt",
            response=response,
            validation_error=validation_error,
            prompt_token_limit=estimate_text_tokens(full_repair) - 1,
        )


def test_compile_material_batches_preserve_all_text_within_budget():
    instruction = "Only compile release rules; exclude all finance rules."
    source = ("## Source\nSecond rule paragraph.\n" * 100) + "final rule"

    batches = _build_material_batches(instruction, source, max_tokens=300)

    assert len(batches) > 2
    assert all(batch_instruction == instruction for batch_instruction, _ in batches)
    assert "".join(batch_source for _, batch_source in batches) == source
    assert all(
        estimate_text_tokens(batch_instruction) + estimate_text_tokens(batch_source) <= 300
        for batch_instruction, batch_source in batches
    )


def test_compile_material_batches_split_instruction_only_input():
    instruction = ("First rule paragraph.\n\n" * 80) + ("必须先确认。\n" * 80)

    batches = _build_material_batches(instruction, "", max_tokens=300)

    assert len(batches) > 1
    assert "".join(batch_instruction for batch_instruction, _ in batches) == instruction
    assert all(not batch_source for _, batch_source in batches)
    assert all(estimate_text_tokens(batch_instruction) <= 300 for batch_instruction, _ in batches)


def test_compile_material_batches_reject_source_when_instruction_fills_budget():
    with pytest.raises(InvalidArgumentError, match="leaves no room for source material"):
        _build_material_batches("scope constraint " * 20, "source", max_tokens=10)


@pytest.mark.asyncio
async def test_compile_repeats_source_scope_instruction_in_every_model_batch(monkeypatch):
    class _Tracker:
        patch_task = AsyncMock()

    instruction = "Only compile the release section; do not import the finance section."
    release = "Release policy clause. " * 800
    finance = "Finance policy clause. " * 800
    source = (release + finance)[:29_460]
    prompts: list[str] = []

    async def record_prompt(*, prompt, thinking):
        assert thinking is True
        prompts.append(prompt)
        return json.dumps({"mutations": [], "reason": "prompt recorded"})

    fs = _FS()
    compiler = SimpleNamespace(
        model="unknown-custom",
        max_tokens=None,
        get_completion_async=AsyncMock(side_effect=record_prompt),
    )
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=compiler)),
        rules_config=None,
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.get_task_tracker",
        lambda: _Tracker(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    monkeypatch.setattr(service, "_collect_sources", AsyncMock(return_value=(source, [])))
    monkeypatch.setattr(
        service,
        "_find_relevant_rules",
        AsyncMock(return_value=SimpleNamespace(scenarios=[], selected=[])),
    )

    result = await service._compile(
        RuleCompileRequest(instruction=instruction),
        ctx=_ctx(),
        task_id="rul_scope_batches",
    )

    assert len(source) == 29_460
    assert result["batch_count"] == len(prompts) == 2
    assert "Finance policy clause." in prompts[1]
    assert all(instruction in prompt for prompt in prompts)
    assert fs.batch is None


@pytest.mark.asyncio
async def test_compile_batches_sequentially_and_writes_once(monkeypatch):
    class _Tracker:
        patch_task = AsyncMock()

    fs = _FS()
    compiler = SimpleNamespace(get_completion_async=AsyncMock())
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=compiler)),
        rules_config=None,
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.get_task_tracker",
        lambda: _Tracker(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    instruction = "A policy paragraph requiring confirmation.\n\n" * 400
    monkeypatch.setattr(
        service,
        "_collect_sources",
        AsyncMock(return_value=("", [])),
    )
    monkeypatch.setattr(service, "_recall_scenarios", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_load_rules_for_scenarios", AsyncMock(return_value=[]))
    prompt_token_limit = (
        estimate_text_tokens(
            service._render_compile_prompt(
                instruction="",
                source_text="",
                scenario_data=[],
                rule_data=[],
            )
        )
        + 600
    )
    monkeypatch.setattr(
        service,
        "_compiler_prompt_token_limit",
        lambda _: prompt_token_limit,
    )

    calls = 0

    async def compile_plan(*, prompt, thinking):
        nonlocal calls
        assert thinking is True
        assert estimate_text_tokens(prompt) <= prompt_token_limit
        if calls == 0:
            response = {
                "mutations": [
                    {
                        "operation": "NEW",
                        "record": _record().model_dump(),
                        "scenario": {
                            "name": "File operations",
                            "description": "Rules for editing files.",
                        },
                        "evidence_spans": ["A policy paragraph"],
                        "applicability_evidence": "A policy paragraph",
                    }
                ],
                "reason": "created",
            }
        else:
            assert '"atomic_id": "confirm-delete"' in prompt
            response = {
                "mutations": [
                    {
                        "operation": "NOOP",
                        "target_ids": ["confirm-delete"],
                        "evidence_spans": ["A policy paragraph"],
                    }
                ],
                "reason": "already covered",
            }
        calls += 1
        return json.dumps(response)

    compiler.get_completion_async.side_effect = compile_plan

    result = await service._compile(
        RuleCompileRequest(instruction=instruction),
        ctx=_ctx(),
        task_id="rul_batch",
    )

    assert result["batch_count"] == calls
    assert calls > 1
    assert result["operations"][0]["operation"] == "NEW"
    assert all(item["operation"] == "NOOP" for item in result["operations"][1:])
    assert len(result["written_uris"]) == 2
    assert fs.batch is not None
    assert len(fs.batch["operations"]) == 2


@pytest.mark.asyncio
async def test_compile_does_not_carry_archived_rules_into_later_batches(monkeypatch):
    class _Tracker:
        patch_task = AsyncMock()

    fs = _FS()
    compiler = SimpleNamespace()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=compiler)),
        rules_config=None,
    )
    paused = _stored_rule("paused-rule", status="paused")
    scenario = StoredScenario(
        schema=SCENARIO_SCHEMA,
        scenario_id=paused.scenario_id,
        name="File operations",
        description="Rules for file operations.",
        uri="viking://user/alice/memories/rules/file-operations/abstract.md",
        content="scenario",
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.get_task_tracker",
        lambda: _Tracker(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    monkeypatch.setattr(service, "_collect_sources", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(service, "_compiler_prompt_token_limit", lambda _: 8_000)
    monkeypatch.setattr(
        "openviking.service.rule_service._build_material_batches",
        lambda *args, **kwargs: [("replace paused rule", ""), ("second batch", "")],
    )
    monkeypatch.setattr(
        service,
        "_find_relevant_rules",
        AsyncMock(
            side_effect=[
                SimpleNamespace(scenarios=[scenario], selected=[(paused, 0.9)]),
                SimpleNamespace(scenarios=[], selected=[]),
            ]
        ),
    )
    supplied_rules: list[list[tuple[str, str]]] = []

    async def build_plan(**kwargs):
        supplied_rules.append([(rule.atomic_id, rule.status) for rule in kwargs["rules"]])
        if len(supplied_rules) == 1:
            return RuleCompilePlan(
                mutations=[
                    RuleMutation(
                        operation="REPLACE",
                        target_ids=[paused.atomic_id],
                        record=_record("replacement"),
                        evidence_spans=["replace paused rule"],
                    )
                ]
            )
        return RuleCompilePlan(
            mutations=[
                RuleMutation(
                    operation="NOOP",
                    target_ids=["replacement"],
                    evidence_spans=["second batch"],
                )
            ]
        )

    monkeypatch.setattr(service, "_build_compile_plan", build_plan)

    await service._compile(
        RuleCompileRequest(instruction="replace paused rule"),
        ctx=_ctx(),
        task_id="rul_status_filter",
    )

    assert supplied_rules == [
        [("paused-rule", "paused")],
        [("replacement", "active")],
    ]


@pytest.mark.asyncio
async def test_compile_does_not_write_when_a_later_batch_fails(monkeypatch):
    class _Tracker:
        patch_task = AsyncMock()

    fs = _FS()
    compiler = SimpleNamespace()
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=compiler)),
        rules_config=None,
    )
    monkeypatch.setattr(
        "openviking.service.rule_service.get_task_tracker",
        lambda: _Tracker(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    monkeypatch.setattr(
        service,
        "_collect_sources",
        AsyncMock(return_value=("", [])),
    )
    monkeypatch.setattr(service, "_recall_scenarios", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_load_rules_for_scenarios", AsyncMock(return_value=[]))
    prompt_token_limit = (
        estimate_text_tokens(
            service._render_compile_prompt(
                instruction="",
                source_text="",
                scenario_data=[],
                rule_data=[],
            )
        )
        + 600
    )
    monkeypatch.setattr(
        service,
        "_compiler_prompt_token_limit",
        lambda _: prompt_token_limit,
    )
    first_plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(),
                scenario=RuleScenarioRef(
                    name="File operations",
                    description="Rules for editing files.",
                ),
                evidence_spans=["A policy paragraph"],
            )
        ]
    )
    monkeypatch.setattr(
        service,
        "_build_compile_plan",
        AsyncMock(side_effect=[first_plan, VLMFailedError("second batch failed")]),
    )

    with pytest.raises(VLMFailedError, match="second batch failed"):
        await service._compile(
            RuleCompileRequest(instruction="A policy paragraph requiring confirmation.\n\n" * 400),
            ctx=_ctx(),
            task_id="rul_batch_failure",
        )

    assert fs.batch is None


def _scenario_metadata(scenario_id="file-operations"):
    return {
        "schema": SCENARIO_SCHEMA,
        "scenario_id": scenario_id,
        "name": "File operations",
        "description": "All file operation constraints.",
    }


def _stored_markdown(rule):
    return _render_markdown(
        {
            "schema": RULE_SCHEMA,
            "atomic_id": rule.atomic_id,
            "revision": rule.revision,
            "status": rule.status,
            "scenario_id": rule.scenario_id,
            **rule.record.model_dump(exclude={"body"}),
        },
        rule.record.body,
    )


@pytest.mark.asyncio
async def test_scenario_directory_loads_rules_without_reference_lists():
    fs = _FS()
    scenario_uri = "viking://user/alice/memories/rules/file-operations/abstract.md"
    fs.documents[scenario_uri] = _render_markdown(_scenario_metadata(), "# File operations")
    rules = [_stored_rule("selected"), _stored_rule("not-selected", status="paused")]
    fs.documents.update({rule.uri: _stored_markdown(rule) for rule in rules})
    service = RuleService()
    service.set_dependencies(
        fs=fs, resources=SimpleNamespace(), search=SimpleNamespace(), vlm_resolver=None
    )

    scenario = await service._load_scenario(scenario_uri, _ctx())
    loaded = await service._load_rules_for_scenarios([scenario], _ctx(), strict=True)

    assert {rule.atomic_id for rule in loaded} == {"selected", "not-selected"}
    assert "rule_uris" not in scenario.model_dump()


@pytest.mark.asyncio
async def test_new_rule_reuses_unrecalled_scenario_without_losing_members():
    fs = _FS()
    scenario_uri = "viking://user/alice/memories/rules/file-operations/abstract.md"
    metadata = _scenario_metadata()
    scenario_content = _render_markdown(metadata, "# File operations")
    fs.documents[scenario_uri] = scenario_content
    old = _stored_rule("unselected")
    fs.documents[old.uri] = _stored_markdown(old)
    service = RuleService()
    service.set_dependencies(
        fs=fs, resources=SimpleNamespace(), search=SimpleNamespace(), vlm_resolver=None
    )
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record("new-rule"),
                scenario=RuleScenarioRef(
                    name="file operations", description="A narrower description."
                ),
            )
        ]
    )

    result = await service._apply_plan(
        plan, existing_scenarios=[], existing_rules=[], source_uris=[], ctx=_ctx()
    )
    writes = {item["uri"]: item["content"] for item in fs.batch["operations"]}
    assert scenario_uri not in writes
    assert fs.documents[scenario_uri] == scenario_content
    assert old.uri not in writes
    fs.documents.update(writes)
    scenario = await service._load_scenario(scenario_uri, _ctx())
    loaded = await service._load_rules_for_scenarios([scenario], _ctx(), strict=True)
    assert {rule.atomic_id for rule in loaded} == {"unselected", "new-rule"}
    assert result["operations"][0]["uri"].endswith("/file-operations/new-rule.md")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["abstract", "existing"])
async def test_new_rule_reserves_summary_name_and_unrecalled_rule_ids(name):
    fs = _FS()
    existing = _stored_rule("existing", scenario_id="other-scenario")
    fs.documents[existing.uri] = _stored_markdown(existing)
    service = RuleService()
    service.set_dependencies(
        fs=fs, resources=SimpleNamespace(), search=SimpleNamespace(), vlm_resolver=None
    )
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(name),
                scenario=RuleScenarioRef(name="File operations", description="File constraints."),
            )
        ]
    )

    result = await service._apply_plan(
        plan, existing_scenarios=[], existing_rules=[], source_uris=[], ctx=_ctx()
    )
    created = result["operations"][0]
    assert created["atomic_id"].startswith(f"{name}-")
    assert created["uri"].endswith(f"/{created['atomic_id']}.md")
    assert existing.uri not in result["written_uris"]


@pytest.mark.asyncio
async def test_rule_directory_identity_must_match_metadata():
    fs = _FS()
    rule = _stored_rule("misplaced")
    uri = rule.uri.replace("/file-operations/", "/other-scenario/")
    fs.documents[uri] = _stored_markdown(rule)
    service = RuleService()
    service.set_dependencies(
        fs=fs, resources=SimpleNamespace(), search=SimpleNamespace(), vlm_resolver=None
    )
    with pytest.raises(ValueError, match="identity does not match"):
        await service._load_rule(uri, _ctx())


@pytest.mark.asyncio
async def test_recall_reports_corrupt_scenario_instead_of_silently_omitting_it():
    uri = "viking://user/alice/memories/rules/file-operations/abstract.md"
    fs = SimpleNamespace(
        stat=AsyncMock(return_value={}),
        glob=AsyncMock(return_value={"matches": [uri]}),
        read=AsyncMock(return_value="invalid metadata"),
    )
    service = RuleService()
    service.set_dependencies(
        fs=fs,
        resources=SimpleNamespace(),
        search=SimpleNamespace(find=AsyncMock(return_value={"memories": [{"uri": uri}]})),
        vlm_resolver=None,
    )
    with pytest.raises(ProcessingError, match="Unreadable recalled Scenario"):
        await service._recall_scenarios("task", ctx=_ctx(), limit=5, score_threshold=0.1)


@pytest.mark.asyncio
async def test_scenario_layout_batch_write_and_directory_read_with_real_fs(service, monkeypatch):
    # Exercise actual file writes and reads without downstream semantic refresh.
    service.fs.compile_versions = None
    async_agfs = service.viking_fs._async_agfs

    async def to_handoff(lease):
        return {"covered_paths": [], "lease_ref": lease["lease_ref"]}

    async def handoff(lease):
        await async_agfs.pathlock_release(lease)

    monkeypatch.setattr(async_agfs, "pathlock_to_handoff", to_handoff)
    monkeypatch.setattr(async_agfs, "pathlock_handoff", handoff)
    monkeypatch.setattr(
        "openviking.storage.content_write.ContentWriteCoordinator._refresh_batch",
        AsyncMock(return_value=None),
    )
    ctx = RequestContext(user=service.user, role=Role.USER)
    rules = service.rules
    await rules._ensure_rule_directories(ctx)
    plan = RuleCompilePlan(
        mutations=[
            RuleMutation(
                operation="NEW",
                record=_record(),
                scenario=RuleScenarioRef(
                    name="File operations", description="Rules for modifying and deleting files."
                ),
            )
        ]
    )
    result = await rules._apply_plan(
        plan, existing_scenarios=[], existing_rules=[], source_uris=[], ctx=ctx
    )
    root = f"viking://user/{ctx.user.user_id}/memories/rules"
    summary_uri = f"{root}/file-operations/abstract.md"
    assert result["written_uris"] == [summary_uri, f"{root}/file-operations/confirm-delete.md"]
    scenario = await rules._load_scenario(summary_uri, ctx)
    loaded = await rules._load_rules_for_scenarios([scenario], ctx, strict=True)
    assert len(loaded) == 1
    assert loaded[0].record.content == _record().content
    assert loaded[0].scenario_id == "file-operations"
