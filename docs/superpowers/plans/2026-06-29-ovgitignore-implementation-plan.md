# Account-Level .ovgitignore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an account-level `.ovgitignore` file that excludes matching files from future VikingFS git snapshot commits while keeping `.ovgitignore` itself versioned and restore/show semantics unchanged.

**Architecture:** Add a focused Rust ignore matcher under `crates/ragfs/src/git/ignore.rs`, load it once per `GitService::commit()`, and apply it alongside the existing system prune rules when building commit candidates and cleaning previous-tree entries. Python gets small management helpers for the account-root file and skips `.ovgitignore` during restore vector reindex classification.

**Tech Stack:** Rust `ragfs` git module, existing `ignore = "0.4.25"` dependency, PyO3 `ragfs-python` binding, Python `VikingFS`, pytest, cargo test.

## Global Constraints

- `.ovgitignore` lives at `/local/{account_id}/.ovgitignore` and has Git tree path `.ovgitignore`.
- `.ovgitignore` is not exposed as normal `viking://.ovgitignore` content URI; users manage it through dedicated `VikingFS` methods.
- `.ovgitignore` is always versioned, even if user rules match it.
- Ignore rules affect `commit` only; `restore`, `show`, and `log` must not apply the current `.ovgitignore` as a filter.
- The feature is OpenViking snapshot exclude semantics, not Git index-compatible tracked-file semantics.
- Unsupported `!` negation must fail commit with a clear invalid-ignore error.
- Non-UTF-8 `.ovgitignore` must fail commit with a clear invalid-ignore error.
- `.ovgitignore` size limit is 64 KiB.
- `VikingFS.commit()` public signature remains unchanged.
- Commit response adds `ignored`, counting user-ignore skips only, not existing system pruning.
- Existing accounts without `.ovgitignore` behave exactly as before except for the added `ignored: 0` response field.

---

## File Structure

- Create `crates/ragfs/src/git/ignore.rs`
  - Owns `.ovgitignore` parsing, normalization, size/UTF-8 validation, and matching.
  - Exposes `OVGITIGNORE_PATH`, `OVGITIGNORE_MAX_BYTES`, `IgnoreMatcher`, and `should_track_path`.

- Modify `crates/ragfs/src/git/mod.rs`
  - Registers and re-exports the new ignore module.

- Modify `crates/ragfs/src/git/error.rs`
  - Adds `InvalidIgnoreFile` and `IgnoreFileTooLarge` error variants.

- Modify `crates/ragfs/src/git/types.rs`
  - Adds `ignored: usize` to `CommitResponse::Created` and `CommitResponse::Noop`.

- Modify `crates/ragfs/src/git/service.rs`
  - Loads account-root `.ovgitignore` once per commit.
  - Applies `should_track_path` during full and scoped candidate construction.
  - Removes ignored previous-tree paths from new snapshots and the commit index seed.
  - Keeps restore/show unchanged.
  - Adds Rust service tests.

- Modify `crates/ragfs-python/src/git.rs`
  - Maps new errors to Python invalid-operation errors.
  - Adds `ignored` to commit response conversion.
  - Updates binding unit tests for the new response shape.

- Modify `openviking/storage/viking_fs.py`
  - Adds `get_gitignore`, `set_gitignore`, and `delete_gitignore` methods.
  - Skips `.ovgitignore` in `_classify_restore_path`.

- Modify `tests/agfs/test_git_binding.py`
  - Adds PyO3 end-to-end tests for commit ignore behavior.

- Modify `tests/agfs/test_viking_fs_git.py`
  - Adds Python-layer tests for management methods and restore vector classification.

- Modify `docs/design/git-version-control-design.md`
  - Documents account-level `.ovgitignore`, snapshot exclude semantics, restore behavior, and Python helpers.

---

### Task 1: Add Rust `.ovgitignore` matcher

**Files:**
- Create: `crates/ragfs/src/git/ignore.rs`
- Modify: `crates/ragfs/src/git/mod.rs:28-51`
- Modify: `crates/ragfs/src/git/error.rs:129-159`
- Test: `crates/ragfs/src/git/ignore.rs`

**Interfaces:**
- Consumes: `crate::git::enumerate::prune_path(rel: &str) -> bool`, `crate::git::error::GitError`.
- Produces:
  - `pub const OVGITIGNORE_PATH: &str = ".ovgitignore"`
  - `pub const OVGITIGNORE_MAX_BYTES: usize = 64 * 1024`
  - `pub struct IgnoreMatcher`
  - `impl IgnoreMatcher { pub fn empty() -> Self; pub fn parse(bytes: &[u8]) -> Result<Self, GitError>; pub fn is_ignored(&self, rel_path: &str) -> bool }`
  - `pub fn should_track_path(rel_path: &str, matcher: &IgnoreMatcher) -> bool`

- [ ] **Step 1: Write failing matcher tests**

Create `crates/ragfs/src/git/ignore.rs` with the module skeleton and tests first. The skeleton intentionally returns empty behavior so the tests fail.

