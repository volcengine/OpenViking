from openviking.session.memory.memory_type_registry import MemoryTypeRegistry


def test_tool_and_skill_memory_types_are_registered_but_disabled():
    registry = MemoryTypeRegistry()
    enabled_names = set(registry.list_names(include_disabled=False))

    for memory_type in ("tools", "skills"):
        schema = registry.get(memory_type)
        assert schema is not None
        assert schema.enabled is False
        assert memory_type not in enabled_names
