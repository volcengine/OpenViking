//! Integration tests for the S3/TOS git storage backend (audit plan §A2).
//!
//! These tests exercise [`S3ObjectStore`] / [`S3RefStore`] against a *real*
//! S3-compatible backend (TOS / MinIO / LocalStack). They are gated behind the
//! `s3` feature **and** the presence of a usable `[git]` + `[git.s3]` section in
//! the OpenViking config file (`ov.conf`), mirroring the skip strategy used by
//! the Python `test_fs_binding_s3.py` suite.
//!
//! Config resolution (first hit wins):
//!   1. `OV_GIT_S3_CONF` env var (explicit path to an `ov.conf` JSON file)
//!   2. `OPENVIKING_CONFIG_FILE` env var
//!   3. `~/.openviking/ov.conf`
//!   4. `/etc/openviking/ov.conf`
//!
//! The relevant section (JSON) looks like:
//! ```json
//! {
//!   "git": {
//!     "enabled": true,
//!     "backend": "s3",
//!     "s3": {
//!       "bucket": "<your-tos-bucket>",
//!       "region": "cn-beijing",
//!       "endpoint": "https://tos-s3-cn-beijing.volces.com",
//!       "access_key": "<ak>",
//!       "secret_key": "<sk>",
//!       "prefix": ".ovgit",
//!       "use_path_style": false,
//!       "cas_mode": "native"
//!     }
//!   }
//! }
//! ```
//!
//! When no usable config is found, each test prints a notice and returns
//! successfully (treated as skipped) so default `cargo test` runs stay green.
//!
//! Run against TOS:
//! ```bash
//! OV_GIT_S3_CONF=/path/to/ov.conf \
//!   cargo test -p ragfs --features s3 --test git_s3_integration -- --nocapture
//! ```
//! Tests namespace every key under `{prefix}/_it/{uuid}` and use a random
//! account per test, so concurrent runs never collide and never touch real data.

#![cfg(feature = "s3")]

use std::path::PathBuf;
use std::sync::Arc;

use bytes::Bytes;
use gix_hash::ObjectId;

use ragfs::core::filesystem::FileSystem;
use ragfs::git::backends::local::LocalObjectStore;
use ragfs::git::backends::s3::{CasMode, S3Config, S3ObjectStore, S3RefStore};
use ragfs::git::error::RefStoreError;
use ragfs::git::object_store::ObjectStore;
use ragfs::git::ref_store::RefStore;
use ragfs::git::service::GitService;
use ragfs::git::types::{
    CommitRequest, CommitResponse, RestoreRequest, RestoreResponse, ShowRequest, ShowResponse,
};
use ragfs::git::util::zlib_compress;
use ragfs::plugins::localfs::LocalFileSystem;

/// Resolve the `ov.conf` path using the same chain documented above.
fn resolve_conf_path() -> Option<PathBuf> {
    let candidates = [
        std::env::var("OV_GIT_S3_CONF").ok(),
        std::env::var("OPENVIKING_CONFIG_FILE").ok(),
        std::env::var("HOME")
            .ok()
            .map(|h| format!("{h}/.openviking/ov.conf")),
        Some("/etc/openviking/ov.conf".to_string()),
    ];
    candidates
        .into_iter()
        .flatten()
        .map(PathBuf::from)
        .find(|p| p.exists())
}

