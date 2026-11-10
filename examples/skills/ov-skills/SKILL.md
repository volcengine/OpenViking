---
name: ov-skills
description: Load when an agent needs to manage, install, update, remove, or validate OpenViking skills via the `ov skills` CLI. Trigger on explicit user requests about skill management, when the user mentions `ov skills`, `install skill`, `update skill`, `delete skill`, `validate skill`, or when an agent needs to discover what skills are available on the OpenViking server.
compatibility: OpenViking CLI configured at `~/.openviking/ovcli.conf`
version: 1.1.0
last_updated: 2026-09-18
---

# OpenViking (OV) Skills Management

The `ov skills` command group manages agent skills on OpenViking — including installation from local directories, Git repositories, GitHub URLs, or raw content; listing, searching, inspecting, updating, and removing skills; and validating skill format locally. `ov add-skill` is the same command as `ov skills add` and takes the same flags.

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
| `subcommand` | yes | One of `list` (alias `ls`), `find`, `add`, `show`, `update`, `remove` (aliases `rm`, `delete`), `validate` |
| `argument` | conditional | Skill name(s) for `show`/`update`/`remove`, query string for `find`, source path/URL or raw SKILL.md text for `add`, local path for `validate` |
| `root` | no | Skill root URI passed with `-p`: `viking://~/skills` (private) or `viking://agent/skills` (shared with the account). `add` defaults to the private root; `list`/`find` search both roots; `show`/`update`/`remove` resolve a name to the private copy first, then the shared one |
| `level` | no | `-L`/`--level` for `show`/`find`: `0` (abstract), `1` (overview), `2` (full SKILL.md) |

## Workflow

1. Identify the user's intent and map to the correct `ov skills <subcommand>`.
2. Determine the source type for `add` (local dir, local file, Git URL, GitHub tree URL, raw content). When a source may hold several skills, run `ov skills add <source> --list` first and pick skills with `--skill`.
3. Construct the command with flags from `examples/commands.md` only. The long form of `-p` is `--uri` for `list`/`find`/`show` and `--parent-auto-create` for `add`/`update`/`remove`. Add the global `-o json` for machine-readable output.
4. Execute and report results.

## Permissions

- `ov skills add` may download and install external code; verify the source when the user provides an untrusted URL.
- `ov skills update` and `ov skills remove` always ask for confirmation, and so does `ov skills add` when the source resolves to more than one skill. Without a terminal the command fails with `requires confirmation. Pass --yes to skip the prompt.`; confirm with the user in the conversation, then rerun with `--yes`.
- `ov skills remove --all` is destructive; confirm with the user before executing.
- `ov skills update` replaces skill content in-place; the old version is not retained.

## Output

- CLI output returned directly to the user: tables by default, JSON with `-o json` (`show` also accepts `--format json`).
- Errors surfaced with suggested fixes.

## Verification

- After `add`, `list` should include the new skill. Processing may continue in the background unless `--wait` was passed; check it with `ov task status <task_id>`.
- After `remove`, `list` should no longer include the removed skill.
- After `update`, `show <name> --level 2` should show the fresh SKILL.md, and `show <name> --source` the recorded source.

## Boundaries

- Do not invent skill names or URLs. Use what the user provides.
- Do not execute `ov skills remove --all` without explicit user confirmation.
- Do not run `ov skills remove` without skill names from an agent shell: it opens an interactive picker that needs a terminal.
- Remove skills with `ov skills remove`, not `ov rm`: only `ov skills remove` also deletes the skill's privacy configuration.

## Runtime Resources

- `docs/upgrade-guide.md` — moving from generic filesystem commands to `ov skills`.
- `examples/commands.md` — common command patterns by scenario.
- `docs/source-types.md` — deep dive on supported source formats and URL patterns.
