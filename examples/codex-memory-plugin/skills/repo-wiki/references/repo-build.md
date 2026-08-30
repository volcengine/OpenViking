# Repo Memory Build

## Core Principle

The agent authors the repo memory. Scripts collect and validate mechanical evidence, but do not replace agent-authored memory.

Do not let a script write `PROFILE.md`, `resources/commits.md`, `resources/prs.md`, or `resources/issues.md`. The agent must inspect the local project, understand the code, and author the human-facing memory. Scripts may create deterministic raw evidence only.

This is the full-build operation, not the daily reader or incremental updater. Return to `SKILL.md` and use `repo-read.md` for task-time search over existing memory. For latest-change-only updates to an existing `.repo_memory`, use `repo-update.md`. Use this reference for first-time creation, full rebuilds, or full refreshes that recollect raw evidence and rewrite authored memory.

## Output Layout

The user selects a repository, not a memory directory. Do not ask for a `.repo_memory` path. Create this bundle inside the target repository:

```text
<repo>/.repo_memory/
├── PROFILE.md
├── <repo-native-topic>.md conceptual pages
├── raw/
│   ├── prepare-report.json
│   ├── git-commits.json
│   └── github-facets.json or gitlab-facets.json
└── resources/
    ├── commits.md
    ├── prs.md
    └── issues.md
```

Script-generated artifacts: `raw/prepare-report.json`, `raw/git-commits.json`, and optional `raw/github-facets.json` or `raw/gitlab-facets.json`. Agent-authored durable artifacts: `PROFILE.md`, repository-native supporting conceptual pages when the repository has enough surface area, `resources/commits.md`, `resources/prs.md`, and `resources/issues.md`. Temporary planning artifact `.repo_memory/_plan.md` is allowed during drafting but must be removed before final validation.

## Wiki-Style Output Contract

Repository Wiki memory is a wiki-style repository memory backed by raw mechanical evidence. The primary user-facing product is a readable wiki, not a dense schema dump or a history table.