/// Load and build an [`S3Config`] from the resolved `ov.conf`, namespacing the
/// `prefix` under a unique `_it/{uuid}` segment for test isolation.
///
/// Returns `None` (treated as "skip") when no config file exists, when git is
/// not enabled, when the backend is not `s3`, or when the `[git.s3]` section is
/// missing required fields (`bucket`/`region`).
fn load_s3_config() -> Option<S3Config> {
    let path = resolve_conf_path()?;
    let raw = std::fs::read_to_string(&path).ok()?;
    let root: serde_json::Value = serde_json::from_str(&raw).ok()?;

    // `git` may live at the top level (OpenVikingConfig.git) or, defensively,
    // under `storage.git`.
    let git = root
        .get("git")
        .or_else(|| root.get("storage").and_then(|s| s.get("git")))?;

    if !git.get("enabled").and_then(|v| v.as_bool()).unwrap_or(false) {
        return None;
    }
    if git.get("backend").and_then(|v| v.as_str()) != Some("s3") {
        return None;
    }

    let s3 = git.get("s3")?;
    let bucket = s3.get("bucket").and_then(|v| v.as_str())?.to_string();
    let region = s3.get("region").and_then(|v| v.as_str())?.to_string();
    if bucket.is_empty() || region.is_empty() {
        return None;
    }

    let str_opt = |key: &str| {
        s3.get(key)
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
    };

    let base_prefix = str_opt("prefix").unwrap_or_else(|| ".ovgit".to_string());
    let prefix = format!(
        "{}/_it/{}",
        base_prefix.trim_end_matches('/'),
        uuid::Uuid::new_v4().simple()
    );

    let endpoint = str_opt("endpoint");
    let access_key_id = str_opt("access_key");
    let secret_access_key = str_opt("secret_key");
    let use_path_style = s3
        .get("use_path_style")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    let cas_mode = match s3.get("cas_mode").and_then(|v| v.as_str()) {
        Some("redis_lock") => CasMode::RedisLock,
        _ => CasMode::Native,
    };

    Some(S3Config {
        bucket,
        prefix,
        region,
        endpoint,
        access_key_id,
        secret_access_key,
        use_path_style,
        cas_mode,
    })
}

/// Build a random account id so concurrent tests never share namespaces.
fn random_account() -> String {
    format!("acct-{}", uuid::Uuid::new_v4().simple())
}

/// Build a valid zlib-compressed loose object body together with its object id.
fn loose_object(data: &[u8]) -> (ObjectId, Bytes) {
    let kind = gix_object::Kind::Blob;
    let header = gix_object::encode::loose_header(kind, data.len() as u64);
    let oid = gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data);
    let mut full = Vec::with_capacity(header.len() + data.len());
    full.extend_from_slice(&header);
    full.extend_from_slice(data);
    let compressed = zlib_compress(&full).expect("zlib compress");
    (oid, Bytes::from(compressed))
}

/// Compute a deterministic [`ObjectId`] from arbitrary bytes (used as ref values).
fn oid_of(data: &[u8]) -> ObjectId {
    gix_object::compute_hash(gix_hash::Kind::Sha1, gix_object::Kind::Blob, data)
}

/// Print a skip notice once and return `true` when no S3 config is available.
macro_rules! cfg_or_skip {
    ($test:expr) => {{
        match load_s3_config() {
            Some(cfg) => cfg,
            None => {
                eprintln!(
                    "[skip] {}: no usable [git.s3] config (set OV_GIT_S3_CONF to an ov.conf \
                     with git.enabled=true, backend=\"s3\")",
                    $test
                );
                return;
            }
        }
    }};
}

/// §A2.1 — ObjectStore round-trip + idempotency + not-found behavior.
#[tokio::test]
async fn s3_object_store_round_trip() {
    let cfg = cfg_or_skip!("s3_object_store_round_trip");
    let account = random_account();
    let store = S3ObjectStore::from_config(cfg)
        .await
        .expect("build S3ObjectStore");

    let (oid, body) = loose_object(b"hello viking s3 round-trip");

    // First write succeeds.
    store.put(&account, &oid, body.clone()).await.expect("put #1");
    // Second write of identical content is idempotent (must not error).
    store.put(&account, &oid, body.clone()).await.expect("put #2 idempotent");

    // get returns the exact zlib bytes we stored.
    let fetched = store.get(&account, &oid).await.expect("get");
    assert_eq!(fetched, body, "stored and fetched bytes must match exactly");

    // exists is true for a written object.
    assert!(store.exists(&account, &oid).await.expect("exists"));

    // A never-written oid: get -> NotFound, exists -> false.
    let (missing_oid, _) = loose_object(b"this object was never written to s3");
    match store.get(&account, &missing_oid).await {
        Err(ragfs::git::error::ObjectStoreError::NotFound(o)) => assert_eq!(o, missing_oid),
        other => panic!("expected NotFound, got {other:?}"),
    }
    assert!(!store.exists(&account, &missing_oid).await.expect("exists missing"));
}

