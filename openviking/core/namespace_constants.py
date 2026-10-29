# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Constants shared by runtime namespace policy and package build tooling."""

SYSTEM_ROOT_URI = "viking://system"
SYSTEM_COMPILE_ROOT = f"{SYSTEM_ROOT_URI}/compile"
SYSTEM_EXAMPLE_SKILLS_ROOT = f"{SYSTEM_COMPILE_ROOT}/example-skills"
SYSTEM_MEMORY_ROOT = f"{SYSTEM_ROOT_URI}/memory"
SYSTEM_MEMORY_TEMPLATES_ROOT = f"{SYSTEM_MEMORY_ROOT}/memory_templates"
SYSTEM_MEMORY_BUILTIN_ROOT = f"{SYSTEM_MEMORY_TEMPLATES_ROOT}/builtin"
SYSTEM_MEMORY_CUSTOM_ROOT = f"{SYSTEM_MEMORY_TEMPLATES_ROOT}/custom"

SYSTEM_ASSET_ROOT_MAPPINGS = (
    ("compile/example-skills", SYSTEM_EXAMPLE_SKILLS_ROOT),
    ("memory/memory_templates/builtin", SYSTEM_MEMORY_BUILTIN_ROOT),
)
SYSTEM_FIXED_DIRECTORIES = frozenset(
    {
        SYSTEM_ROOT_URI,
        SYSTEM_COMPILE_ROOT,
        SYSTEM_EXAMPLE_SKILLS_ROOT,
        SYSTEM_MEMORY_ROOT,
        SYSTEM_MEMORY_TEMPLATES_ROOT,
        SYSTEM_MEMORY_BUILTIN_ROOT,
        SYSTEM_MEMORY_CUSTOM_ROOT,
    }
)
SYSTEM_OFFICIAL_ASSET_ROOTS = frozenset(
    {
        SYSTEM_EXAMPLE_SKILLS_ROOT,
        SYSTEM_MEMORY_BUILTIN_ROOT,
    }
)
