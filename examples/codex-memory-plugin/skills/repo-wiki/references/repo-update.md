# Repo Memory Update

## Core Principle

Update existing repo memory from a delta, not a full rebuild. Treat the current `.repo_memory/` bundle as the baseline, follow the effective history policy, detect only the enabled local commit and provider PR/MR/issue changes, then edit only the affected resources.

Do not rebuild the whole bundle by default. Return to `SKILL.md` and use `repo-build.md` only when `.repo_memory/PROFILE.md` is missing, the existing memory is structurally unusable, or the user explicitly asks for a full rebuild.

This is the incremental updater, not the daily reader. Use `repo-read.md` for task-time search over existing memory. Use `repo-build.md` for first-time creation, full rebuild flows, or full refresh work that should rerun builder collection with `collect_all.py --reuse`.

## Prerequisite

The user selects a repository, not a memory directory. Derive the memory path as `<repo>/.repo_memory`.

Require:

- `<repo>` is a local git repository;
- `<repo>/.repo_memory/PROFILE.md` exists;
- existing `PROFILE.md` and `resources/*.md` are treated as the baseline.

If `.repo_memory/PROFILE.md` is missing, stop and route to `repo-build.md`; do not fabricate an incremental baseline from live files alone.

## Path Convention

`<skill-dir>` means the parent directory of the `references/` directory containing this file.

## Default Settings

Updater uses `<skill-dir>/defaults.json` so build and update operations stay aligned. It reads `repoHistory.mode`, `repoHistory.limits.prs`, `repoHistory.limits.issues`, and `summaryChars`, with compatibility fallback to legacy `limits.prs` and `limits.issues`; local commit deltas are not limit-capped because they are computed from the stored baseline commit to current `HEAD` when commit history is enabled.

Override one updater run with `--history-mode`, `--pr-limit`, `--issue-limit`, or `--summary-chars`. Do not edit `defaults.json` for a one-run updater request.

History policy modes:

- `none`: skip local commit deltas and provider PR/MR/issue deltas.
- `commits-only` or `local-only`: detect local commit deltas and skip provider PR/MR/issue deltas.
- `provider`: detect local commit deltas and fetch provider PR/MR/issue deltas when provider evidence is ready.
- `provider-required`: detect local commit deltas and require provider PR/MR/issue evidence rather than silently authoring provider resources without evidence.

Do not re-enable commit or provider channels disabled by policy. Use the detector's `effective_settings.history` block as the authority for which history channels are enabled.

## User Count Requests

If the user says how many PRs/MRs or issues to compare, pass explicit one-run flags:

- "PR 拉 20 条" means add `--pr-limit 20`.
- "issue 拉 30 条" means add `--issue-limit 30`.
- "PR 和 issue 都拉 50 条" means add `--pr-limit 50 --issue-limit 50`.

Only change `<skill-dir>/defaults.json` when the user asks to change future default behavior.

## Detection

Start every updater run by detecting deltas:

```bash
python3 <skill-dir>/scripts/detect_updates.py \
  --repo-path <repo-path> \
  --pretty
```

The detector:

- reads the effective history policy from `repoHistory.mode` and reports it in `effective_settings.history`;
- reads the commit baseline from `PROFILE.md` `local_head`, with a fallback to the nearest stored ancestor commit in `resources/commits.md`;
- reads PR/MR and issue baselines from `resources/prs.md` and `resources/issues.md`, enriched by existing raw provider facets when available;
- compares local git `HEAD` against the newest stored commit only when commit history is enabled;
- reports `local_commit_status.status: "skipped"` with `reason: "history_disabled_by_policy"` when `repoHistory.mode` disables commit history;
- reports `local_commit_status.status: "skipped"` with `reason: "missing_baseline_commit"` when no stored local commit baseline can be found;
- reports `local_commit_status.status: "skipped"` with `reason: "baseline_not_ancestor_of_head"` when history was rebased or force-pushed, instead of silently returning no commit delta;
- detects provider state from live git remotes and provider CLIs only when provider history is enabled;
- fetches bounded latest GitHub/GitLab PR/MR/issue facets only when provider history is enabled and provider evidence is available;
- reports added or changed PR/MR/issue numbers without editing memory files;
- reports provider items missing from the current bounded window as `baseline_only_numbers`, not as deletions.

Warnings such as rewritten commit baselines or provider fetch failures are returned as structured `notices[]` with `render_as: "assistant_message"`. Show these notices to the user as normal assistant messages; do not bury them in terminal output.

