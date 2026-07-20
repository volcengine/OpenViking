#!/usr/bin/env python3
"""Store one generated periodic report as OpenViking Resources."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone

import openviking as ov


def _digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_report_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None:
        raise ValueError(
            "report ID must use only letters, digits, dot, dash, or underscore"
        )
    return value


async def archive_report(*, path: str, report_id: str) -> dict[str, object]:
    report_id = _safe_report_id(report_id)
    client = ov.AsyncOpenViking(path=path)
    await client.initialize()
    try:
        root = f"viking://resources/reports/{report_id}"
        report_uri = f"{root}/report.md"
        manifest_uri = f"{root}/manifest.json"
        report = (
            f"# Periodic report {report_id}\n\n"
            "- Completed: published one verified outcome.\n"
            "- Next: validate the next delivery milestone.\n"
        )
        manifest = {
            "schema_version": "periodic_report_resource_example_v0",
            "report_id": report_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "report_uri": report_uri,
            "report_digest": _digest(report),
        }
        manifest_text = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"

        # The caller chooses this ordering: the manifest is written last.
        await client.write(report_uri, report, mode="create", wait=False)
        if await client.read(report_uri) != report:
            raise RuntimeError("report readback did not match")
        await client.write(manifest_uri, manifest_text, mode="create", wait=False)
        if await client.read(manifest_uri) != manifest_text:
            raise RuntimeError("manifest readback did not match")

        return {
            "report_uri": report_uri,
            "manifest_uri": manifest_uri,
            "report_digest": manifest["report_digest"],
            "manifest_digest": _digest(manifest_text),
            "readback_verified": True,
        }
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="./data")
    parser.add_argument("--report-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(archive_report(path=args.path, report_id=args.report_id)),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
