# Repo Memory Authoring Templates

Use these templates as fill-in scaffolds. Replace all bracketed placeholders with conclusions from local code inspection and raw facets. Remove sections that are genuinely unavailable; do not leave placeholders in final files.

### `.repo_memory/PROFILE.md`

`PROFILE.md` is the only fixed conceptual wiki file. It is a wiki landing page, not a dense resource index. It should read like a wiki-style repository memory home page: concise project identity, first-use path, major areas, supporting pages, and historical/provider pointers. Keep repository-Wiki frontmatter so the validator and maintenance hooks can still trust the bundle.

```markdown
---
schema: "repo_memory_profile.v0.2"
layout: "wiki_landing_page.v0.1"
disclosure_model: "progressive_wiki"
repo_name: "[repo name]"
repo_owner: "[owner or empty]"
repo_full_name: "[owner/name or empty]"
repo_url: "[remote URL or empty]"
code_host_provider: "github|gitlab|none"
source_repo_path: "."
generated_at: "[ISO timestamp]"
build_mode: "lightweight|deep"
local_head: "[git HEAD]"
source_tree: "[40-character Git tree SHA]"
local_branch: "[branch or detached HEAD]"
working_tree_state: "clean|dirty|unknown"
trust_state: "draft"
code_host_resource_state: "available|unavailable|partial"
resources:
  commits: "resources/commits.md"
  prs: "resources/prs.md"
  issues: "resources/issues.md"
raw:
  prepare_report: "raw/prepare-report.json"
  commit_facets: "raw/git-commits.json"
  provider_facets: "raw/github-facets.json|raw/gitlab-facets.json|"
---

# [Repo Name] Repository Wiki

[One short paragraph explaining what this repository is, who uses it, and the primary runtime/package/product surface. Ground this in inspected docs or source.]

- Repository: `[owner/name or local repo]`
- Local HEAD when prepared: `[sha]` on `[branch]`
- Build mode: `[lightweight|deep]`; working tree was `[clean/dirty + explanation]`
- License: `[license if known]`

## What This Repo Contains

| Area | What it does |
|------|--------------|
| `[repo-native product/module/workflow name]` (`path/`) | [Plain-language responsibility and primary entrypoint.] |
| `[repo-native product/module/workflow name]` (`path/`) | [Plain-language responsibility and primary entrypoint.] |

## How It Works

[Five to ten sentences describing the main runtime/build/request/data flow. Link to supporting pages instead of explaining every detail here. Use conservative wording when lightweight mode did not inspect representative source.]

## First-Use Path

```bash
[install or setup command]
[one fast verification or smoke command]
```

[Explain the shortest safe path a future agent should use to orient, build, test, or run the project.]

## Major Areas

Choose these rows from `.repo_memory/_plan.md` after clustering inspected docs/source paths. Do not assume fixed page names; generic names are fallback names only when repository vocabulary gives no stronger topic.

| Area | Page | What It Covers |
|------|------|----------------|
| `[repo-native area name]` | [`[human page title]`](./repo-native-topic.md) | [What task cluster this page routes and why it is a canonical home.] |
| `[repo-native area name]` | [`[human page title]`](./repo-native-topic.md) | [What task cluster this page routes and why it is a canonical home.] |

## Supporting Pages

- [`[human page title]`](./repo-native-topic.md) - open when [task cue, change surface, workflow, or risk].
- [`[human page title]`](./another-repo-native-topic.md) - open when [task cue, change surface, workflow, or risk].

## Agent Consumption Rules

1. Read this file first, then open only the supporting page relevant to the task. Memory is a map, not proof.
2. Verify current behavior against live source, configs, tests, or executable checks before editing or making strong claims.
3. Treat commits, PRs/MRs, and issues as historical routing context; they do not prove current checkout behavior.
4. Prefer source paths and commands linked from supporting pages over broad repository scanning.

## Provider Context

- [Historical commits](./resources/commits.md) - local checkout history and regression routing.
- [Historical PRs/MRs](./resources/prs.md) - implementation context from provider facets when available.
- [Historical issues](./resources/issues.md) - user/request/problem context when available.
- Raw local commit facets: `./raw/git-commits.json`.
- Raw code-host facets: `./raw/github-facets.json` or `./raw/gitlab-facets.json` when present.

## Evidence Inspected

- Root docs and agent instructions: `[README/docs/AGENTS.md/CLAUDE.md or similar files inspected]`.
- Module docs: `[module README/docs inspected]`.
- Representative source: `[important source/entrypoint files inspected, or 'not inspected in lightweight mode']`.
- Manifests/scripts/CI: `[manifests, scripts, workflows inspected, or 'not inspected in lightweight mode']`.
- Local commit snapshot: `[raw/git-commits.json status and count]`.
- Historical code-host snapshot: `[raw/github-facets.json or raw/gitlab-facets.json status and counts]`.
```

### `.repo_memory/_plan.md`

Create this temporary planning artifact after discovery and before final wiki writing. Use it to cluster repository concepts and choose natural page boundaries. Remove `_plan.md` before final validation; it is not part of the durable bundle.