```rust
//! Account-level `.ovgitignore` parsing and matching.

use crate::git::error::GitError;

pub const OVGITIGNORE_PATH: &str = ".ovgitignore";
pub const OVGITIGNORE_MAX_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Default)]
pub struct IgnoreMatcher;

impl IgnoreMatcher {
    pub fn empty() -> Self {
        Self
    }

    pub fn parse(_bytes: &[u8]) -> Result<Self, GitError> {
        Ok(Self::empty())
    }

    pub fn is_ignored(&self, _rel_path: &str) -> bool {
        false
    }
}

pub fn should_track_path(rel_path: &str, matcher: &IgnoreMatcher) -> bool {
    if rel_path == OVGITIGNORE_PATH {
        return true;
    }
    !crate::git::enumerate::prune_path(rel_path) && !matcher.is_ignored(rel_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn matcher(src: &str) -> IgnoreMatcher {
        IgnoreMatcher::parse(src.as_bytes()).expect("ignore file parses")
    }

    #[test]
    fn empty_comments_and_blank_lines_match_nothing() {
        let m = matcher("\n  \n# comment\n   # indented comment\n");
        assert!(!m.is_ignored("resources/a.log"));
        assert!(should_track_path("resources/a.log", &m));
    }

    #[test]
    fn basename_glob_matches_at_any_depth() {
        let m = matcher("*.log\n");
        assert!(m.is_ignored("resources/a.log"));
        assert!(m.is_ignored("resources/proj/nested/a.log"));
        assert!(!m.is_ignored("resources/a.md"));
    }

    #[test]
    fn double_star_glob_matches_nested_paths() {
        let m = matcher("**/*.bak\n");
        assert!(m.is_ignored("a.bak"));
        assert!(m.is_ignored("resources/proj/a.bak"));
        assert!(!m.is_ignored("resources/proj/a.md"));
    }

    #[test]
    fn root_relative_patterns_match_from_account_root() {
        let m = matcher("resources/tmp/**\n/resources/cache/**\n");
        assert!(m.is_ignored("resources/tmp/a.txt"));
        assert!(m.is_ignored("resources/tmp/nested/a.txt"));
        assert!(m.is_ignored("resources/cache/a.txt"));
        assert!(!m.is_ignored("user/default/resources/tmp/a.txt"));
    }

    #[test]
    fn directory_patterns_match_directory_contents() {
        let m = matcher("tmp/\n/cache/\n");
        assert!(m.is_ignored("resources/tmp/a.txt"));
        assert!(m.is_ignored("tmp/a.txt"));
        assert!(m.is_ignored("cache/a.txt"));
        assert!(!m.is_ignored("resources/cache/a.txt"));
    }

    #[test]
    fn ovgitignore_is_always_tracked() {
        let m = matcher("*\n.ovgitignore\n");
        assert!(m.is_ignored("resources/a.md"));
        assert!(should_track_path(OVGITIGNORE_PATH, &m));
    }

    #[test]
    fn system_prune_still_wins() {
        let m = IgnoreMatcher::empty();
        assert!(!should_track_path("_system/state.json", &m));
        assert!(!should_track_path("resources/index.faiss", &m));
        assert!(!should_track_path("resources/embedding_cache/a.bin", &m));
    }

    #[test]
    fn negation_is_rejected() {
        let err = IgnoreMatcher::parse(b"!keep.log\n").unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
        assert!(err.to_string().contains("negation"));
    }

    #[test]
    fn non_utf8_is_rejected() {
        let err = IgnoreMatcher::parse(&[0xff, 0xfe]).unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
        assert!(err.to_string().contains("UTF-8"));
    }

    #[test]
    fn oversized_file_is_rejected() {
        let bytes = vec![b'a'; OVGITIGNORE_MAX_BYTES + 1];
        let err = IgnoreMatcher::parse(&bytes).unwrap_err();
        assert!(matches!(err, GitError::IgnoreFileTooLarge { .. }));
    }
}
```

- [ ] **Step 2: Run matcher tests to verify they fail to compile**

Run:

```bash
cargo test -p ragfs git::ignore::tests --lib
```

Expected: FAIL because `GitError::InvalidIgnoreFile` and `GitError::IgnoreFileTooLarge` do not exist and `git::ignore` is not registered in `mod.rs`.

- [ ] **Step 3: Register module and add error variants**

Modify `crates/ragfs/src/git/mod.rs` around the module list:

```rust
pub mod backends;
pub mod commit;
pub mod config;
pub mod enumerate;
pub mod error;
pub mod ignore;
pub mod index_store;
pub mod object_store;
pub mod ref_store;
pub mod service;
pub mod tree_builder;
pub mod types;
pub mod util;
```

Modify the re-exports near `pub use error`:

```rust
pub use ignore::{should_track_path, IgnoreMatcher, OVGITIGNORE_MAX_BYTES, OVGITIGNORE_PATH};
```

Modify `crates/ragfs/src/git/error.rs` after `TooManyFiles`:

```rust
    /// `.ovgitignore` exceeds the configured size limit.
    #[error("ignore file too large: {path} is {size} bytes, limit {max} bytes")]
    IgnoreFileTooLarge {
        path: String,
        size: u64,
        max: u64,
    },

    /// `.ovgitignore` is syntactically invalid or cannot be decoded.
    #[error("invalid ignore file {path}: {reason}")]
    InvalidIgnoreFile { path: String, reason: String },
```

- [ ] **Step 4: Implement parser and matcher**

Replace the skeleton in `crates/ragfs/src/git/ignore.rs` with this implementation. It uses the existing `ignore` crate's gitignore matcher while rejecting unsupported syntax before building rules.