/// §A2.2 — RefStore CAS create: first create wins, second create conflicts.
#[tokio::test]
async fn s3_ref_store_cas_create() {
    let cfg = cfg_or_skip!("s3_ref_store_cas_create");
    let account = random_account();
    let store = S3RefStore::from_config(cfg)
        .await
        .expect("build S3RefStore");

    let ref_name = "refs/heads/main";
    let a = oid_of(b"commit-A");
    let b = oid_of(b"commit-B");

    // create-if-absent succeeds.
    store
        .cas_update(&account, ref_name, None, a)
        .await
        .expect("first create");
    assert_eq!(store.read(&account, ref_name).await.expect("read"), a);

    // A second create-if-absent must conflict (ref already exists).
    match store.cas_update(&account, ref_name, None, b).await {
        Err(RefStoreError::Conflict { expected, actual }) => {
            assert_eq!(expected, None);
            assert_eq!(actual, Some(a));
        }
        other => panic!("expected Conflict, got {other:?}"),
    }
    // Value must be unchanged.
    assert_eq!(store.read(&account, ref_name).await.expect("read after conflict"), a);
}

/// §A2.3 — RefStore CAS advance: expected==current moves the ref forward.
#[tokio::test]
async fn s3_ref_store_cas_advance() {
    let cfg = cfg_or_skip!("s3_ref_store_cas_advance");
    let account = random_account();
    let store = S3RefStore::from_config(cfg)
        .await
        .expect("build S3RefStore");

    let ref_name = "refs/heads/main";
    let a = oid_of(b"commit-A");
    let b = oid_of(b"commit-B");

    store.cas_update(&account, ref_name, None, a).await.expect("create A");
    store
        .cas_update(&account, ref_name, Some(a), b)
        .await
        .expect("advance A -> B");
    assert_eq!(store.read(&account, ref_name).await.expect("read"), b);
}

/// §A2.4 — RefStore CAS conflict: a stale `expected` is rejected with the
/// actual current value reported.
#[tokio::test]
async fn s3_ref_store_cas_conflict() {
    let cfg = cfg_or_skip!("s3_ref_store_cas_conflict");
    let account = random_account();
    let store = S3RefStore::from_config(cfg)
        .await
        .expect("build S3RefStore");

    let ref_name = "refs/heads/main";
    let a = oid_of(b"commit-A");
    let stale = oid_of(b"commit-STALE");
    let c = oid_of(b"commit-C");

    store.cas_update(&account, ref_name, None, a).await.expect("create A");

    match store.cas_update(&account, ref_name, Some(stale), c).await {
        Err(RefStoreError::Conflict { expected, actual }) => {
            assert_eq!(expected, Some(stale));
            assert_eq!(actual, Some(a));
        }
        other => panic!("expected Conflict, got {other:?}"),
    }
    assert_eq!(store.read(&account, ref_name).await.expect("read"), a);
}

/// §A2.5 — RefStore list: all refs under a prefix are returned (exercises the
/// `list_objects_v2` pagination path).
#[tokio::test]
async fn s3_ref_store_list() {
    let cfg = cfg_or_skip!("s3_ref_store_list");
    let account = random_account();
    let store = S3RefStore::from_config(cfg)
        .await
        .expect("build S3RefStore");

    let entries = [
        ("refs/heads/main", oid_of(b"main")),
        ("refs/heads/dev", oid_of(b"dev")),
        ("refs/heads/release", oid_of(b"release")),
    ];
    for (name, oid) in &entries {
        store.cas_update(&account, name, None, *oid).await.expect("create ref");
    }

    let mut listed = store.list(&account, "refs/heads").await.expect("list");
    listed.sort();

    let mut expected: Vec<(String, ObjectId)> =
        entries.iter().map(|(n, o)| (n.to_string(), *o)).collect();
    expected.sort();

    assert_eq!(listed, expected);
}

/// §A2.6 — Backend equivalence: the same object is byte-identical and equally
/// visible whether stored via the local or the S3 backend.
#[tokio::test]
async fn backend_equivalence_local_vs_s3() {
    let cfg = cfg_or_skip!("backend_equivalence_local_vs_s3");
    let account = random_account();

    let s3 = S3ObjectStore::from_config(cfg)
        .await
        .expect("build S3ObjectStore");
    let tmp = tempfile::tempdir().expect("tempdir");
    let local = LocalObjectStore::new(tmp.path());

    let (oid, body) = loose_object(b"backend equivalence payload \x00\x01\x02 binary-ish");

    let s3: Arc<dyn ObjectStore> = Arc::new(s3);
    let local: Arc<dyn ObjectStore> = Arc::new(local);

    for store in [&s3, &local] {
        store.put(&account, &oid, body.clone()).await.expect("put");
        assert!(store.exists(&account, &oid).await.expect("exists"));
    }

    let from_s3 = s3.get(&account, &oid).await.expect("s3 get");
    let from_local = local.get(&account, &oid).await.expect("local get");

    assert_eq!(from_s3, body, "s3 bytes must match input");
    assert_eq!(from_local, body, "local bytes must match input");
    assert_eq!(from_s3, from_local, "s3 and local stored bytes must be identical");
}

