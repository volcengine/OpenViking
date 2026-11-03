# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
import yaml

from openviking.session.memory.account_templates import (
    EDITABLE_MEMORY_TEMPLATE_FIELDS,
    memory_template_data,
    normalize_account_memory_template_write,
    validate_account_memory_template_transfer,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking_cli.exceptions import InvalidArgumentError


@pytest.fixture(scope="module")
def registry() -> MemoryTypeRegistry:
    return MemoryTypeRegistry()


@pytest.mark.parametrize("memory_type", EDITABLE_MEMORY_TEMPLATE_FIELDS)
def test_generic_write_accepts_complete_template_with_editable_changes(registry, memory_type):
    data = memory_template_data(registry.get(memory_type))
    data["description"] += "\nAccount instructions."

    normalize_account_memory_template_write(
        f"viking://system/memory/memory_templates/custom/{memory_type}.yaml",
        yaml.safe_dump(data),
        "replace",
        registry=registry,
    )


@pytest.mark.parametrize("memory_type", EDITABLE_MEMORY_TEMPLATE_FIELDS)
def test_generic_write_rejects_locked_field_change(registry, memory_type):
    data = memory_template_data(registry.get(memory_type))
    data["filename_template"] = "unmanaged-{{ memory_type }}.md"

    with pytest.raises(InvalidArgumentError, match="filename_template is locked"):
        normalize_account_memory_template_write(
            f"viking://system/memory/memory_templates/custom/{memory_type}.yaml",
            yaml.safe_dump(data),
            "replace",
            registry=registry,
        )


def test_generic_write_requires_complete_template(registry):
    with pytest.raises(InvalidArgumentError, match="complete deployment template"):
        normalize_account_memory_template_write(
            "viking://system/memory/memory_templates/custom/profile.yaml",
            yaml.safe_dump({"memory_type": "profile", "description": "Account profile"}),
            "replace",
            registry=registry,
        )


def test_generic_write_rejects_append_for_managed_template(registry):
    data = memory_template_data(registry.get("profile"))

    with pytest.raises(InvalidArgumentError, match="append is not supported"):
        normalize_account_memory_template_write(
            "viking://system/memory/memory_templates/custom/profile.yaml",
            yaml.safe_dump(data),
            "append",
            registry=registry,
        )


def test_generic_write_rejects_registered_noneditable_template(registry):
    private_registry = MemoryTypeRegistry(load_schemas=False)
    private_registry.register(
        registry.get("profile").model_copy(update={"memory_type": "private"}, deep=True)
    )

    with pytest.raises(InvalidArgumentError, match="not editable"):
        normalize_account_memory_template_write(
            "viking://system/memory/memory_templates/custom/private.yaml",
            "arbitrary",
            "replace",
            registry=private_registry,
        )


@pytest.mark.parametrize(
    "uri",
    [
        "viking://system/memory/memory_templates/custom/customer-defined.yaml",
        "viking://system/memory/memory_templates/custom/team/profile.yaml",
    ],
)
def test_generic_write_keeps_unmanaged_custom_files_unrestricted(registry, uri):
    assert (
        normalize_account_memory_template_write(uri, "arbitrary", "append", registry=registry)
        == "arbitrary"
    )


def test_generic_write_roundtrip_replaces_server_timestamp(registry):
    data = memory_template_data(registry.get("profile"))
    data["description"] += "\nAccount instructions."
    data["_updated_at"] = "forged"

    normalized = normalize_account_memory_template_write(
        "viking://system/memory/memory_templates/custom/profile.yaml",
        yaml.safe_dump(data),
        "replace",
        registry=registry,
    )

    stored = yaml.safe_load(normalized)
    assert stored.pop("_updated_at") != "forged"
    assert stored == {key: value for key, value in data.items() if key != "_updated_at"}


def test_transfer_rejects_registered_template_target_only(registry):
    with pytest.raises(InvalidArgumentError, match="must be updated through write"):
        validate_account_memory_template_transfer(
            "viking://system/memory/memory_templates/custom/profile.yaml",
            registry=registry,
        )

    validate_account_memory_template_transfer(
        "viking://system/memory/memory_templates/custom/customer-defined.yaml",
        registry=registry,
    )
