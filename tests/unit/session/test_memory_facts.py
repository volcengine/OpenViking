from pathlib import Path

import pytest

from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils import deserialize_metadata, serialize_with_metadata


FACTS_TEMPLATE = (
    Path(__file__).parents[3] / "openviking" / "prompts" / "templates" / "memory" / "facts.yaml"
)


def _load_facts_schema():
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.load_from_yaml(str(FACTS_TEMPLATE))
    return registry, registry.get("facts")


def test_facts_schema_uses_stable_upsert_uri():
    _registry, schema = _load_facts_schema()

    assert schema is not None
    assert schema.enabled is True
    assert schema.operation_mode == "upsert"
    assert schema.filename_template == "{{ fact_key }}.md"
    assert "extract_context.read_message_ranges" not in schema.content_template


@pytest.mark.asyncio
async def test_fact_upsert_preserves_unspecified_existing_fields():
    registry, schema = _load_facts_schema()
    assert schema is not None

    uri = "viking://user/default/memories/facts/user_pre_1920_coin_count.md"
    existing = serialize_with_metadata(
        {
            "fact_key": "user_pre_1920_coin_count",
            "statement": "User owns 37 pre-1920 coins.",
            "subject": "user",
            "relation": "owns_count",
            "object": "37 pre-1920 coins",
            "qualifiers": '{"collection":"pre-1920 coins"}',
            "time": "2023-05-29",
            "ranges": "1",
            "confidence": 0.9,
        },
        content_template=schema.content_template,
    )

    class FakeVikingFS:
        def __init__(self):
            self.files = {uri: existing}

        async def read_file(self, read_uri, ctx=None):
            return self.files[read_uri]

        async def write_file(self, write_uri, content, ctx=None):
            self.files[write_uri] = content

    fake_fs = FakeVikingFS()
    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fake_fs

    edited = await updater._apply_edit(
        {
            "fact_key": "user_pre_1920_coin_count",
            "statement": "User owns 38 pre-1920 coins after adding a 1915-S Barber quarter.",
            "subject": "user",
            "relation": "owns_count",
            "object": "38 pre-1920 coins",
            "time": "2023-05-30",
            "ranges": "2",
            "confidence": 0.95,
        },
        uri,
        ctx=None,
        memory_type="facts",
    )

    assert edited is True
    metadata = deserialize_metadata(fake_fs.files[uri])
    assert metadata["statement"] == (
        "User owns 38 pre-1920 coins after adding a 1915-S Barber quarter."
    )
    assert metadata["object"] == "38 pre-1920 coins"
    assert metadata["qualifiers"] == '{"collection":"pre-1920 coins"}'
    assert metadata["ranges"] == "2"
