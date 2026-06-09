---
name: ov-resources
description: Load when an agent needs to add, manage, browse, search, modify, or remove resources in OpenViking. Trigger on explicit user requests about resource management or context search, when the user mentions `ov add-resource`, `ov task watch`, `ov export`, `ov import`, `ov backup`, `ov restore`, `ov ls`, `ov tree`, `ov read`, `ov write`, `ov mkdir`, `ov rm`, `ov mv`, `ov find`, `ov search`, `ov grep`, `ov glob`, or when an agent needs to inspect, search, or organize the `viking://resources/` namespace.
compatibility: OpenViking CLI configured at `~/.openviking/ovcli.conf`
version: 1.0.0
last_updated: 2026-06-08
---

# OpenViking (OV) Resource Management

The `ov` command group for resources covers adding external knowledge, filesystem operations, scheduled refresh (watch tasks), and ovpack import/export/backup/restore.

## Goal

Guide an agent to correctly invoke resource and filesystem commands without guessing flags, URI semantics, or processing behavior.

## Load When

- User explicitly requests resource operations: `ov add-resource`, `ov task watch`, `ov export`, `ov import`, `ov backup`, `ov restore`.
- User asks to browse, read, write, create, move, or delete files under `viking://resources/`.
- User asks to search for context: `ov find`, `ov search`, `ov grep`, `ov glob`.
- User asks to create or manage relations between resources (`ov link`, `ov relations`, `ov unlink`).
- Agent needs to list, inspect, or search the resource tree to find context.

## Inputs

| Name | Required | Description |
|---|---|---|
| `subcommand` | yes | Resource command: `add-resource`, `task watch`, `export`, `import`, `backup`, `restore`, filesystem command: `ls`, `tree`, `read`, `write`, `mkdir`, `rm`, `mv`, `grep`, `glob`, `link`, `relations`, `unlink`, or search command: `find`, `search` |
| `target` | conditional | File path, URL, Viking URI, or query string |
| `flags` | no | Command-specific flags like `--wait`, `--to`, `--parent`, `--recursive`, `--watch-interval` |

## Workflow

1. Identify the user's intent and map to the correct `ov <subcommand>`.
2. Resolve the target (local path, URL, or Viking URI).
3. Construct the command with appropriate flags.
4. Execute and report results.

## Permissions

- `ov add-resource` from external URLs may download untrusted content; verify the source when the user provides an untrusted URL.
- `ov rm --recursive` is destructive; confirm with the user before executing on large directories.
- `ov export`, `ov import`, `ov backup`, `ov restore` require ROOT or ADMIN permissions.
- `ov write` replaces file content in-place; the old version is not retained.

## Output

- CLI output returned directly to the user.
- Errors surfaced with suggested fixes and relevant flag guidance.

## Verification

- After `add-resource`, `ov ls` or `ov tree` should show the new resource under the expected URI.
- After `rm`, `ov ls` should no longer list the removed path.
- After `write`, `ov read` should reflect the new content.
- After `ov import` or `ov restore`, `ov tree` should show the imported structure.

## Boundaries

- Do not invent paths or URLs. Use what the user provides.
- Do not execute `ov rm --recursive` on broad paths like `viking://resources/` without explicit user confirmation.
- Do not treat `ov add-skill` or `ov skills` as equivalent to resource commands; skill management is handled by `ov-skills`.
- Do not treat `ov add-memory` as a resource command; memory management is out of scope for this skill.

## Runtime Resources

- `docs/add-resource.md` — detailed resource ingestion docs, source types, and async processing.
- `docs/filesystem.md` — filesystem operations reference (ls, tree, read, write, mkdir, rm, mv, grep, glob, link, relations, unlink).
- `docs/search.md` — semantic search (`ov find`, `ov search`) and search combination strategies.
- `docs/watch-management.md` — watch task lifecycle (create, pause, resume, trigger, update, remove).
- `docs/ovpack.md` — ovpack export/import/backup/restore reference.
- `examples/commands.md` — common command patterns by scenario.
