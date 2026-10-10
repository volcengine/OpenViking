# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reserved derived-record discriminator in the unified context collection."""

from openviking.storage.expr import In, RawDSL

MEMORY_ASSOCIATION_TYPE = "memory_association"
# Keep the previous experimental discriminator isolated when reopening its data.
# New writes always use MEMORY_ASSOCIATION_TYPE.
ASSOCIATION_RECORD_TYPES = (MEMORY_ASSOCIATION_TYPE, "entity_link")


def association_records():
    return In("type", list(ASSOCIATION_RECORD_TYPES))


def context_records():
    # Negative membership keeps legacy records with empty/missing type visible.
    return RawDSL({"op": "must_not", "field": "type", "conds": list(ASSOCIATION_RECORD_TYPES)})