When provider fetch succeeds, the report includes fetched authoring evidence under `current.provider_items.pull_requests` and `current.provider_items.issues`. Use those facets, plus existing raw/provider resources when needed, to write or replace search-grade sections; do not author provider sections from number-only deltas.

The report also includes `builder_helpers` fingerprints for the sibling builder files that updater depends on, including `path`, `exists`, `mtime_ns`, and `size_bytes`. Use this only for compatibility diagnostics; it is not evidence for authoring repo-memory resources.

Use `--history-mode local-only` or `--history-mode none` when the user requests a one-run history-policy override. `--provider-mode off` remains supported as a compatibility provider-only override when provider access is intentionally unavailable.

## Provider Sandbox and Transport Failures

`gh/glab` provider delta detection needs external network access. If `current.provider_fetch.ok` is false, or notice/stderr shows `fetch failed`, timeout, DNS, connection, TLS, `ENOTFOUND`, `EAI_AGAIN`, or similar transport text, show `Provider Delta Fetch Failed`, keep existing PR/MR/issue resources unchanged, and continue only with safe local commit updates.

Verify provider authentication in the same normal shell with the command reported by `detect_updates.py`; for GitHub Enterprise or self-hosted GitLab this may include `--hostname <host>`. Authenticate with `gh auth login` or `glab auth login` (also host-scoped when prompted), then rerun `detect_updates.py`; do not paste tokens into the skill or call provider APIs directly.

Do not use a restricted shell sandbox to verify provider/API availability. Verify in a normal shell or approved network-enabled mode before editing provider resources. If only restricted shell access is available, keep existing PR/MR/issue resources unchanged and continue only with safe local commit updates. Do not treat provider fetch failure as no PR/issue delta, empty provider evidence, bad login, or bypass the detector with direct APIs, browser scraping, copied credentials, or hand-written raw facets.

## Report Gates

Read the JSON report as gates before editing. Stop at the first blocking gate; otherwise edit only resources named by the report.

| Report field | Action |
| --- | --- |
| `ok: false` and missing memory | Route to `repo-build.md`; do not invent a baseline. |
| no deltas and no notices | Report no update needed. |
| `commit_delta_skipped: "missing_baseline_commit"` | Stop commit-resource updates; recommend full rebuild. |
| `commit_delta_skipped: "baseline_not_ancestor_of_head"` | Stop commit-resource updates; recommend rebuild or explicit baseline reset. |
| provider fetch failed / `Provider Delta Fetch Failed` | Keep existing PR/MR/issue resources unchanged; local commits may still be updated. |
| `deltas.local_commits` | Update only `resources/commits.md`; refresh `PROFILE.md` local head and `generated_at` timestamp. |
| `deltas.pull_requests.upsert_numbers` | Upsert only matching PR/MR sections from `current.provider_items.pull_requests`; preserve `baseline_only_numbers`. |
| `deltas.issues.upsert_numbers` | Upsert only matching issue sections from `current.provider_items.issues`; preserve `baseline_only_numbers`. |

Never delete prior PR/MR/issue sections because they are absent from the bounded provider fetch; deletion needs explicit user instruction or verified invalidation. Finish with the builder validator:

```bash
python3 <skill-dir>/scripts/validate_memory.py <repo-path> --pretty
```

Whenever an update changes generated repo-memory artifacts, set `PROFILE.md.generated_at` to the successful update time, `PROFILE.md.local_head` to the processed snapshot, and `PROFILE.md.source_tree` to the snapshot's full Git tree SHA. The read-time cooldown policy uses this timestamp; do not preserve an older build time after a successful incremental update.

## Authoring Rules

Match existing fixed-field sections first; use builder templates only when the file shape is incomplete. Upsert by stable keys: commit SHA, PR/MR number, and issue number. Replace matching sections, append only new keys, and do not delete useful notes unless explicit instruction or verified evidence invalidates them.

Every new or replaced section needs a search-grade `Description`, evidence strength, and source from local commit delta, `current.provider_items`, or raw provider files. Number-only deltas route the edit; they are not authoring evidence. Do not invent PR/MR/issue evidence when provider fetch is skipped or unavailable, paste long raw summaries, or treat validator/counts as proof of body quality.

## Trust Rules

- Live code and targeted verification are stronger than repo memory.
- Local commit deltas are checkout history, not current behavior proof.
- Provider PR/MR/issue deltas are historical or planning context, not implementation truth.
- Open PRs/MRs are branch intent, not landed behavior.
- Issues are user/problem context, not implementation proof.
