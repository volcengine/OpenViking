# Repo Memory Read

## Role And Demand Gate

Use existing `.repo_memory/` as wiki-style repo memory for repository identity, architecture, history, PR/issue context, remembered fixes, and cross-module search. Live code and tests remain authoritative.

Select this reference for broad repo introduction, history, architecture background, cross-module routing, PR/issue context, design rationale, or stale-memory awareness. Skip this reference for narrow tasks with a clear live-code target.

Explicit build, rebuild, or update requests route through `SKILL.md` to
`repo-build.md` or `repo-update.md`.

## Repository

Resolve the repository in this order:

1. Use the user's explicit local path.
2. Otherwise use the current workspace Git root.
3. Infer a named local repo only when the match is unambiguous.
4. Ask for the path only when multiple repos remain plausible.

Derive memory as `<repo>/.repo_memory`; never ask for a memory-directory path. If the target is not inside a Git worktree, continue from live files.

## Retrieval

If `.repo_memory/PROFILE.md` is a readable regular file, read `PROFILE.md` as the wiki landing page once. Treat its descriptions as routing cues, not proof.

Extract task-relevant links from `Major Areas` and `Supporting Pages`. Do not assume fixed page names; use `PROFILE.md` links and headings to find the repository-native canonical homes for the user's task. Open at most 2-4 relevant conceptual pages from `.repo_memory/*.md` before searching historical resources.

Search `PROFILE.md`, `.repo_memory/*.md`, and `.repo_memory/resources/` with a combined query built from the user's strongest handles: path, basename, symbol, command, PR/issue number, error text, module, branch, environment variable, or config key.

```bash
rg -n '<handle-1>|<handle-2>' \
  .repo_memory/PROFILE.md .repo_memory/*.md .repo_memory/resources
```

Use:

- conceptual `.repo_memory/*.md` pages for repository-native canonical homes, workflows, system areas, change surfaces, and verification routing;
- resources/*.md for historical routing cards;
- `resources/commits.md` for local history and regressions;
- `resources/prs.md` for merged or active implementation context;
- `resources/issues.md` for symptoms, requests, and requirements.

Disabled and unavailable historical resource files are collection state, not repository state. If a resource frontmatter uses `source: "history_disabled"`, `source: "provider_skipped_local_only"`, or `source: "provider_unavailable"` with `resource_count: 0`, do not conclude that there are no commits, PRs, MRs, or issues. Treat the channel as intentionally uncollected or unavailable; answer from available memory only. Ask whether to rebuild with provider history when that context matters.

If a read is missing, unreadable, or structurally mixed, discard conclusions that depend only on the bundle. Do not repair it in the foreground.

## Retrieval Budget

- Read `PROFILE.md` at most once.
- Run at most 2 combined `rg` commands.
- Stop repo-memory retrieval as soon as the hits are sufficient.
- Open at most 2-4 relevant conceptual pages total during the bounded memory phase.
- Open only the matched resource section when `rg` context is insufficient.

Do NOT open these unless the user explicitly asks or a compact hit contains only a `facetId`:

- `.repo_memory/raw/*.json`;
- `docs/`, `packages/`, `tests/`, or other live source directories.

The live directories restriction applies only during the bounded memory phase. Current implementation claims and code edits still require live-code verification after the maintenance handoff.

## Answer And Trust Rules

Answer history, architecture, and context questions from sufficient memory hits. If two bounded searches miss, say what was not found and ask whether to inspect more deeply.

Continue into live evidence when the user asks about current behavior, requests an edit, or the bundle was unavailable. Keep these distinctions explicit:

- live code and targeted verification are stronger than generated memory;
- tests are stronger than PR or issue summaries;
- commits and merged PRs are historical evidence, not current-behavior proof;
- open PRs describe intent;
- issues describe problem context.

Never create, update, delete, or repair repo-memory files in the foreground `repo-read` task.
