# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Regression tests for legacy / non-conforming LLM response recovery (#3508).

Local models (e.g. llama.cpp) may return memory items as a root JSON list or a
legacy envelope (``{"memories": [...]}``) instead of the expected per-type
operations object. ``ExtractLoop._recover_legacy_operations`` re-routes such
items into the matching ``memory_type`` field so they are not silently dropped.
"""

from unittest.mock import MagicMock, patch

from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.merge_op import MergeOp
from openviking.session.memory.merge_op.base import FieldType
from openviking.session.memory.schema_model_generator import SchemaModelGenerator


def _field(name: str, merge_op: MergeOp = MergeOp.PATCH) -> MemoryField:
    return MemoryField(name=name, field_type=FieldType.STRING, merge_op=merge_op)


def _preferences_schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="preferences",
        fields=[
            _field("user", MergeOp.IMMUTABLE),
            _field("topic", MergeOp.IMMUTABLE),
            _field("content", MergeOp.PATCH),
        ],
    )


def _entities_schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="entities",
        fields=[
            _field("category", MergeOp.IMMUTABLE),
            _field("name", MergeOp.IMMUTABLE),
            _field("content", MergeOp.PATCH),
        ],
    )


def _mock_config(link_enabled: bool = False):
    """Mock get_openviking_config so tests don't need an ov.conf file."""
    cfg = type("Cfg", (), {"memory": type("Mem", (), {"link_enabled": link_enabled})()})
    return cfg


def _make_loop(schemas):
    """Build an ExtractLoop shell wired only enough for recovery."""
    gen = SchemaModelGenerator(list(schemas), template_context={"language": "en"})
    gen.generate_all_models()
    with patch(
        "openviking_cli.utils.config.get_openviking_config", return_value=_mock_config()
    ):
        ops_model = gen.create_structured_operations_model(role_scope=None)

    loop = object.__new__(ExtractLoop)
    loop.ctx = None
    loop._operations_model = ops_model
    # Mirror ExtractLoop.run(): top-level fields are the active memory_types
    # (links/delete_ids are handled separately and not part of expected_fields).
    loop._expected_fields = [s.memory_type for s in schemas]

    provider = MagicMock()
    provider.get_memory_schemas.return_value = list(schemas)
    loop.context_provider = provider
    return loop


class TestLegacyResponseRecovery:
    """Tests for ExtractLoop._recover_legacy_operations (#3508)."""

    def test_root_list_routed_by_type_discriminator(self):
        """A root list of typed items is bucketed into the right memory_type."""
        loop = _make_loop([_preferences_schema(), _entities_schema()])
        content = (
            '[{"type": "entity", "category": "people", "name": "sinking", '
            '"content": "admin"}, {"type": "preference", "user": "sinking", '
            '"topic": "drink", "content": "- likes black tea"}]'
        )

        recovered = loop._recover_legacy_operations(content)

        assert recovered is not None
        assert not recovered.is_empty()
        assert len(recovered.entities) == 1
        assert len(recovered.preferences) == 1
        assert recovered.entities[0].name == "sinking"
        assert recovered.preferences[0].topic == "drink"

    def test_legacy_memories_envelope_is_unwrapped(self):
        """The legacy {current_status, memories:[...]} envelope is recovered."""
        loop = _make_loop([_entities_schema()])
        content = (
            '{"current_status": "测试", "memories": ['
            '{"type": "entity", "category": "people", "name": "sinking", '
            '"content": "likes black tea"}]}'
        )

        recovered = loop._recover_legacy_operations(content)

        assert recovered is not None
        assert len(recovered.entities) == 1
        assert recovered.entities[0].content == "likes black tea"

    def test_single_active_schema_routes_untyped_items(self):
        """With one active schema, untyped root-list items route to it."""
        loop = _make_loop([_preferences_schema()])
        content = (
            '[{"user": "sinking", "topic": "drink", "content": "- tea"}, '
            '{"user": "sinking", "topic": "editor", "content": "- vscode"}]'
        )

        recovered = loop._recover_legacy_operations(content)

        assert recovered is not None
        assert len(recovered.preferences) == 2
        topics = {p.topic for p in recovered.preferences}
        assert topics == {"drink", "editor"}

    def test_singular_plural_discriminator_matching(self):
        """Singular 'entity'/'preference' map to plural memory_types."""
        loop = _make_loop([_preferences_schema(), _entities_schema()])

        # Singular discriminators.
        recovered = loop._recover_legacy_operations(
            '[{"type": "entity", "category": "c", "name": "n", "content": "x"}]'
        )
        assert recovered is not None
        assert len(recovered.entities) == 1

        recovered = loop._recover_legacy_operations(
            '[{"type": "preference", "user": "u", "topic": "t", "content": "x"}]'
        )
        assert recovered is not None
        assert len(recovered.preferences) == 1

    def test_page_id_auto_assigned_when_missing(self):
        """Legacy items lacking page_id get synthetic ids >= 100."""
        loop = _make_loop([_entities_schema()])
        recovered = loop._recover_legacy_operations(
            '[{"type": "entity", "category": "c", "name": "n1", "content": "a"},'
            ' {"type": "entity", "category": "c", "name": "n2", "content": "b"}]'
        )
        assert recovered is not None
        ids = [e.page_id for e in recovered.entities]
        assert all(i >= 100 for i in ids)
        assert len(set(ids)) == 2  # unique

    def test_conforming_response_is_not_recovered(self):
        """A dict already carrying an expected top-level field is left alone."""
        loop = _make_loop([_preferences_schema(), _entities_schema()])
        # Already a proper operations object -> recovery must return None.
        content = (
            '{"preferences": [{"page_id": 100, "user": "u", "topic": "t", '
            '"content": "x"}]}'
        )
        assert loop._recover_legacy_operations(content) is None

    def test_unmatched_items_yield_none(self):
        """Items whose type matches no active schema yield no recovery."""
        loop = _make_loop([_preferences_schema()])
        recovered = loop._recover_legacy_operations(
            '[{"type": "unknown_thing", "foo": "bar"}]'
        )
        assert recovered is None

    def test_empty_or_non_json_returns_none(self):
        loop = _make_loop([_preferences_schema()])
        assert loop._recover_legacy_operations("") is None
        assert loop._recover_legacy_operations("not json at all") is None
        assert loop._recover_legacy_operations("[]") is None
        assert loop._recover_legacy_operations("{}") is None

    def test_envelope_with_multiple_list_fields_not_guessed(self):
        """Ambiguous envelopes (several list fields) are not blindly guessed."""
        loop = _make_loop([_preferences_schema()])
        content = (
            '{"a": [{"x": 1}], "b": [{"y": 2}]}'  # two list-of-dicts fields
        )
        assert loop._recover_legacy_operations(content) is None


class TestSingularizeType:
    def test_plural_and_ies_forms(self):
        assert ExtractLoop._singularize_type("entities") == "entity"
        assert ExtractLoop._singularize_type("preferences") == "preference"
        assert ExtractLoop._singularize_type("cases") == "case"
        assert ExtractLoop._singularize_type("profile") == "profile"
        assert ExtractLoop._singularize_type("Entities") == "entity"