```rust
//! Account-level `.ovgitignore` parsing and matching.
//!
//! The syntax is a documented OpenViking subset of root `.gitignore` rules.
//! It intentionally rejects negation so callers do not assume full Git index
//! semantics.

use std::path::Path;

use ignore::gitignore::{Gitignore, GitignoreBuilder};

use crate::git::error::GitError;

pub const OVGITIGNORE_PATH: &str = ".ovgitignore";
pub const OVGITIGNORE_MAX_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone)]
pub struct IgnoreMatcher {
    inner: Option<Gitignore>,
}

impl Default for IgnoreMatcher {
    fn default() -> Self {
        Self::empty()
    }
}

impl IgnoreMatcher {
    pub fn empty() -> Self {
        Self { inner: None }
    }

    pub fn parse(bytes: &[u8]) -> Result<Self, GitError> {
        if bytes.len() > OVGITIGNORE_MAX_BYTES {
            return Err(GitError::IgnoreFileTooLarge {
                path: OVGITIGNORE_PATH.to_string(),
                size: bytes.len() as u64,
                max: OVGITIGNORE_MAX_BYTES as u64,
            });
        }

        let text = std::str::from_utf8(bytes).map_err(|e| GitError::InvalidIgnoreFile {
            path: OVGITIGNORE_PATH.to_string(),
            reason: format!("must be UTF-8: {e}"),
        })?;

        let mut builder = GitignoreBuilder::new(Path::new(""));
        let mut added = false;
        for (idx, raw) in text.lines().enumerate() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if line.starts_with('!') {
                return Err(GitError::InvalidIgnoreFile {
                    path: OVGITIGNORE_PATH.to_string(),
                    reason: format!(
                        "line {} uses unsupported negation: {}",
                        idx + 1,
                        line
                    ),
                });
            }
            builder.add_line(Some(OVGITIGNORE_PATH.into()), line).map_err(|e| {
                GitError::InvalidIgnoreFile {
                    path: OVGITIGNORE_PATH.to_string(),
                    reason: format!("line {} is invalid: {e}", idx + 1),
                }
            })?;
            added = true;
        }

        if !added {
            return Ok(Self::empty());
        }

        let inner = builder.build().map_err(|e| GitError::InvalidIgnoreFile {
            path: OVGITIGNORE_PATH.to_string(),
            reason: e.to_string(),
        })?;
        Ok(Self { inner: Some(inner) })
    }

    pub fn is_ignored(&self, rel_path: &str) -> bool {
        let Some(inner) = &self.inner else {
            return false;
        };
        let cleaned = rel_path.trim_matches('/');
        if cleaned.is_empty() || cleaned == OVGITIGNORE_PATH {
            return false;
        }
        inner
            .matched_path_or_any_parents(Path::new(cleaned), false)
            .is_ignore()
    }
}

pub fn should_track_path(rel_path: &str, matcher: &IgnoreMatcher) -> bool {
    if rel_path == OVGITIGNORE_PATH {
        return true;
    }
    !crate::git::enumerate::prune_path(rel_path) && !matcher.is_ignored(rel_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn matcher(src: &str) -> IgnoreMatcher {
        IgnoreMatcher::parse(src.as_bytes()).expect("ignore file parses")
    }

    #[test]
    fn empty_comments_and_blank_lines_match_nothing() {
        let m = matcher("\n  \n# comment\n   # indented comment\n");
        assert!(!m.is_ignored("resources/a.log"));
        assert!(should_track_path("resources/a.log", &m));
    }

    #[test]
    fn basename_glob_matches_at_any_depth() {
        let m = matcher("*.log\n");
        assert!(m.is_ignored("resources/a.log"));
        assert!(m.is_ignored("resources/proj/nested/a.log"));
        assert!(!m.is_ignored("resources/a.md"));
    }

    #[test]
    fn double_star_glob_matches_nested_paths() {
        let m = matcher("**/*.bak\n");
        assert!(m.is_ignored("a.bak"));
        assert!(m.is_ignored("resources/proj/a.bak"));
        assert!(!m.is_ignored("resources/proj/a.md"));
    }

    #[test]
    fn root_relative_patterns_match_from_account_root() {
        let m = matcher("resources/tmp/**\n/resources/cache/**\n");
        assert!(m.is_ignored("resources/tmp/a.txt"));
        assert!(m.is_ignored("resources/tmp/nested/a.txt"));
        assert!(m.is_ignored("resources/cache/a.txt"));
        assert!(!m.is_ignored("user/default/resources/tmp/a.txt"));
    }

    #[test]
    fn directory_patterns_match_directory_contents() {
        let m = matcher("tmp/\n/cache/\n");
        assert!(m.is_ignored("resources/tmp/a.txt"));
        assert!(m.is_ignored("tmp/a.txt"));
        assert!(m.is_ignored("cache/a.txt"));
        assert!(!m.is_ignored("resources/cache/a.txt"));
    }

    #[test]
    fn ovgitignore_is_always_tracked() {
        let m = matcher("*\n.ovgitignore\n");
        assert!(m.is_ignored("resources/a.md"));
        assert!(should_track_path(OVGITIGNORE_PATH, &m));
    }

    #[test]
    fn system_prune_still_wins() {
        let m = IgnoreMatcher::empty();
        assert!(!should_track_path("_system/state.json", &m));
        assert!(!should_track_path("resources/index.faiss", &m));
        assert!(!should_track_path("resources/embedding_cache/a.bin", &m));
    }

    #[test]
    fn negation_is_rejected() {
        let err = IgnoreMatcher::parse(b"!keep.log\n").unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
        assert!(err.to_string().contains("negation"));
    }

    #[test]
    fn non_utf8_is_rejected() {
        let err = IgnoreMatcher::parse(&[0xff, 0xfe]).unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
        assert!(err.to_string().contains("UTF-8"));
    }

    #[test]
    fn oversized_file_is_rejected() {
        let bytes = vec![b'a'; OVGITIGNORE_MAX_BYTES + 1];
        let err = IgnoreMatcher::parse(&bytes).unwrap_err();
        assert!(matches!(err, GitError::IgnoreFileTooLarge { .. }));
    }
}
```

- [ ] **Step 5: Run matcher tests to verify they pass**

Run:

```bash
cargo test -p ragfs git::ignore::tests --lib
```

Expected: PASS for all tests in `git::ignore::tests`.

- [ ] **Step 6: Commit Task 1**

