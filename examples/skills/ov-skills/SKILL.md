---
name: ov-skills
description: Load when an agent needs to manage, install, update, remove, or validate OpenViking skills via the `ov skills` CLI. Trigger on explicit user requests about skill management, when the user mentions `ov skills`, `install skill`, `update skill`, `delete skill`, `validate skill`, or when an agent needs to discover what skills are available on the OpenViking server.
compatibility: OpenViking CLI configured at `~/.openviking/ovcli.conf`
version: 1.0.0
last_updated: 2026-06-08
---

# OpenViking (OV) Skills Management

The `ov skills` command group manages agent skills on OpenViking — including installation from local directories, Git repositories, GitHub URLs, or raw content; listing, searching, inspecting, updating, and removing skills; and validating skill format locally.

## Goal

Guide an agent to correctly invoke `ov skills` subcommands for skill lifecycle operations without guessing flags, source types, or update semantics.

## Load When

- User explicitly requests skill management: `ov skills ...`, `install skill`, `update skill`, `delete skill`, `remove skill`.
- User asks to validate a SKILL.md or skill directory.
- User asks to find or search installed skills.
- User asks to inspect a skill's content, files, or source.
- Agent needs to discover available skills before selecting one.

## Inputs

| Name | Required | Description |
|---|---|---|
| `subcommand` | yes | One of `list`, `find`, `add`, `show`, `update`, `remove`, `validate` |
| `target` | conditional | Skill name, query string, source path/URL, or directory path |
| `level` | no | Content level for `show`/`find`: `0` (abstract), `1` (overview), `2` (full) |

## Workflow

1. Identify the user's intent and map to the correct `ov skills <subcommand>`.
2. Determine the source type for `add` (local dir, local file, Git URL, GitHub tree URL, raw content).
3. Construct the command with appropriate flags.
4. Execute and report results.

## Permissions

- `ov skills add` may download and install external code; verify the source when the user provides an untrusted URL.
- `ov skills remove --all` is destructive; confirm with the user before executing.
- `ov skills update` replaces skill content in-place; the old version is not retained.

## Output

- CLI output returned directly to the user.
- Errors surfaced with suggested fixes.

## Verification

- After `add`, `list` should include the new skill.
- After `remove`, `list` should no longer include the removed skill.
- After `update`, `show <name> --source` should reflect fresh content.

## Boundaries

- Do not invent skill names or URLs. Use what the user provides.
- Do not execute `ov skills remove --all` without explicit user confirmation.
- Do not treat `ov add-skill` (legacy resource command) as equivalent to `ov skills add`.

## Runtime Resources

- `docs/upgrade-guide.md` — migrating from legacy `ov add-skill` / resource commands.
- `examples/commands.md` — common command patterns by scenario.
- `docs/source-types.md` — deep dive on supported source formats and URL patterns.
