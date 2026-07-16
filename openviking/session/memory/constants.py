# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

TRAJECTORY_MEMORY_TYPE = "trajectories"
EXPERIENCE_MEMORY_TYPE = "experiences"
TOOL_MEMORY_TYPE = "tools"
SKILL_MEMORY_TYPE = "skills"

# These schemas are extracted together from the archived conversation because
# they all require ToolPart evidence. Experiences remain a second phase that is
# derived from newly written trajectories.
DIRECT_EXECUTION_MEMORY_TYPES = frozenset(
    {TRAJECTORY_MEMORY_TYPE, TOOL_MEMORY_TYPE, SKILL_MEMORY_TYPE}
)
EXECUTION_MEMORY_TYPES = DIRECT_EXECUTION_MEMORY_TYPES | {EXPERIENCE_MEMORY_TYPE}