- `PROFILE.md` is the only fixed conceptual wiki file and the wiki landing page: project identity, what the repo contains, how it works, first-use path, Major Areas, Supporting Pages, provider context, and evidence inspected.
- Do not assume fixed supporting page names. Decide supporting pages and filenames from the repository itself after discovery.
- For very small repositories, `PROFILE.md` plus 1-2 supporting pages is enough. For most repositories, generate 3-7 Markdown pages total, including `PROFILE.md`. Only exceed 7 pages when the repository clearly has multiple substantial, independent areas that would become confusing if merged.
- Every durable supporting conceptual page must include frontmatter with `schema: "repo_memory_wiki_page.v0.1"` so validation can scan it.
- Supporting pages explain concepts, workflows, system areas, change surfaces, setup, verification, and agent routing cautions. They must link claims to inspected docs/source and use conservative wording when lightweight mode did not inspect source.
- resources/*.md remain compact historical routing cards for commits, PRs/MRs, and issues. They should not be the main architecture explanation.
- Keep raw JSON as debug evidence only. Do not ask future agents to read raw facets unless compact pages and resources are insufficient.

## Conceptual Page Planning

After discovery and before final wiki writing, create a temporary planning artifact at `.repo_memory/_plan.md`. Keep it compact. Use it to outline intended final pages, each page purpose, source evidence, page boundaries, canonical homes for overlapping concepts, remaining questions, weak evidence, and paths that must be verified before final writing.

- Remove `.repo_memory/_plan.md` before final validation; it is not part of the durable repo memory bundle.
- Organize pages like human documentation, not a raw file inventory. Use human documentation titles. Do not title pages or headings after source paths, and do not mirror the source tree.
- Create a page only when a future human or agent needs a canonical explanation for a concept, workflow, system area, or change surface. A page is a durable orientation artifact, not a parking place for exploration facts.
- Merge rather than split when two pages would repeat the same workflow, types, or source paths; when one page would only explain a subset of another page; or when the distinction is based on source directory layout rather than reader task or concept.
- Split rather than merge when one page would mix unrelated reader tasks; when a concept has its own workflow, data model, risks, and source-backed change guidance; or when a page would become too long to navigate.
- Prefer headings inside broader pages before creating many small pages. Avoid thin pages and stub-like pages.

Natural documentation domains are candidates, not a required checklist: architecture, workflows, data model, integrations, operations, testing/evaluation, and extension points. In `_plan.md`, decide which candidates were selected, merged, or skipped and why. Generic names such as `architecture.md`, `runtime-flow.md`, and `developer-workflow.md` are fallback names only when repository vocabulary gives no stronger topic.

## Repository Prerequisite

The selected target must be a local git repository: either the directory contains `.git/` or is inside a git worktree. A GitHub/GitLab remote is optional; local-only git repositories are supported.

If the directory is not a git repository, do not create `.repo_memory/` and do not fabricate a memory bundle from file inspection alone. Stop with a prominent notice:

```text
**Repo Memory Cannot Be Built Yet**

> This folder is not a git repository, so repo memory cannot collect local commit history or provider-linked PR/MR/issue evidence.

**Next steps**
- If this is an existing project, open the real cloned repo directory.
- If this is a new project, run `git init` first, make at least one commit, then rerun the `repo-build` operation.
- If you only want file inspection, continue by inspecting files without repo memory.
```

## Path Convention

`<skill-dir>` means the parent directory of the `references/` directory containing this file. Resolve it before running scripts, for example:

```bash
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --pretty --progress
```

## Default Settings

Default mechanical collection settings are intentionally visible in `<skill-dir>/defaults.json`:

```json
{
  "schema": "repo_memory_builder_defaults.v2",
  "repoHistory": {
    "mode": "local-only",
    "limits": {
      "commits": 30,
      "prs": 30,
      "issues": 30
    }
  },
  "summaryChars": 4000
}
```

To change defaults for future builder runs, edit `defaults.json`, not the Python scripts. To override one run, pass `--history-mode`, `--commit-limit`, `--pr-limit`, `--issue-limit`, or `--summary-chars` to `collect_all.py`.

Provider collection is disabled by default. Use `--history-mode provider` to
request best-effort provider evidence.

## Historical Evidence Policy

Historical evidence collection is configurable per run with `--history-mode` and by default with `repoHistory.mode` in `defaults.json`:

| Mode | Commits | PRs/MRs and issues | Resource file contract |
|------|---------|--------------------|------------------------|
| `--history-mode none` | Disabled | Disabled | Write disabled resource files for commits, PRs/MRs, and issues using `source: "history_disabled"`, `resource_count: 0`, and `raw_source: ""`. |
| `--history-mode commits-only` | Collected locally | Disabled | Write commit resources from `raw/git-commits.json`; write PR/issue files using `source: "provider_skipped_local_only"`, `resource_count: 0`, and `raw_source: ""`. |
| `--history-mode local-only` | Collected locally | Disabled | Same as commits-only; use when the user wants local Git history but no provider calls. |
| `--history-mode provider` | Collected locally | Best-effort | Collect provider facets when ready; if provider evidence is unavailable or fails, continue local-only and mark PR/issue resources unavailable. |
| `--history-mode provider-required` | Collected locally | Required | Fail the collection if provider facets cannot be collected. Use only when the user explicitly requires PR/MR/issue evidence. |

`--skip-provider` is a compatibility alias for `--history-mode local-only`; `--require-provider` is a compatibility alias for `--history-mode provider-required`.

When history or provider evidence is disabled, skipped, unavailable, or degraded, still create `resources/commits.md`, `resources/prs.md`, and `resources/issues.md`; write disabled resource files for any historical channel that was not collected. The disabled or unavailable resource files tell future agents that the builder intentionally did not collect that evidence. For PR/issue files, do not say that no PRs or issues exist unless provider evidence was actually collected and empty.

## User Count Requests

If the user mentions how many commits, PRs/MRs, or issues to collect, treat that as a one-run override and pass explicit limit flags to `collect_all.py`. Do not edit `defaults.json` for a one-run request.

Examples:

- "拉 50 条 commit" means add `--commit-limit 50`.
- "PR 拉 20 条，issue 拉 30 条" means add `--pr-limit 20 --issue-limit 30`.
- "commit、PR、issue 都拉 50 条" means add `--commit-limit 50 --pr-limit 50 --issue-limit 50`.

Only edit `defaults.json` when the user asks to change defaults for future build runs, for example "以后默认都拉 50 条" or "把 repo memory 的默认 limit 改成 50".

## Progress Display

Skill instructions cannot render app-native progress components by themselves. For interactive builder runs, prefer `collect_all.py --progress`; it renders a terminal progress bar on stderr while keeping stdout as the final JSON report. Omit `--progress` only for strict machine-only runs that must avoid stderr progress output.

The progress bar covers the mechanical collection steps: preparation, local commits, and provider facets. Provider warnings are not terminal notifications; they are returned in the JSON report as structured `notices[]` so the agent can show them as a normal user-visible assistant message. The progress bar does not replace the required provider warning notice, final JSON report review, agent-authored Markdown work, or final validation.

## Build Modes

Build mode controls only the local project understanding phase. The mechanical collection, authoring, and validation flow stays the same.

- Default to lightweight mode when the user does not specify.
- Use lightweight mode for a quick or docs-only pass. Read root README/high-level docs, repo-local agent instructions such as `AGENTS.md` or `CLAUDE.md`, and clearly linked overview/setup docs. Do not inspect source, manifests, scripts, or CI just to deepen the profile. Mark code-level architecture claims as doc-derived or unverified.
- Use deep mode only when the user explicitly asks for deep/thorough/full work, or when docs are too thin to produce useful memory. Read docs and agent instructions, then inspect repository structure, module READMEs, representative source and entrypoints, manifests, scripts, and CI before writing `PROFILE.md`.
- Do not stop to ask for a mode unless the user's request is contradictory.

## Tool Roles

### `scripts/collect_all.py`

Use this as the primary mechanical path. It prepares the workspace, collects local git commit evidence, collects GitHub/GitLab PR/MR/issue evidence when provider auth is ready, and returns a JSON report describing completed steps and outputs. If provider collection fails after preparation reports `provider_evidence_state: "ready"`, the default behavior is to keep local commit evidence, emit a warning notice, and report `steps.provider_facets.degraded_to_local_only: true`. Only `--require-provider` turns this into a hard failure.

Default run:

```bash
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --snapshot-ref HEAD --pretty --progress
```

One-run limit override:

```bash
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --snapshot-ref HEAD --commit-limit 50 --pr-limit 20 --issue-limit 20 --pretty --progress
```

Full refresh of an existing `.repo_memory` bundle:

```bash
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --reuse --snapshot-ref HEAD --pretty --progress
```

Local-only memory or hard-required provider evidence:

```bash
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --snapshot-ref HEAD --skip-provider --pretty --progress
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --snapshot-ref HEAD --require-provider --pretty --progress
```

Tell the user before starting long collection steps. After completion, report collected counts from the JSON report: local commits, PRs/MRs, issues, provider state, skipped steps, notices, and output files.

#### Final Summary Notices

If the final JSON report contains `notices[]`, the final user-facing summary must include those notices explicitly. Include the notice title, message, and next steps, especially `Provider Evidence Unavailable`.

Do not silently collapse notices into counts, provider state, or a generic "local-only" sentence. If provider evidence degraded to local-only, say that directly and include the reason from the notice.

#### Provider Sandbox and Transport Failures

`gh/glab` provider collection needs external network access. If provider stderr or `notices[]` shows `fetch failed`, timeout, DNS, connection, TLS, `ENOTFOUND`, `EAI_AGAIN`, or similar transport text, report provider evidence unavailable and continue local-only unless `--require-provider` was requested.

Verify provider authentication in the same normal shell with the command reported by `collect_all.py`; for GitHub Enterprise or self-hosted GitLab this may include `--hostname <host>`. Authenticate with `gh auth login` or `glab auth login` (also host-scoped when prompted), then rerun `collect_all.py`; do not paste tokens into the skill or call provider APIs directly.

Do not use a restricted shell sandbox to verify provider/API availability. Verify in a normal shell or approved network-enabled mode. If only restricted shell access is available, report provider evidence unavailable and continue local-only unless `--require-provider` was requested. Do not treat a provider transport failure as empty PR/issue evidence, no PR/issue changes, bad login, or bypass the scripts with direct APIs, browser scraping, copied credentials, or hand-written raw facets.

### `scripts/validate_memory.py`

Use this as the final gate after authoring `PROFILE.md`, supporting conceptual pages, and `resources/*.md`. It accepts either the repository root or the memory root, validates required files, JSON parseability, frontmatter counts, placeholder removal, and provider raw/resource consistency.

```bash
python3 <skill-dir>/scripts/validate_memory.py <repo-path> --pretty
```

### Internal scripts

These are invoked by `collect_all.py`. Use them directly only for debugging or narrow recovery:

- `prepare_repo_memory.py`: validates the target repo, handles `--reuse`, creates `.repo_memory/{raw,resources}`, updates `.gitignore`, and writes `raw/prepare-report.json`.
- `git_commit_facets.py`: collects local commit facets from the selected snapshot. It needs only local git history.
- `github_resource_facets.py` and `gitlab_resource_facets.py`: collect provider PR/MR/issue facets when `prepare-report.json` says the provider is ready. They share the same downstream resource shape; GitLab merge requests are normalized into PR resources.

Provider resource facets share this contract: `--snapshot-ref HEAD` filters merged PRs/MRs by merge-commit ancestry against the local snapshot; open and closed-unmerged PRs/MRs are not retained as landed evidence; issues come from the current bounded provider list because issues do not have reliable commit ancestry. For PRs/MRs, `--pr-limit` is the retained count after snapshot filtering.

## Agent Workflow

Follow this order manually:

1. **Collect mechanical evidence**
   - Run `collect_all.py`.
   - Use `--reuse` only for a full refresh/rebuild of an existing `.repo_memory`; for latest-change-only updates, prefer `repo-update.md`.
   - Read the returned JSON report and `.repo_memory/raw/prepare-report.json`.
   - Stop if preparation fails. Do not improvise around git/repo safety checks.

2. **Show provider notices**
   - If `notices[]` contains `Provider Evidence Unavailable`, show it as a normal assistant message before continuing and repeat it in the final summary.
   - Render `title` as a bold heading, `message` as body text, `command` as the login command when present, and `next_steps` as recovery steps. Do not show raw JSON or put the notice inside a fenced code block.
   - If `notices[]` is absent, fall back to `raw/prepare-report.json`: when `provider_notice_level` is `warning`, render `provider_notice_markdown` as a normal assistant message.
   - If the user explicitly required PR/MR/issue evidence, run with `--require-provider`, stop after provider failure, and ask them to install/login or fix repository access before rerunning.

Use this visible shape:

```text
**Provider Evidence Unavailable**

<notice.message>

**Next steps for provider evidence**
Run: `<notice.command>`

<notice.next_steps rerun item>
```

3. **Understand the local project**
   - Choose lightweight by default; choose deep only when requested or clearly needed.
   - Form project-level understanding before looking at PR/issue history.
   - Record whether conclusions are doc-derived or code-inspected.

4. **Plan and draft the wiki landing page and conceptual pages**
   - Read [repo-templates.md](repo-templates.md) before writing memory files.
   - Create `.repo_memory/_plan.md` after discovery and before final wiki writing. Use it to cluster repository-native conceptual areas, page purposes, evidence, boundaries, canonical homes, merge/split decisions, weak evidence, and path verification needs.
   - Write `PROFILE.md` as the wiki landing page with repo identity, checkout state, what the repo contains, how it works, first-use path, Major Areas, Supporting Pages, provider context, and evidence inspected.
   - Create repository-native supporting conceptual pages from the plan. Cover only concepts, workflows, system areas, or change surfaces with enough evidence to deserve canonical pages.
   - Remove `.repo_memory/_plan.md` before final validation.
   - Record the selected build mode and avoid overstating code-level claims in lightweight mode.

5. **Review raw evidence**
   - Confirm `raw/git-commits.json` exists.
   - When provider evidence is available, confirm the matching provider facets exist.
   - If provider evidence is missing, skipped, or degraded to local-only, keep PR/issue resources explicitly marked unavailable instead of inventing provider evidence.

6. **Author human-readable resources**
   - Use the resource templates in [repo-templates.md](repo-templates.md).
   - Read raw facets and write `resources/commits.md`, `resources/prs.md`, and `resources/issues.md` yourself.
   - Keep resources compact. Use one fixed-field section per commit, PR/MR, or issue, separated by `---`; do not use Markdown tables.
   - Every section must include a search-grade `Description`.

7. **Finalize and validate**
   - Add final pointers and provider snapshot notes to `PROFILE.md`.
   - Set `PROFILE.md.source_tree` to the selected snapshot's full Git tree SHA.
   - Run `validate_memory.py` as the final gate.
   - Fix authored Markdown placeholders, frontmatter counts, or resource mismatches before reporting success.

## Refresh/Update Workflow

For incremental updates to recent commits, PRs/MRs, or issues in an existing `.repo_memory`, use `repo-update.md`.

Use builder refresh only when the user wants a full rebuild/full refresh while preserving the existing memory directory:

```bash
python3 <skill-dir>/scripts/collect_all.py --repo-path <repo-path> --reuse --pretty --progress
```

Then rewrite affected human-authored resources from the new raw evidence. Preserve useful existing notes only when they are still supported by current raw evidence and live repository inspection. Never append or patch raw/provider JSON by hand; rerun the collection scripts instead.

## Description Quality Standard

`Description` is the main agentic-search surface for `resources/commits.md`, `resources/prs.md`, and `resources/issues.md`. Keep only the core rule here; the full quality standard and section scaffolding live in [repo-templates.md](repo-templates.md).

Every description must say what the item explains, when a future agent should open it, which modules/files/commands/config it points to, concrete search cues, and evidence strength. Do not copy raw summaries, headings, or tables into descriptions, and avoid vague descriptions like "fix bug" or "update docs".

## Generated Knowledge Contract

Generate memory that the `repo-wiki` repo-read operation can consume progressively:

- `PROFILE.md` is the stable wiki landing page and should summarize identity, evidence, major areas, supporting pages, verification gates, and resource pointers.
- Supporting conceptual pages are repository-native canonical homes for concepts, workflows, system areas, and change surfaces selected through temporary planning.
- `resources/*.md` are compact historical routing cards with search-grade descriptions.
- `raw/*.json` contains optional build/debug evidence for deep inspection when compact resources are insufficient.

Do not duplicate the daily retrieval workflow here. The `repo-read` operation owns trigger timing, silent background-maintenance handoff, and task-specific search behavior.

## Trust Rules

- Local code and targeted verification are stronger evidence than generated memory.
- Local git commit evidence is checkout history and does not require provider login.
- GitHub/GitLab PR/issue evidence is historical/contextual, not proof of current checkout behavior.
- Open PRs are active branch intent, not landed behavior.
- Closed unmerged PRs are weak or superseded evidence.
- Merged PRs are useful history but still require current code verification.

## Authoring Templates

When writing `PROFILE.md`, `resources/commits.md`, `resources/prs.md`, or `resources/issues.md`, read [repo-templates.md](repo-templates.md) and use it as fill-in scaffolding. Remove placeholders from final files and delete sections that are genuinely unavailable.

Apply these rules even before loading the templates:

- Use fixed-field resource sections separated by `---`; do not use Markdown tables for PRs or issues.
- Write search-grade `Description` fields that explain what the PR/issue covers, when future agents should open it, affected modules/files/behaviors, concrete search cues, and evidence strength.
- Do not copy raw summaries or headings into descriptions; synthesize routing cues from raw facets and current project understanding.
- Do not add persistent `indexes/*.json` files or `index:` frontmatter; search `resources/*.md` directly and open raw JSON only for build/debug evidence.