// ─────────────────────────────────────────────────────────────────────────
// §A3 — End-to-end GitService over the S3/TOS backend.
//
// These mirror the local-backend service tests in `src/git/service.rs`, but
// wire `GitService` to `S3ObjectStore` + `S3RefStore` (loaded from `ov.conf`)
// and a real `LocalFileSystem` working tree rooted at a temp dir. The working
// tree layout matches production: each account lives under `/local/{account}`
// relative to the mount base.
// ─────────────────────────────────────────────────────────────────────────

/// Build a `GitService` backed by S3 stores plus a fresh `LocalFileSystem`
/// working tree. Returns the service, the kept-alive temp dir, and the
/// absolute account root path (`{tmp}/local/{account}`) for direct file IO.
fn make_s3_service(
    cfg: S3Config,
    account: &str,
) -> impl std::future::Future<Output = (GitService, tempfile::TempDir, PathBuf)> + '_ {
    async move {
        let object_store: Arc<dyn ObjectStore> = Arc::new(
            S3ObjectStore::from_config(cfg.clone())
                .await
                .expect("build S3ObjectStore"),
        );
        let ref_store: Arc<dyn RefStore> = Arc::new(
            S3RefStore::from_config(cfg)
                .await
                .expect("build S3RefStore"),
        );

        let work_dir = tempfile::tempdir().expect("tempdir");
        let acct_root = work_dir.path().join("local").join(account);
        std::fs::create_dir_all(&acct_root).expect("create account root");
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).expect("localfs"));

        let svc = GitService::new(vfs, object_store, ref_store);
        (svc, work_dir, acct_root)
    }
}

/// Build a `CommitRequest` with fixed test author info.
fn commit_req(account: &str, branch: &str, message: &str, paths: Option<Vec<String>>) -> CommitRequest {
    CommitRequest {
        account: account.to_string(),
        branch: branch.to_string(),
        message: message.to_string(),
        paths,
        author_name: "tester".to_string(),
        author_email: "tester@example.com".to_string(),
    }
}

/// §A3.1 — commit → show(path) returns the exact blob bytes that were written.
#[tokio::test]
async fn s3_e2e_commit_then_show_blob_round_trip() {
    let cfg = cfg_or_skip!("s3_e2e_commit_then_show_blob_round_trip");
    let account = random_account();
    let (svc, _work_dir, acct_root) = make_s3_service(cfg, &account).await;

    // Binary-ish payload to guard against any string-vs-bytes regression.
    let body: &[u8] = b"hello viking s3 e2e \x00\x01\x02\nline2\n";
    std::fs::create_dir_all(acct_root.join("resources")).expect("mkdir resources");
    std::fs::write(acct_root.join("resources/a.md"), body).expect("write a.md");

    match svc
        .commit(commit_req(&account, "main", "first", None))
        .await
        .expect("commit")
    {
        CommitResponse::Created { .. } => {}
        other => panic!("expected Created, got {other:?}"),
    }

    let resp = svc
        .show(ShowRequest {
            account: account.clone(),
            target_ref: "main".into(),
            path: Some("resources/a.md".into()),
        })
        .await
        .expect("show");

    match resp {
        ShowResponse::Blob { bytes, size, .. } => {
            assert_eq!(bytes.as_ref(), body, "blob bytes must match written content");
            assert_eq!(size, body.len() as u64);
        }
        other => panic!("expected Blob, got {other:?}"),
    }
}

