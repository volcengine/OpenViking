#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Audit a local OpenViking agent experience corpus.

This helper is read-only. It is intended for TAU-2 / agent-memory corpus-prep
diagnostics where faster batch consolidation must still preserve useful
experience granularity.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from openviking.session.memory.experience_quality_audit import (
    ExperienceAuditConfig,
    audit_experience_dir,
    audit_to_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experience_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--watch-term",
        action="append",
        default=[],
        help=(
            "Optional term that should ideally have a standalone named experience when "
            "it appears in generated content. Repeatable; use only for diagnostics."
        ),
    )
    parser.add_argument("--duplicate-name-jaccard", type=float, default=0.6)
    parser.add_argument("--broad-source-threshold", type=int, default=4)
    parser.add_argument("--long-content-threshold", type=int, default=3500)
    args = parser.parse_args()

    config = ExperienceAuditConfig(
        duplicate_name_jaccard=args.duplicate_name_jaccard,
        broad_source_threshold=args.broad_source_threshold,
        long_content_threshold=args.long_content_threshold,
        watch_terms=tuple(args.watch_term or ()),
    )
    report = audit_experience_dir(args.experience_dir, config=config)
    payload = audit_to_json(report) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