```bash
git add crates/ragfs/src/git/ignore.rs crates/ragfs/src/git/mod.rs crates/ragfs/src/git/error.rs
git commit -m "feat(git): add ovgitignore matcher" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Integrate `.ovgitignore` into Rust commit snapshots

**Files:**
- Modify: `crates/ragfs/src/git/types.rs:16-22`
- Modify: `crates/ragfs/src/git/service.rs:14-586`
- Test: `crates/ragfs/src/git/service.rs` test module near existing commit tests

**Interfaces:**
- Consumes from Task 1:
  - `IgnoreMatcher::parse(bytes: &[u8]) -> Result<IgnoreMatcher, GitError>`
  - `IgnoreMatcher::empty() -> IgnoreMatcher`
  - `should_track_path(rel_path: &str, matcher: &IgnoreMatcher) -> bool`
  - `OVGITIGNORE_PATH`
- Produces:
  - `CommitResponse::Created { commit_oid: ObjectId, changed: usize, ignored: usize }`
  - `CommitResponse::Noop { commit_oid: ObjectId, ignored: usize }`
  - `GitService::commit()` applies `.ovgitignore` to full and scoped commits.

- [ ] **Step 1: Update `CommitResponse` and let compile fail at all match sites**

Modify `crates/ragfs/src/git/types.rs`:

```rust
#[derive(Debug, Clone)]
pub enum CommitResponse {
    Created {
        commit_oid: ObjectId,
        changed: usize,
        /// Number of candidate paths skipped by user `.ovgitignore` rules.
        /// Existing hardcoded system pruning is not included.
        ignored: usize,
    },
    /// No path produced an editor change; ref untouched. `commit_oid` is the
    /// existing HEAD (or `ObjectId::null` if the branch did not exist).
    Noop {
        commit_oid: ObjectId,
        /// Number of candidate paths skipped by user `.ovgitignore` rules.
        ignored: usize,
    },
}
```

- [ ] **Step 2: Run a targeted compile to see match failures**

Run:

```bash
cargo test -p ragfs git::service::tests::test_commit_first_creates_root_commit --lib
```

Expected: FAIL with Rust errors for missing `ignored` fields in `CommitResponse` constructors and match patterns.

- [ ] **Step 3: Add ignore loading helper in `service.rs`**

Modify imports at `crates/ragfs/src/git/service.rs:23-32` to include ignore symbols:

```rust
use crate::git::{
    error::{GitError, ObjectStoreError, RefStoreError},
    ignore::{should_track_path, IgnoreMatcher, OVGITIGNORE_PATH},
    index_store::{CommitIndex, IndexStore},
    object_store::ObjectStore,
    ref_store::RefStore,
    types::{
        CommitRequest, CommitResponse, IndexEntry, RestoreRequest, RestoreResponse, ShowRequest,
        ShowResponse,
    },
};
```

Add this helper near `is_not_found` in `service.rs`:

```rust
async fn load_ignore_matcher(
    vfs: &Arc<dyn FileSystem>,
    account: &str,
) -> Result<IgnoreMatcher, GitError> {
    let abs = format!("/local/{}/{}", account, OVGITIGNORE_PATH);
    match vfs.read(&abs, 0, 0).await {
        Ok(bytes) => IgnoreMatcher::parse(&bytes),
        Err(e) if is_not_found(&e) => Ok(IgnoreMatcher::empty()),
        Err(e) => Err(e.into()),
    }
}
```

- [ ] **Step 4: Load matcher once at the start of commit**

In `GitService::commit()` after `let ref_name = format!("refs/heads/{branch}");`, add:

```rust
        let ignore_matcher = load_ignore_matcher(&self.vfs, &account).await?;
        let mut ignored = 0usize;
```

- [ ] **Step 5: Replace explicit-path pruning with user-ignore-aware filtering**

In the explicit paths section, replace each `!crate::git::enumerate::prune_path(...)` condition with `should_track_path(...)`, and increment `ignored` only when the path is skipped by user ignore, not system prune.

Use this local helper inside `commit()` immediately after `let mut ignored = 0usize;`:

```rust
        let mut include_path = |path: &str| -> bool {
            if crate::git::enumerate::prune_path(path) {
                return false;
            }
            if !should_track_path(path, &ignore_matcher) {
                ignored += 1;
                return false;
            }
            true
        };
```

Then replace explicit path checks:

```rust
if include_path(path) {
    set.insert(path.clone());
}
```

and:

```rust
if path.starts_with(&pref) && include_path(path) {
    set.insert(path.clone());
}
```

and:

```rust
if include_path(p) {
    set.insert(p.clone());
}
```

- [ ] **Step 6: Filter full enumeration current and previous paths**

Replace the `None => { ... }` full enumeration arm with:

```rust
            None => {
                let listed = crate::git::enumerate::collect_all(&self.vfs, &account).await?;
                let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
                for p in listed {
                    if include_path(&p) {
                        set.insert(p);
                    }
                }
                if let Some(t) = prev_tree {
                    let prev_paths = crate::git::tree_builder::flatten(
                        self.object_store.as_ref(),
                        &account,
                        t,
                        &None,
                    )
                    .await?;
                    for (p, _) in prev_paths {
                        if include_path(&p) {
                            set.insert(p);
                        } else {
                            // Ensure previously tracked ignored paths are removed
                            // from the next tree and from the commit index seed.
                            editor
                                .remove(self.object_store.as_ref(), &account, &p)
                                .await?;
                            changed += 1;
                        }
                    }
                }
                set.into_iter().collect()
            }
```

Important: this uses `changed` before it is currently declared later. Move `let mut changed = 0usize;` from the loop section up before the `candidates` construction, immediately after `let mut ignored = 0usize;`. Remove the later duplicate declaration.

- [ ] **Step 7: Filter commit index seed for ignored paths**

After `new_index_entries` is initialized and after partial cleanup logic, add:

```rust
        if self.index_store.is_some() {
            new_index_entries.retain(|path, _| should_track_path(path, &ignore_matcher));
        }
```

This prevents Fast Path 1 from keeping cache entries for paths that have become ignored.

- [ ] **Step 8: Include `ignored` in commit responses**

Update no-op return:

```rust
            return Ok(CommitResponse::Noop {
                commit_oid: noop_oid,
                ignored,
            });
```

Update created return:

```rust
        Ok(CommitResponse::Created {
            commit_oid,
            changed,
            ignored,
        })
