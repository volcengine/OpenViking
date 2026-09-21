# VikingBot Skills

A Skill describes when and how to perform a task in `SKILL.md`, alongside any supporting resources. The model reads those instructions and calls tools already registered in VikingBot. A Skill does not register tools or provide an execution backend.

VikingBot supports local Skills in the active Workspace and remote Skills stored in OpenViking. This chapter describes the current implementation; see [RFC #3656](https://github.com/volcengine/OpenViking/discussions/3656) for the remote Skill design background.

## Local and Remote Skills

| Area | Local Skill | OpenViking remote Skill |
|------|-------------|-------------------------|
| Storage | `<active-workspace>/skills/<name>/` | Canonical Skill URI returned by OpenViking |
| Discovery | Scan Workspace Skill directories | Call `find_skills` with the current user query; retrieve L0 summaries only |
| Default context | Name, description, local path | Name, description, `SKILL.md` URI, read tool |
| Read instructions | `read_file` | `openviking_multi_read`; reading the definition activates it |
| `always` | Can inject the full instructions every turn | Does not enable persistent or automatic activation |
| Requirements | Check the Bot process environment; hide unavailable summaries | Check the execution sandbox before the first ordinary tool call after activation |
| `allowed-tools` | Not enforced by the local loader | Runtime enforces restrictions on tools for the current turn |
| Resources | Already in the Workspace | Read text by URI; download the package when a tool needs local paths |
| Lifetime | Files persist; rebuild context each turn | Activate for each user message; remove execution copies at turn completion |

Both types remain subject to Bot, channel, request, and sandbox policies. Disabling OpenViking tools does not disable local Skill loading.

## Using a Local Skill

Place the Skill in the **active Workspace**, such as `<workspace>/shared/skills/` in the default `shared` mode. See [Workspace and Agent Customization](./02-agent-capabilities.md#workspace-and-agent-customization) for root paths and isolation modes.

```text
<active-workspace>/skills/report-summary/
├── SKILL.md
├── scripts/
│   └── summarize.py
├── references/
│   └── report-format.md
└── assets/
    └── template.md
```

Discovery lists only immediate subdirectories containing `SKILL.md`; the directory name identifies a local Skill. Each turn normally includes summaries of Skills whose requirements are met. The model then reads `skills/report-summary/SKILL.md` when needed. A local Skill with `always: true` injects its instructions without frontmatter directly into context; supporting files still load on demand.

`bot.skills` selects bundled templates to copy into the active Workspace. It is not an allowlist for runtime discovery. Initialization copies selected templates and skips existing directories with the same name; manually added Workspace Skills do not need to appear in this list. Explicit loading by name prefers Workspace files over bundled templates, while normal summary discovery scans the Workspace.

Tool paths in local Skills are relative to the Workspace, for example `python3 skills/report-summary/scripts/summarize.py`. Reading `SKILL.md` does not change the working directory.

## Using a Remote Skill

### Setup

1. Configure an available connection as described in [OpenViking Integration](./04-openviking-integration.md#connection-modes), and ensure the Bot's current identity can read the Skill.
2. Upload the Skill to that OpenViking service. Upload the directory when it contains supporting files:

   ```bash
   ov add-skill ./skills/report-summary/
   ```

   Configure the CLI for the target service and an appropriately authorized identity. Save the returned URI. If the operation returns a background `task_id`, check progress with `ov task status TASK_ID`. See the [OpenViking Skills API](../../../../docs/en/api/04-skills.md) for other import methods.
3. Keep `ov_tools_enable: true` on the current channel. `openviking_multi_read` must be registered and must not appear in `disabled_tools`.
4. Describe a task to let the Bot select a Skill from remote summaries, or explicitly ask it to read a particular canonical `SKILL.md` URI.

No additional Remote Skill switch or `load_skill` tool is needed. `bot.remote_skills` tunes retrieval and resource handling; it has no `enabled`, `default_materialize`, or `materialize_mode` fields.

### Discovery, Activation, and Execution

Each ordinary user message that needs a reply creates a `SkillRuntimeContext` spanning all model and tool iterations for that message:

```text
User query → find_skills → local + remote summaries
  → openviking_multi_read(SKILL.md) → validate and activate
  → refresh tool schemas → check requirements → resolve resources → execute
  → save results and usage → close runtime and clean request copies
```

Name collisions prefer local Workspace Skills, then OpenViking user Skills, then shared Skills (`viking://agent/skills/`). A local directory shadows a remote candidate even if unmet requirements hide its local summary. Explicit URIs bypass name selection. Use canonical URIs actually returned by the service; a name alone is not a unique identity across users.

For example, read a user Skill at the URI returned by the service:

```json
{
  "name": "openviking_multi_read",
  "arguments": {
    "uris": ["viking://user/alice/skills/report-summary/SKILL.md"]
  }
}
```

Activation parses frontmatter and calls `get_skill(include_integrity=true)` to validate the content, canonical URI, revision, and file manifest. It does not download auxiliary files. Instructions returned to the model include bindings from package-relative paths to canonical resource URIs. Activation requires the complete definition. The read tool currently refuses full reads of files larger than 512 KiB by default, so move lengthy reference material into auxiliary files; partial reads cannot activate a definition.

If the model requests activation and other tools in the same batch, activation runs first. Other calls return `SKILL_CONTEXT_UPDATED` so the model can retry with refreshed schemas. If activation fails, ordinary calls in that batch return `SKILL_ACTIVATION_FAILED` without executing.

## SKILL.md and Metadata

Use YAML frontmatter and place VikingBot extensions under `metadata.vikingbot`. This remote Skill example declares Python command permissions and a runtime requirement. Both `scripts/summarize.py` and `references/report-format.md` must exist in the package.

```markdown
---
name: report-summary
description: Summarize a report when the user requests a report review.
allowed-tools: Bash(python3 *)
tags: [reporting]
metadata:
  author: example-team
  version: "1.0"
  vikingbot:
    always: false
    requires:
      bins: [python3]
---

# Report summary

Read references/report-format.md with openviking_multi_read.
Run python3 scripts/summarize.py and summarize its output.
```

### Field Reference

| Field | Type / default | Current behavior |
|-------|----------------|------------------|
| `name` | String; required remotely | Must match the Skill name in the remote URI; local identity remains the directory name, so keep them consistent |
| `description` | String; required remotely | Describes applicable tasks for model selection; local summaries fall back to the directory name when absent |
| `allowed-tools` | Space-separated string or compatible string list; undeclared by default | Remote tool policy, described below; not enforced by the local loader |
| `tags` | String list; `[]` | Classification data retained by OpenViking; does not change Bot permissions or activation |
| `metadata` | YAML object or compatible JSON string | Extension metadata container |
| `metadata.vikingbot` | Object | VikingBot extension scope; when present, extension fields are read from this object |
| `metadata.vikingbot.always` | Boolean; `false` | Local only: inject complete instructions each turn when requirements are met |
| `always` | Top-level boolean; `false` | Local compatibility form; ORed with scoped `always`; ignored by the remote runtime |
| `metadata.vikingbot.requires.bins` | Command name list; `[]` | All commands must be available; no automatic installation |
| `metadata.vikingbot.requires.env` | Environment variable name list; `[]` | All variables must pass environment checks; declare names, not values |
| `metadata.vikingbot.emoji` | Optional string | Informational field used by some bundled templates; the current summary builder does not read it |
| `metadata.vikingbot.os` | Optional string list | Platform information in some templates, such as `[darwin, linux]`; the current loader does not filter platforms with it |
| `metadata.vikingbot.install` | Optional object list | Installation hints in some templates, commonly containing `id`, `kind`, `bins`, `label`, `formula`, or `package`; not executed automatically |
| Other metadata, such as `author`, `version` | Custom | Informational; the Bot does not use it to control execution or version its cache |

The frontmatter permission field must be **`allowed-tools`**. `allowed_tools` is the parsed structured-data field in OpenViking and does not replace the hyphenated field in `SKILL.md`. Use actual YAML booleans `true` / `false`, not strings such as `"false"`.

Both older metadata forms below are supported. Prefer the scoped form for new Skills to keep Bot extensions separate from other systems' metadata:

```yaml
metadata:
  requires:
    bins: [python3]
```

```yaml
metadata: '{"vikingbot":{"requires":{"bins":["python3"]}}}'
```

### Requirement Checks

The local loader searches the Bot process `PATH` for commands and requires nonempty environment variable values. Missing requirements remove a Skill from both summaries and Always content. This is discovery filtering, not enforcement inside the execution sandbox.

The remote runtime checks commands inside the sandbox that executes tools. Environment variables only need to be defined; empty values count as present, and values are neither read nor logged. Variable names must match `[A-Za-z_][A-Za-z0-9_]*`. Each active Skill is checked once per turn before its first ordinary tool call, regardless of whether files need downloading. The `openviking_multi_read` transport does not trigger these checks. Remote requirements also accept a single string for `bins` / `env`; lists work with both loaders.

### Remote allowed-tools

| Declaration | Meaning |
|-------------|---------|
| Omit `allowed-tools` | Do not further restrict the existing tool set |
| `allowed-tools: []` or `allowed-tools: ""` | Deny ordinary task tools; retain the `openviking_multi_read` transport |
| `allowed-tools: Read Bash` | Allow `read_file` and `exec` |
| `allowed-tools: Bash(python3 *)` | Allow only a single `exec` command matching this pattern |
| `allowed-tools: Bash(git:*)` | Compatible colon form that can match `git ...` |

The runtime supports these aliases. Registered tool names such as `openviking_search` or `mcp_<server>_<tool>` can also be used directly.

| Skill spelling | VikingBot tool |
|----------------|----------------|
| `Bash` / `exec` | `exec` |
| `Read` / `read_file` | `read_file` |
| `Write` / `write_file` | `write_file` |
| `Edit` / `edit_file` | `edit_file` |
| `Glob` | `openviking_glob` |
| `Grep` | `openviking_grep` |
| `WebFetch` / `web_fetch` | `web_fetch` |
| `WebSearch` / `web_search` | `web_search` |
| `spawn` | `spawn` |

When multiple remote Skills are active, ordinary tool permissions are the intersection of every active Skill's policy, subject to existing Bot/channel/request policies. Restrictions cover subsequent ordinary tool calls throughout the turn, including calls that do not reference package files. A Skill cannot restore disabled tools. `openviking_multi_read` is exempt from the Skill policy intersection but must still be available in the Bot.

Multiple Bash patterns within a Skill are alternatives; patterns across Skills must all be satisfied. Patterns match the entire command string. Constrained Bash rejects pipes, redirection, command chaining, newlines, backticks, and `$()` shell substitutions. Plain `Bash` does not add this command matching restriction. Parenthesized argument constraints on other tools, such as `Read(...)`, are currently rejected. Invoke scripts through an explicit interpreter such as `python3` or `bash`; do not depend on executable file permissions after download.

## Remote Resources and Local Paths

After activation, use `openviking_multi_read` for text references. Materialization downloads the Skill package into the sandbox and rewrites arguments only when `exec` references a packaged file or a tool's declared input needs a local file/directory.

| Example input | Behavior |
|---------------|----------|
| `openviking_multi_read` of `references/report-format.md` | Resolve a unique active-package match to a canonical URI and read remotely |
| `exec` with `python3 scripts/summarize.py` | Match the manifest, download the package, and rewrite the script path |
| Full `viking://.../assets/template.md` in an active package | Read remotely or provide a local path according to the consuming tool |
| `scripts/summarize.py` present in multiple active packages | Reject the ambiguous path; require its canonical URI |
| Ordinary Workspace file `workspace:input/report.csv` | Remove `workspace:` before passing it to the local file tool or `exec` |
| Native absolute path in the current sandbox, HTTP / Data URL | Pass through to the consuming tool |
| Unmatched bare relative file path, directory traversal, or resource from an inactive Skill | Reject; identify the source or read the corresponding `SKILL.md` first |

`workspace:` is for local file parameters and `exec`; it does not make `openviking_multi_read` read Workspace files. Save outputs in the ordinary Workspace too, for example by using `workspace:output/summary.md` in command arguments, so they survive cleanup of the temporary Skill package.

The runtime preserves the package directory structure but does not change the tool's working directory to the package root. Scripts should locate adjacent templates and modules relative to their own files. The model should use returned resource bindings directly, without copying resources manually or setting `working_dir` to a remote URI, cache path, or materialization directory. Execution failure does not automatically download more files and rerun the command.

Tool developers declare local input parameters through `Tool.resource_inputs`, for example `{"path": "local_file", "/files/*/workspace_path": "local_file"}`. Supported kinds are `local_file` and `local_directory`; JSON Pointer style paths support `*`. Undeclared parameters are not processed just because their names resemble paths. Do not declare output parameters as inputs. This declaration belongs to the tool implementation, not Skill metadata.

## Lifecycle, Cache, and Configuration

Each user message discovers and activates remote Skills again. Later turns in the same Session do not inherit candidates, permission intersections, or requirement check results. Tool usage records include real `skill_uri` / `skill_uris` and resolved `resolved_args`. The current implementation uses ordinary tool-result recording for Sessions, events, and traces, so Skill instructions may enter those records too. The RFC's separate redacted views and exclusion of persisted Skill instructions are not guarantees of the current implementation.

File storage has two layers:

| Layer | Current path | Lifetime |
|-------|--------------|----------|
| Bot host cache | `<bot-data>/remote_skill_cache/` | Isolated by permission scope, canonical root URI, and server revision; evicted by TTL/LRU |
| Tool execution copy | `<sandbox>/.remote-skill/<request-id>/<root-uri-sha256>/<skill-name>/` | Reused within the current turn and cleaned at completion; tools do not execute host cache files directly |

`.remote-skill/` is the current implementation path. Cache hits still require reading and activating with the current identity and rechecking the manifest. Files are validated by size and SHA-256 before use in the request sandbox. Revision changes or failed file validation prevent further use of that snapshot. Caching avoids repeated downloads without bypassing OpenViking ACL; `metadata.version` is not the cache revision.

These fields belong under **`bot.remote_skills`** in `ov.conf`. They are deployment configuration, not frontmatter:

| Field | Default | Purpose |
|-------|---------|---------|
| `discovery_limit` | `8` | Maximum remote candidates injected; range 1–50 |
| `score_threshold` | `0.35` | Minimum retrieval score; range 0–1 |
| `discovery_timeout_seconds` | `2.0` | Discovery timeout in seconds; greater than 0 and at most 30 |
| `max_files` | `128` | Maximum materialized files per package, including `SKILL.md` |
| `max_file_bytes` | `8388608` (8 MiB) | Maximum individual file size; also applies to `SKILL.md` at activation |
| `max_total_bytes` | `33554432` (32 MiB) | Maximum materialized package size |
| `cache_idle_ttl_seconds` | `600` | Host cache idle TTL, refreshed on hits |
| `cache_max_entries` | `32` | Maximum cached snapshots |
| `cache_max_bytes` | `268435456` (256 MiB) | Maximum host cache size |

File counts, sizes, and cache capacities must be positive. Later cache activity cleans expired entries; capacity pressure evicts unused entries by LRU. Server integrity API limits also apply, so increasing Bot limits cannot override server limits.

## Subagents and Troubleshooting

Subagents use local Workspace Skill summaries and Always content. They currently do not register OpenViking tools, so they do not discover, activate, or inherit the main Agent's remote Skill runtime or snapshots.

| Symptom or error | What to check |
|------------------|---------------|
| Local Skill missing from summaries | Active Workspace, `SKILL.md` in an immediate Skill subdirectory, and commands/environment in the Bot process |
| No remote candidates | Connection, channel switch, disabled tools, score/timeout, and local name collisions; discovery failure leaves local context available |
| `SKILL_NOT_ACTIVE` | Read that package's `SKILL.md` with `openviking_multi_read` first |
| `SKILL_TOOL_NOT_ALLOWED` | All active Skills' intersected permissions and Bash command constraints |
| `SKILL_CAPABILITY_UNAVAILABLE` | Provide required commands/environment in the execution sandbox and ensure the sandbox is available |
| `SKILL_RESOURCE_NOT_FOUND` / `SKILL_RESOURCE_AMBIGUOUS` | Use returned canonical resource URIs; use `workspace:` for ordinary Workspace inputs |
| `SKILL_INTEGRITY_UNAVAILABLE` / `SKILL_REVISION_CHANGED` | Ensure server support for integrity manifests; read again in a new turn after Skill updates finish |
| `SKILL_PACKAGE_TOO_LARGE` | Reduce package resources or review Bot and server limits |

## Implementation Locations

| Area | Path (relative to `bot/` unless stated otherwise) |
|------|-------------------------------------------------|
| Local loading, metadata, requirement filtering | `vikingbot/agent/skills.py` |
| Main Agent / subagent Skill context | `vikingbot/agent/context.py`, `vikingbot/agent/subagent.py` |
| Request runtime, activation, policies, resources | `vikingbot/agent/remote_skills.py` |
| Cache and Workspace initialization | `vikingbot/agent/remote_skill_cache.py`, `vikingbot/sandbox/manager.py` |
| Tool batches, schemas, and calls | `vikingbot/agent/loop.py`, `vikingbot/agent/tools/registry.py` |
| Remote reads and Experience Hook | `vikingbot/agent/tools/ov_file.py`, `vikingbot/hooks/builtins/openviking_hooks.py` |
| Configuration defaults | `vikingbot/config/schema.py` |
| OpenViking frontmatter parsing | `openviking/core/skill_loader.py` at the repository root |

## Related Documentation

- [Agent Capabilities](./02-agent-capabilities.md)
- [VikingBot and OpenViking Integration](./04-openviking-integration.md)
- [OpenViking Skills API](../../../../docs/en/api/04-skills.md)