/// §A3.2 — restore to an older commit rolls back the working tree and creates
/// a *new* commit whose parent is the current HEAD (forward-only history).
#[tokio::test]
async fn s3_e2e_restore_rolls_back_and_advances_head() {
    let cfg = cfg_or_skip!("s3_e2e_restore_rolls_back_and_advances_head");
    let account = random_account();
    let (svc, _work_dir, acct_root) = make_s3_service(cfg, &account).await;

    let proj = acct_root.join("resources/proj_a");
    std::fs::create_dir_all(&proj).expect("mkdir proj_a");

    // Source commit: a.md=v1, b.md=v1.
    std::fs::write(proj.join("a.md"), b"A v1").unwrap();
    std::fs::write(proj.join("b.md"), b"B v1").unwrap();
    let source_oid = match svc
        .commit(commit_req(&account, "main", "source", None))
        .await
        .expect("commit source")
    {
        CommitResponse::Created { commit_oid, .. } => commit_oid,
        other => panic!("expected Created, got {other:?}"),
    };

    // HEAD commit: rewrite a.md, delete b.md, add c.md.
    std::fs::write(proj.join("a.md"), b"A v2").unwrap();
    std::fs::remove_file(proj.join("b.md")).unwrap();
    std::fs::write(proj.join("c.md"), b"C new").unwrap();
    let head_oid = match svc
        .commit(commit_req(
            &account,
            "main",
            "head",
            Some(vec![
                "resources/proj_a/a.md".to_string(),
                "resources/proj_a/b.md".to_string(),
                "resources/proj_a/c.md".to_string(),
            ]),
        ))
        .await
        .expect("commit head")
    {
        CommitResponse::Created { commit_oid, .. } => commit_oid,
        other => panic!("expected Created, got {other:?}"),
    };

    // Restore the project subtree back to the source commit.
    let resp = svc
        .restore(RestoreRequest {
            account: account.clone(),
            branch: "main".into(),
            project_dir: Some("resources/proj_a".into()),
            source_commit: source_oid.to_hex().to_string(),
            dry_run: false,
            message: Some("rewind proj_a".into()),
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        })
        .await
        .expect("restore");

    match resp {
        RestoreResponse::Applied {
            source_commit,
            parent_commit,
            written,
            deleted,
            ..
        } => {
            assert_eq!(source_commit, source_oid);
            assert_eq!(parent_commit, head_oid, "new commit's parent MUST be HEAD, not source");
            assert_eq!(written, 2, "a.md rewrite + b.md recreate");
            assert_eq!(deleted, 1, "c.md removed");
        }
        other => panic!("expected Applied, got {other:?}"),
    }

    // Working tree rolled back to the source snapshot.
    assert_eq!(std::fs::read(proj.join("a.md")).unwrap(), b"A v1", "a.md rolled back");
    assert_eq!(std::fs::read(proj.join("b.md")).unwrap(), b"B v1", "b.md restored");
    assert!(!proj.join("c.md").exists(), "c.md must be deleted");

    // HEAD advanced to a brand-new commit (forward-only), parented on the old HEAD.
    let new_head = svc
        .show(ShowRequest {
            account: account.clone(),
            target_ref: "main".into(),
            path: None,
        })
        .await
        .expect("show head");
    match new_head {
        ShowResponse::Commit { oid, parents, .. } => {
            assert_ne!(oid, head_oid, "restore must create a new commit");
            assert_ne!(oid, source_oid, "HEAD is a new commit, not the source");
            assert_eq!(parents, vec![head_oid], "new commit parent must be prior HEAD");
        }
        other => panic!("expected Commit, got {other:?}"),
    }
}

/// §A3.3 — idempotency: committing again with no working-tree change is a Noop
/// and leaves the branch ref pointing at the same commit.
#[tokio::test]
async fn s3_e2e_commit_noop_when_unchanged() {
    let cfg = cfg_or_skip!("s3_e2e_commit_noop_when_unchanged");
    let account = random_account();
    let (svc, _work_dir, acct_root) = make_s3_service(cfg, &account).await;

    std::fs::create_dir_all(acct_root.join("resources")).unwrap();
    std::fs::write(acct_root.join("resources/a.md"), b"stable content").unwrap();

    let first_oid = match svc
        .commit(commit_req(&account, "main", "first", None))
        .await
        .expect("commit first")
    {
        CommitResponse::Created { commit_oid, .. } => commit_oid,
        other => panic!("expected Created, got {other:?}"),
    };

    // Second commit with identical tree state — must be a Noop pointing at the
    // existing HEAD.
    match svc
        .commit(commit_req(&account, "main", "second", None))
        .await
        .expect("commit second")
    {
        CommitResponse::Noop { commit_oid } => {
            assert_eq!(commit_oid, first_oid, "Noop must report the unchanged HEAD oid");
        }
        other => panic!("expected Noop, got {other:?}"),
    }
}
