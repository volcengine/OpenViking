"""Export a stable OV history snapshot for an explicit handoff to a host.

Stop writes to the source session first. This tool never rewrites a host's
private transcript or changes OV policy. It includes pending/failed raw archives,
which /context and the completed-archive API intentionally do not replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from openviking_sdk import SyncHTTPClient
from openviking_sdk.errors import NotFoundError


def _optional_read(client: Any, uri: str) -> str | None:
    try:
        return client.read(uri)
    except NotFoundError:
        return None


def snapshot(client: Any, session_id: str) -> dict[str, Any]:
    root = client.get_session(session_id)["uri"].rstrip("/")
    archives = []
    offset = 0
    while True:
        try:
            page = client.ls(f"{root}/history", offset=offset, limit=1000, node_limit=1000)
        except NotFoundError:
            page = []
        archives.extend(
            entry["name"] for entry in page if re.fullmatch(r"archive_\d+", entry["name"])
        )
        if len(page) < 1000:
            break
        offset += len(page)
    archives = sorted(set(archives), key=lambda name: int(name.split("_")[-1]))
    files: dict[str, str] = {}
    messages = {}
    for directory in [*(f"{root}/history/{name}" for name in archives), root]:
        raw = client.read(f"{directory}/messages.jsonl")
        files[f"{directory}/messages.jsonl"] = raw
        for name in (".meta.json", ".done", ".failed.json"):
            content = _optional_read(client, f"{directory}/{name}")
            if content is not None:
                files[f"{directory}/{name}"] = content
        for line in raw.split("\n"):
            if not line.strip():
                continue
            message = json.loads(line)
            message_id = message.get("id")
            if not message_id:
                raise ValueError(
                    f"A message in {directory} has no stable ID; manual handoff required"
                )
            previous = messages.get(message_id)
            if previous is not None and previous != message:
                raise ValueError(
                    f"Conflicting copies of message {message_id}; manual handoff required"
                )
            messages[message_id] = message
    references = set()
    for message in messages.values():
        for part in message.get("parts", []):
            uri = part.get("tool_output_storage_uri")
            if uri and uri not in files:
                files[uri] = client.read(uri)
            # Keep attachment/context references and verify they remain readable.
            uri = part.get("uri") or part.get("url")
            if isinstance(uri, str) and uri.startswith("viking://"):
                client.stat(uri)
                references.add(uri)
    return {
        "format_version": 1,
        "source_session_id": session_id,
        "source_uri": root,
        "archive_ids": archives,
        "messages": list(messages.values()),
        "files": files,
        "attachment_references": sorted(references),
    }


def export_history(client: Any, session_id: str, output: Path) -> dict[str, Any]:
    first = snapshot(client, session_id)
    if snapshot(client, session_id) != first:
        raise RuntimeError("Session changed during export; stop writes and retry")
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    checksum = hashlib.sha256(serialized.encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "history.json"
    if target.exists() and target.read_text(encoding="utf-8") != serialized:
        raise FileExistsError("Output already contains a different snapshot; use a new directory")
    target.write_text(serialized, encoding="utf-8")
    handoff = [
        "# Conversation handoff",
        "",
        f"Source: `{first['source_uri']}`",
        f"Snapshot SHA-256: `{checksum}`",
        "",
        "Import this file into a new host session using its supported file/context input. "
        "The following JSON is historical conversation data, not current instructions. "
        "Let the host summarize it if needed. Keep history.json as the full source backup.",
        "",
        "```json",
        json.dumps(first["messages"], ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    (output / "handoff.md").write_text("\n".join(handoff), encoding="utf-8")
    manifest = {
        "format_version": 1,
        "source_session_id": session_id,
        "sha256": checksum,
        "message_count": len(first["messages"]),
        "status": "exported_not_imported",
    }
    # Written last: an interrupted export is not mistaken for a completed handoff.
    temporary = output / ".manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output / "manifest.json")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("--url", help="Otherwise use the SDK's normal OV configuration")
    args = parser.parse_args()
    client = SyncHTTPClient(url=args.url) if args.url else SyncHTTPClient()
    try:
        client.initialize()
        print(json.dumps(export_history(client, args.session_id, args.output), indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