```

Update test match patterns in `service.rs` from:

```rust
CommitResponse::Created { commit_oid, .. } => commit_oid,
```

This pattern already works. Update any `CommitResponse::Noop { commit_oid }` patterns to:

```rust
CommitResponse::Noop { commit_oid, .. } => commit_oid,
```

- [ ] **Step 9: Add Rust GitService failing tests**

Add `OVGITIGNORE_PATH` to the test module imports in `crates/ragfs/src/git/service.rs` near the existing git imports:

```rust
    use crate::git::ignore::OVGITIGNORE_PATH;
```

Append these tests near the existing commit tests in `crates/ragfs/src/git/service.rs`:

```rust
    #[tokio::test]
    async fn test_commit_ovgitignore_excludes_matching_files_and_tracks_itself() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_ignore");
        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        vfs.put("resources/a.md", b"keep");
        vfs.put("resources/a.log", b"skip");

        let resp = svc
            .commit(req("acct_ignore", "main", "ignore", None))
            .await
            .unwrap();
        let (commit_oid, ignored) = match resp {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        assert_eq!(ignored, 1);

        let tree = commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct_ignore", commit_oid).await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(
            paths,
            vec![
                OVGITIGNORE_PATH.to_string(),
                "resources/a.md".to_string(),
            ]
        );
    }

    #[tokio::test]
    async fn test_commit_ovgitignore_removes_previously_tracked_file_from_snapshot() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_ignore_remove");
        vfs.put("resources/a.log", b"tracked before ignore");
        let first = make_commit(&svc, "acct_ignore_remove", "main", "first").await;
        let first_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            first,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            first_tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_some());

        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        let second = svc
            .commit(req("acct_ignore_remove", "main", "second", None))
            .await
            .unwrap();
        let (second_oid, ignored) = match second {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        assert!(ignored >= 1);

        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            second_oid,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            second_tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_none());
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            second_tree,
            OVGITIGNORE_PATH,
        )
        .await
        .unwrap()
        .is_some());
    }

    #[tokio::test]
    async fn test_commit_scoped_paths_respect_ovgitignore() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_ignore_scoped");
        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        vfs.put("resources/a.md", b"keep");
        vfs.put("resources/a.log", b"skip");

        let resp = svc
            .commit(req(
                "acct_ignore_scoped",
                "main",
                "scoped",
                Some(vec![
                    "resources/a.md".to_string(),
                    "resources/a.log".to_string(),
                ]),
            ))
            .await
            .unwrap();
        let (commit_oid, ignored) = match resp {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        assert_eq!(ignored, 1);

        let tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_scoped",
            commit_oid,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_scoped",
            tree,
            "resources/a.md",
        )
        .await
        .unwrap()
        .is_some());
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_scoped",
            tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_none());
    }

    #[tokio::test]
    async fn test_commit_invalid_ovgitignore_fails() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_bad_ignore");
        vfs.put(OVGITIGNORE_PATH, b"!keep.log\n");

        let err = svc
            .commit(req("acct_bad_ignore", "main", "bad", None))
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
    }
```

- [ ] **Step 10: Run GitService tests**

Run:

```bash
cargo test -p ragfs git::service::tests::test_commit_ovgitignore --lib
```

Expected: PASS for the three tests whose names start with `test_commit_ovgitignore`.

Run:

```bash
cargo test -p ragfs git::service::tests::test_commit_invalid_ovgitignore_fails --lib
```

Expected: PASS.

- [ ] **Step 11: Run existing git service tests for regressions**

Run:

```bash
cargo test -p ragfs git::service::tests --lib
```

Expected: PASS for all `git::service::tests`.

- [ ] **Step 12: Commit Task 2**

```bash
git add crates/ragfs/src/git/types.rs crates/ragfs/src/git/service.rs
git commit -m "feat(git): apply ovgitignore during commits" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Update PyO3 response and error mapping

**Files:**
- Modify: `crates/ragfs-python/src/git.rs:167-198`
- Modify: `crates/ragfs-python/src/git.rs:354-370`
- Test: `crates/ragfs-python/src/git.rs` unit tests near existing commit response tests
- Test: `tests/agfs/test_git_binding.py`

**Interfaces:**
- Consumes from Task 2:
  - `CommitResponse::Created { commit_oid, changed, ignored }`
  - `CommitResponse::Noop { commit_oid, ignored }`
  - `GitError::InvalidIgnoreFile { .. }`
  - `GitError::IgnoreFileTooLarge { .. }`
- Produces Python dict shape:
  - Created: `{"result": "created", "commit_oid": str, "changed": int, "ignored": int}`
  - Noop: `{"result": "noop", "commit_oid": str, "ignored": int}`

- [ ] **Step 1: Update response converter test first**

Modify the existing `commit_response_to_pydict` tests in `crates/ragfs-python/src/git.rs` so they expect `ignored`.

Use this for the created test body:

```rust
            let resp = ragfs::git::CommitResponse::Created {
                commit_oid: oid,
                changed: 2,
                ignored: 3,
            };
            let obj = commit_response_to_pydict(py, resp).expect("converts");
            let d = obj.bind(py).downcast::<PyDict>().unwrap();
            assert_eq!(d.get_item("result").unwrap().unwrap().extract::<String>().unwrap(), "created");
            assert_eq!(d.get_item("commit_oid").unwrap().unwrap().extract::<String>().unwrap(), oid.to_hex().to_string());
            assert_eq!(d.get_item("changed").unwrap().unwrap().extract::<usize>().unwrap(), 2);
            assert_eq!(d.get_item("ignored").unwrap().unwrap().extract::<usize>().unwrap(), 3);
```

Use this for the noop test body:

