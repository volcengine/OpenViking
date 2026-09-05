# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared model-facing policy for safe memory reorganization."""

MEMORY_MERGE_POLICY = """
## Memory Reorganization Safety
- Search similarity, a shared category, or an overlapping topic only identifies candidates for
  review. It is never sufficient evidence that two memories have the same identity.
- Merge memories only when they have the same identity under the active memory schema. For
  real-world entities or events, require the same object or occurrence, or an explicit alias. For
  preferences, both the owner and the behavioral choice dimension must match.
- Keep distinct people, pets, works, artworks, products, possessions, places, events, and
  preferences separate even when they share attributes, participants, categories, or themes.
- Before deleting or replacing a source memory, account for every substantive atomic fact in it.
  Preserve each non-duplicate fact exactly once in a surviving destination. If identity is uncertain
  or any fact lacks one clear destination, keep the source separate.
- When a memory is too large or mixes multiple identities or choice dimensions, split it. Compact
  only duplicate wording; never satisfy a size target by dropping, weakening, or generalizing a
  concrete fact. Suggested length limits are readability targets, not permission to lose facts.
""".strip()