```markdown
# Repo Memory Wiki Plan

## Candidate Domains Considered

- [selected, merged, or skipped candidate domain] - [source evidence and decision].

## Intended Final Pages

| Page | Purpose | Source evidence | Boundary |
|------|---------|-----------------|----------|
| `repo-native-topic.md` | [Future-agent task this page routes.] | [Docs/source/tests/scripts inspected.] | [What belongs here and what belongs elsewhere.] |

## Canonical Homes And Overlaps

- [Overlapping concept] belongs in `[page]`; link from `[other page]` instead of duplicating because [reason].

## Weak Evidence And Verification Needs

- [Claim/path/command] needs [specific verification] before final writing, or should be omitted.
```

### `.repo_memory/<repo-native-topic>.md`

Create supporting conceptual pages after clustering inspected evidence. Name pages from repository vocabulary, not from this template. Use lowercase kebab-case filenames derived from product surfaces, runtime components, commands, protocols, adapters, workflows, data models, operations, or other durable repository concepts. Generic names are fallback names only when repository vocabulary gives no stronger topic. Every conceptual page must include frontmatter so validation can scan it.

```markdown
---
schema: "repo_memory_wiki_page.v0.1"
page_type: "architecture|workflow|data-model|integration|operation|testing|extension|domain"
generated_at: "[ISO timestamp]"
source_profile: "PROFILE.md"
trust_state: "draft_wiki_page"
---

# [Human Documentation Title]

## Purpose

[Explain which future tasks should open this page and what decisions it helps route.]

## Key Paths

| Path | Responsibility | When to inspect |
|------|----------------|-----------------|
| `path/to/file-or-dir` | [What it owns.] | [Task cue, symbol, command, or failure mode.] |

## Flow Or Boundaries

[Explain the concept, lifecycle, dependency direction, or ownership boundary. Prefer short paragraphs and bullets over exhaustive API reference.]

## Verification

- [Fast command or source check relevant to this page.]
- [Expensive or environment-dependent check, or say none identified.]

## Agent Notes

- Treat this page as routing context and verify current behavior in live source before editing.
- [One repo-specific caution, extension rule, or likely pitfall.]
```

### `.repo_memory/resources/commits.md`

Use fixed-field sections, not Markdown tables. Every commit needs a search-grade `Description` following the standard in `SKILL.md`. Do not paste the full raw summary; full raw summaries stay in `../raw/git-commits.json`.

```markdown
---
schema: "repo_memory_commit_resource.v0.1"
repo_full_name: "[owner/name or local repo name]"
generated_at: "[ISO timestamp]"
source: "git_commit_facets"
resource_count: [count]
trust_state: "draft_resource"
raw_source: "../raw/git-commits.json"
---

# Commit Resource Snapshot

Source: `.repo_memory/raw/git-commits.json`. Treat commits as local checkout history only: they are useful routing evidence, but current-code verification is still required.

## Commit [short_sha]: [title]

- SHA: `[full sha]`
- Evidence status: [One short sentence describing local-history evidence strength and whether current source was verified.]
- Author: `[author name]`
- Authored: `[ISO timestamp]`
- Modules: `[semantic modules for human routing]`
- Path modules: `[path prefixes from raw facets or current inspection]`
- Description: [2-4 search-grade sentences: what this commit changes, when future agents should open it, affected modules/files/runtime behavior, search cues, and evidence strength]
- Key files: `[path/a.py]`, `[path/b.md]`
- Diff: `[changed_files] files, +[additions]/-[deletions]`
- Parent count: `[count]`
- Agent note: [how future agents should treat this commit]
- Raw lookup: `facetId=commit.abc1234`

---

## Commit [short_sha]: [title]

- SHA: `...`
- Evidence status: ...
- Author: `...`
- Authored: `...`
- Modules: `...`
- Path modules: `...`
- Description: ...
- Key files: ...
- Diff: ...
- Parent count: ...
- Agent note: ...
- Raw lookup: `facetId=commit.abc1234`
```

When commit history is disabled by policy, still write a disabled resource file:

```markdown
---
schema: "repo_memory_commit_resource.v0.1"
repo_full_name: "[owner/name or local repo name]"
generated_at: "[ISO timestamp]"
source: "history_disabled"
resource_count: 0
trust_state: "disabled_by_policy"
raw_source: ""
---

# Commit Resource Snapshot

No commit evidence was collected because historical evidence was disabled for this build. This is a collection-policy statement, not proof that the repository has no commits.
```

### `.repo_memory/resources/prs.md`

Use fixed-field sections, not Markdown tables. Every GitHub PR or GitLab MR needs a search-grade `Description` following the standard in `SKILL.md`. Do not paste the full raw summary; full raw summaries stay in `../raw/github-facets.json` or `../raw/gitlab-facets.json`.