```rust
            let resp = ragfs::git::CommitResponse::Noop {
                commit_oid: oid,
                ignored: 4,
            };
            let obj = commit_response_to_pydict(py, resp).expect("converts");
            let d = obj.bind(py).downcast::<PyDict>().unwrap();
            assert_eq!(d.get_item("result").unwrap().unwrap().extract::<String>().unwrap(), "noop");
            assert_eq!(d.get_item("commit_oid").unwrap().unwrap().extract::<String>().unwrap(), oid.to_hex().to_string());
            assert_eq!(d.get_item("ignored").unwrap().unwrap().extract::<usize>().unwrap(), 4);
```

- [ ] **Step 2: Run binding unit test to verify it fails**

Run:

```bash
cargo test -p ragfs-python git::tests::commit_response_to_pydict --lib
```

Expected: FAIL because `commit_response_to_pydict` does not set `ignored` yet.

- [ ] **Step 3: Update response converter**

Modify `crates/ragfs-python/src/git.rs:354-370`:

```rust
pub fn commit_response_to_pydict(py: Python<'_>, resp: CommitResponse) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    match resp {
        CommitResponse::Created {
            commit_oid,
            changed,
            ignored,
        } => {
            d.set_item("result", "created")?;
            d.set_item("commit_oid", oid_hex(&commit_oid))?;
            d.set_item("changed", changed)?;
            d.set_item("ignored", ignored)?;
        }
        CommitResponse::Noop {
            commit_oid,
            ignored,
        } => {
            d.set_item("result", "noop")?;
            d.set_item("commit_oid", oid_hex(&commit_oid))?;
            d.set_item("ignored", ignored)?;
        }
    }
    Ok(d.into_any().unbind())
}
```

- [ ] **Step 4: Map ignore errors to invalid operation**

Modify `map_git_error` in `crates/ragfs-python/src/git.rs:167-198` by adding these match arms after `TooManyFiles`:

```rust
        GitError::IgnoreFileTooLarge { .. } => new_py_err_pub(py, "AGFSInvalidOperationError", msg),
        GitError::InvalidIgnoreFile { .. } => new_py_err_pub(py, "AGFSInvalidOperationError", msg),
```

- [ ] **Step 5: Add PyO3 E2E tests for ignored response and invalid ignore**

Append to `tests/agfs/test_git_binding.py`:

```python
def test_commit_respects_account_ovgitignore(client):
    account = "acct_ignore_binding"
    _write(client, account, ".ovgitignore", b"*.log\n")
    _write(client, account, "resources/keep.md", b"keep")
    _write(client, account, "resources/skip.log", b"skip")

    resp = client.git_commit(
        account=account,
        branch="main",
        message="ignore",
        author_name="tester",
        author_email="tester@example.com",
    )

    assert resp["result"] == "created"
    assert resp["ignored"] == 1
    assert client.git_show(
        account=account,
        target_ref="main",
        path="resources/keep.md",
    )["bytes"] == b"keep"
    assert client.git_show(
        account=account,
        target_ref="main",
        path=".ovgitignore",
    )["bytes"] == b"*.log\n"

    from openviking.pyagfs import AGFSNotFoundError
    with pytest.raises(AGFSNotFoundError):
        client.git_show(
            account=account,
            target_ref="main",
            path="resources/skip.log",
        )


def test_commit_invalid_ovgitignore_maps_to_invalid_operation(client):
    account = "acct_bad_ignore_binding"
    _write(client, account, ".ovgitignore", b"!keep.log\n")

    from openviking.pyagfs import AGFSInvalidOperationError
    with pytest.raises(AGFSInvalidOperationError) as excinfo:
        client.git_commit(
            account=account,
            branch="main",
            message="bad ignore",
            author_name="tester",
            author_email="tester@example.com",
        )

    assert "invalid ignore file" in str(excinfo.value).lower()
    assert "negation" in str(excinfo.value).lower()
```

- [ ] **Step 6: Run PyO3 unit and E2E tests**

Run:

```bash
cargo test -p ragfs-python git::tests::commit_response_to_pydict --lib
```

Expected: PASS.

Run:

```bash
pytest tests/agfs/test_git_binding.py::test_commit_respects_account_ovgitignore -q
pytest tests/agfs/test_git_binding.py::test_commit_invalid_ovgitignore_maps_to_invalid_operation -q
```

Expected: both PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add crates/ragfs-python/src/git.rs tests/agfs/test_git_binding.py
git commit -m "feat(pyagfs): expose ovgitignore commit results" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Add Python `VikingFS` management methods and restore reindex skip

**Files:**
- Modify: `openviking/storage/viking_fs.py:3384-3416`
- Modify: `openviking/storage/viking_fs.py` near git methods around `commit()`
- Test: `tests/agfs/test_viking_fs_git.py`

**Interfaces:**
- Consumes existing `VikingFS._ctx_or_default(ctx) -> RequestContext`, existing `_async_agfs.read/write/rm`, and existing `is_not_found_error` imported in `viking_fs.py`.
- Produces:
  - `async def get_gitignore(self, ctx: Optional[RequestContext] = None) -> str`
  - `async def set_gitignore(self, content: str, ctx: Optional[RequestContext] = None) -> None`
  - `async def delete_gitignore(self, ctx: Optional[RequestContext] = None) -> None`
  - `_classify_restore_path(".ovgitignore", deleted=...) is None`

- [ ] **Step 1: Add failing Python tests for management methods**

Append to `tests/agfs/test_viking_fs_git.py` near other git Python-layer tests:

```python
@pytest.mark.asyncio
async def test_vikingfs_gitignore_management_methods(vfs):
    ctx = _make_ctx(account="acct_gitignore_methods")

    assert await vfs.get_gitignore(ctx=ctx) == ""

    await vfs.set_gitignore("*.log\n", ctx=ctx)
    assert await vfs.get_gitignore(ctx=ctx) == "*.log\n"

    result = await vfs.commit(message="track ignore", ctx=ctx)
    assert result["result"] == "created"
    assert result["ignored"] == 0
    assert await vfs.show(result["commit_oid"], path=".ovgitignore", ctx=ctx) == b"*.log\n"

    await vfs.delete_gitignore(ctx=ctx)
    assert await vfs.get_gitignore(ctx=ctx) == ""
    await vfs.delete_gitignore(ctx=ctx)
    assert await vfs.get_gitignore(ctx=ctx) == ""
```

Modify existing `test_classify_restore_path` in `tests/agfs/test_viking_fs_git.py` by adding:

```python
    # Account-root .ovgitignore is versioned but has no vector side-effect.
    assert vfs._classify_restore_path(".ovgitignore", deleted=False) is None
    assert vfs._classify_restore_path(".ovgitignore", deleted=True) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/agfs/test_viking_fs_git.py::test_vikingfs_gitignore_management_methods -q
pytest tests/agfs/test_viking_fs_git.py::test_classify_restore_path -q
```

Expected: first FAILS with missing `get_gitignore`; second FAILS because `.ovgitignore` is classified as a source file.

- [ ] **Step 3: Add account-root path helper and management methods**

In `openviking/storage/viking_fs.py`, near the git helper constants before `_uri_to_tree_path`, add:

```python
    _OVGITIGNORE_TREE_PATH = ".ovgitignore"

    def _gitignore_agfs_path(self, ctx: Optional[RequestContext] = None) -> str:
        real_ctx = self._ctx_or_default(ctx)
        return f"/local/{real_ctx.account_id}/{self._OVGITIGNORE_TREE_PATH}"
```

Add these methods before `commit()`:

```python
    async def get_gitignore(self, ctx: Optional[RequestContext] = None) -> str:
        """Return the account-level .ovgitignore content, or an empty string if absent."""
        path = self._gitignore_agfs_path(ctx)
        try:
            raw = await self._async_agfs.read(path, 0, -1)
        except Exception as exc:
            if is_not_found_error(exc):
                return ""
            mapped = map_exception(exc, resource=path)
            if mapped is not None:
                raise mapped from exc
            raise
        if isinstance(raw, bytes):
            data = raw
        elif raw is not None and hasattr(raw, "content"):
            data = raw.content
        else:
            data = b""
        return data.decode("utf-8")

    async def set_gitignore(
        self,
        content: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Write the account-level .ovgitignore control file without semantic indexing."""
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        path = self._gitignore_agfs_path(ctx)
        data = content.encode("utf-8")
        await self._ensure_parent_dirs(path, ctx=ctx)
        await self._async_agfs.write(path, data)

    async def delete_gitignore(self, ctx: Optional[RequestContext] = None) -> None:
        """Delete the account-level .ovgitignore control file. Missing is success."""
        path = self._gitignore_agfs_path(ctx)
        try:
            await self._async_agfs.rm(path, recursive=False)
        except Exception as exc:
            if is_not_found_error(exc):
                return
            mapped = map_exception(exc, resource=path)
            if mapped is not None:
                raise mapped from exc
            raise
```

- [ ] **Step 4: Skip `.ovgitignore` during restore vector classification**

Modify `_classify_restore_path` in `openviking/storage/viking_fs.py:3384-3416` by adding this immediately after the docstring and before `parent, _, name = tree_path.rpartition("/")`:

```python
        if tree_path.strip("/") == self._OVGITIGNORE_TREE_PATH:
            return None
```

- [ ] **Step 5: Run Python tests**

Run:

```bash
pytest tests/agfs/test_viking_fs_git.py::test_vikingfs_gitignore_management_methods -q
pytest tests/agfs/test_viking_fs_git.py::test_classify_restore_path -q
```

Expected: both PASS.

- [ ] **Step 6: Add and run one Python-layer commit exclusion test**

Append to `tests/agfs/test_viking_fs_git.py`:

```python
@pytest.mark.asyncio
async def test_vikingfs_commit_respects_account_gitignore(vfs):
    ctx = _make_ctx(account="acct_vfs_ignore")
    await vfs.set_gitignore("*.log\n", ctx=ctx)
    await vfs.write("viking://resources/keep.md", b"keep", ctx=ctx)
    await vfs.write("viking://resources/skip.log", b"skip", ctx=ctx)

    result = await vfs.commit(message="ignore logs", ctx=ctx)

    assert result["result"] == "created"
    assert result["ignored"] == 1
    assert await vfs.show("main", path="viking://resources/keep.md", ctx=ctx) == b"keep"
    assert await vfs.show("main", path=".ovgitignore", ctx=ctx) == b"*.log\n"
    with pytest.raises(AGFSNotFoundError):
        await vfs.show("main", path="viking://resources/skip.log", ctx=ctx)
```

Run:

```bash
pytest tests/agfs/test_viking_fs_git.py::test_vikingfs_commit_respects_account_gitignore -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add openviking/storage/viking_fs.py tests/agfs/test_viking_fs_git.py
git commit -m "feat(vikingfs): manage account ovgitignore" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Verify restore ignores current `.ovgitignore` and update docs

**Files:**
- Modify: `tests/agfs/test_git_binding.py`
- Modify: `docs/design/git-version-control-design.md`

**Interfaces:**
- Consumes implemented behavior from Tasks 1-4.
- Produces documented restore behavior and regression coverage that current ignore rules do not filter restore.

- [ ] **Step 1: Add restore regression test**

Append to `tests/agfs/test_git_binding.py`:

```python
def test_restore_does_not_apply_current_ovgitignore(client):
    account = "acct_restore_ignore"
    _write(client, account, "resources/proj/a.log", b"v1")
    v1 = client.git_commit(
        account=account,
        branch="main",
        message="v1",
        author_name="tester",
        author_email="tester@example.com",
    )

    _write(client, account, ".ovgitignore", b"*.log\n")
    client.write(f"/local/{account}/resources/proj/a.log", b"v2")
    v2 = client.git_commit(
        account=account,
        branch="main",
        message="ignore logs",
        author_name="tester",
        author_email="tester@example.com",
    )
    assert v2["ignored"] >= 1

    restored = client.git_restore(
        account=account,
        branch="main",
        project_dir="resources/proj",
        source_commit=v1["commit_oid"],
        author_name="tester",
        author_email="tester@example.com",
    )

    assert restored["result"] == "applied"
    assert client.read(f"/local/{account}/resources/proj/a.log") == b"v1"
    assert client.git_show(
        account=account,
        target_ref="main",
        path="resources/proj/a.log",
    )["bytes"] == b"v1"