```markdown
---
schema: "repo_memory_pr_resource.v0.1"
repo_full_name: "[owner/name]"
generated_at: "[ISO timestamp]"
source: "github_resource_facets|gitlab_resource_facets"
resource_count: [count]
trust_state: "draft_resource"
raw_source: "../raw/github-facets.json|../raw/gitlab-facets.json"
---

# Pull Request Resource Snapshot

Source: `.repo_memory/raw/github-facets.json` or `.repo_memory/raw/gitlab-facets.json`. Treat PRs/MRs as historical context only: merged PRs/MRs still require current-code verification, open PRs/MRs are branch intent, and closed-unmerged PRs/MRs are weak evidence.

## PR/MR #[number]: [title]

- State: `MERGED|OPEN|CLOSED` [include draft if applicable]
- Evidence status: [One short sentence describing historical/current relevance and whether current source was verified.]
- Branch: `base <- head`
- Modules: `[semantic modules for human routing]`
- Path modules: `[path prefixes from raw facets or current inspection]`
- Description: [2-4 search-grade sentences: what this PR explains, when future agents should open it, affected modules/files/runtime behavior, search cues, and evidence strength]
- Key files: `[path/a.py]`, `[path/b.md]`
- Diff: `[changed_files] files, +[additions]/-[deletions]`
- Linked issues: `#[issue]` or `-`
- Commit signal: [1-3 commit headlines or `-`]
- Agent note: [how future agents should treat this PR]
- URL: [PR URL]
- Raw lookup: use `facetId=pr.123` for GitHub PRs or `facetId=mr.123` for GitLab MRs.

---

## PR/MR #[number]: [title]

- State: `...`
- Evidence status: ...
- Branch: `...`
- Modules: `...`
- Path modules: `...`
- Description: ...
- Key files: ...
- Diff: ...
- Linked issues: ...
- Commit signal: ...
- Agent note: ...
- URL: ...
- Raw lookup: use `facetId=pr.123` for GitHub PRs or `facetId=mr.123` for GitLab MRs.
```

### `.repo_memory/resources/issues.md`

Use fixed-field sections, not Markdown tables. Every issue needs a search-grade `Description` following the standard in `SKILL.md`. Keep evidence compact; full raw summaries stay in `../raw/github-facets.json` or `../raw/gitlab-facets.json`.

```markdown
---
schema: "repo_memory_issue_resource.v0.1"
repo_full_name: "[owner/name]"
generated_at: "[ISO timestamp]"
source: "github_resource_facets|gitlab_resource_facets"
resource_count: [count]
trust_state: "draft_resource"
raw_source: "../raw/github-facets.json|../raw/gitlab-facets.json"
---

# Issue Resource Snapshot

Source: `.repo_memory/raw/github-facets.json` or `.repo_memory/raw/gitlab-facets.json`. Treat issues as requirement, bug, support, or planning context; verify against current code before acting.

## Issue #[number]: [title]

- State: `OPEN|CLOSED`
- Evidence status: [One short sentence describing issue evidence strength, relevance, and whether current source was verified.]
- Modules: `[semantic modules for human routing]`
- Path modules: `[path prefixes from linked PR facets or current inspection, or -]`
- Description: [2-4 search-grade sentences: what user problem/request this issue explains, when future agents should open it, affected behavior/modules, symptoms/errors, search cues, and evidence strength]
- Evidence: [short evidence: label, error, user pain point, requirement, or theme]
- Linked PRs: `#[pr]` or `-`
- Linked branches: `base <- head` or `-`
- Agent note: [how future agents should use this issue]
- URL: [Issue URL]
- Raw lookup: `facetId=issue.123`

---

## Issue #[number]: [title]

- State: `...`
- Modules: `...`
- Path modules: `...`
- Description: ...
- Evidence: ...
- Linked PRs: ...
- Linked branches: ...
- Agent note: ...
- URL: ...
- Raw lookup: `facetId=issue.123`
```

### Disabled Or Unavailable Resource Files

Use this pattern when historical evidence or provider evidence was intentionally not collected, unavailable, or degraded to local-only. Keep `resource_count: 0` and `raw_source: ""`; do not fabricate PRs, MRs, issues, or raw provider evidence. Use `source: "history_disabled"` when the entire history channel is disabled, `source: "provider_skipped_local_only"` when provider collection was skipped for a local-only build, and `source: "provider_unavailable"` when provider collection could not be completed.

For PR/MR resources:

```markdown
---
schema: "repo_memory_pr_resource.v0.1"
repo_full_name: "[owner/name or local repo name]"
generated_at: "[ISO timestamp]"
source: "provider_skipped_local_only|provider_unavailable|history_disabled"
resource_count: 0
trust_state: "unavailable_local_only|disabled_by_policy"
raw_source: ""
---

# Pull Request Resource Snapshot

No provider evidence was collected for PRs/MRs in this build. This means PR/MR history is unavailable in the memory bundle; it does not mean the repository has no PRs or MRs.
```

For issue resources:

```markdown
---
schema: "repo_memory_issue_resource.v0.1"
repo_full_name: "[owner/name or local repo name]"
generated_at: "[ISO timestamp]"
source: "provider_skipped_local_only|provider_unavailable|history_disabled"
resource_count: 0
trust_state: "unavailable_local_only|disabled_by_policy"
raw_source: ""
---

# Issue Resource Snapshot

No provider evidence was collected for issues in this build. This means issue history is unavailable in the memory bundle; it does not mean the repository has no issues.
```