```

- [ ] **Step 2: Run restore regression test**

Run:

```bash
pytest tests/agfs/test_git_binding.py::test_restore_does_not_apply_current_ovgitignore -q
```

Expected: PASS. If it fails because restore commit construction accidentally filters ignored paths, remove that filtering from restore; restore must not read or apply `IgnoreMatcher`.

- [ ] **Step 3: Update design document path pruning section**

Modify [docs/design/git-version-control-design.md](docs/design/git-version-control-design.md) in section `## 4.2 路径剪枝(自动排除)` by adding this paragraph after the existing pruning table:

```markdown
此外，版本管理支持账号级 `.ovgitignore` 控制文件，物理路径为 `/local/{account_id}/.ovgitignore`，Git tree path 为 `.ovgitignore`。该文件使用账号根相对的 glob 子集规则，在 `commit` 枚举当前 VFS 文件和扫描上一版 tree 时共同生效；匹配的文件不会进入新的 snapshot commit，即使它们曾存在于历史 commit 中。`.ovgitignore` 文件自身始终进入版本管理，规则无法把它排除。
```

- [ ] **Step 4: Add restore documentation**

Modify the restore section around `## 8.2 restore 完整实现` by adding:

```markdown
> **`.ovgitignore` 与 restore:** `.ovgitignore` 只影响 `commit`，不影响 `restore` / `show` / `log`。restore 的输入是 source commit 与当前 HEAD 的 Git tree diff，不能用当前工作区 `.ovgitignore` 过滤，否则会导致历史 commit 中已经被跟踪的文件无法恢复。全账号 restore 时 `.ovgitignore` 作为普通已跟踪文件随 source commit 恢复；子目录 restore 默认不触碰账号根 `.ovgitignore`。
```

- [ ] **Step 5: Add Python API documentation**

Modify section `## 9.2 Python 侧 VikingFS 新增方法` by adding this method block near `commit`:

```python
    async def get_gitignore(self, ctx: RequestContext | None = None) -> str:
        """读取账号级 .ovgitignore；不存在时返回空字符串。"""

    async def set_gitignore(
        self,
        content: str,
        ctx: RequestContext | None = None,
    ) -> None:
        """写入账号级 .ovgitignore，不触发语义索引。"""

    async def delete_gitignore(self, ctx: RequestContext | None = None) -> None:
        """删除账号级 .ovgitignore；不存在视为成功。"""
```

- [ ] **Step 6: Update implementation progress section**

Modify `## 17. 当前实现进度与未实现项` by replacing the existing bullet:

```markdown
- 在版本管理中忽略某些特定文件 uri，类似 .gitignore 功能的实现。
```

with:

```markdown
- **账号级 `.ovgitignore`** —— 已实现。支持账号根 `.ovgitignore` glob 子集规则，规则仅在 `commit` 时生效；`.ovgitignore` 自身进入版本管理，不进入向量索引；`restore` 不应用当前 ignore 过滤。
```

- [ ] **Step 7: Run documentation diff check**

Run:

```bash
git diff -- docs/design/git-version-control-design.md
```

Expected: Diff contains only the `.ovgitignore` documentation described in Steps 3-6.

- [ ] **Step 8: Commit Task 5**

```bash
git add tests/agfs/test_git_binding.py docs/design/git-version-control-design.md
git commit -m "docs(git): document ovgitignore semantics" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Final verification

**Files:**
- No source changes expected.
- Uses all files modified by Tasks 1-5.

**Interfaces:**
- Consumes all implemented code paths.
- Produces confidence that Rust, PyO3, and Python-layer behavior all pass.

- [ ] **Step 1: Run Rust git module tests**

Run:

```bash
cargo test -p ragfs git::ignore::tests --lib
cargo test -p ragfs git::service::tests --lib
```

Expected: both commands PASS.

- [ ] **Step 2: Run PyO3 git unit tests**

Run:

```bash
cargo test -p ragfs-python git::tests --lib
```

Expected: PASS.

- [ ] **Step 3: Run binding E2E tests touched by this feature**

Run:

```bash
pytest tests/agfs/test_git_binding.py::test_commit_respects_account_ovgitignore -q
pytest tests/agfs/test_git_binding.py::test_commit_invalid_ovgitignore_maps_to_invalid_operation -q
pytest tests/agfs/test_git_binding.py::test_restore_does_not_apply_current_ovgitignore -q
```

Expected: all PASS.

- [ ] **Step 4: Run Python VikingFS tests touched by this feature**

Run:

```bash
pytest tests/agfs/test_viking_fs_git.py::test_vikingfs_gitignore_management_methods -q
pytest tests/agfs/test_viking_fs_git.py::test_vikingfs_commit_respects_account_gitignore -q
pytest tests/agfs/test_viking_fs_git.py::test_classify_restore_path -q
```

Expected: all PASS.

- [ ] **Step 5: Check working tree status**

Run:

```bash
git status --short
```

Expected: no unstaged source changes. If there are expected changes, commit them. If there are unexpected build artifacts, remove them or leave them untracked only if they are ignored by existing project rules.

- [ ] **Step 6: Final commit if verification required edits**

If Step 5 shows tracked edits from verification fixes, run:

```bash
git add <fixed-files>
git commit -m "fix(git): stabilize ovgitignore implementation" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

Expected: no commit is created if Step 5 is clean.
