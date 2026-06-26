//! PyO3 binding helpers for the Git version-control service.
//!
//! This module owns:
//! - Backend construction (`build_git_service`) from a `GitConfig`
//! - Request parsers: `parse_commit_request`, `parse_restore_request`, `parse_show_request`
//!   (added in later tasks)
//! - Response converters: `commit_response_to_pydict`, `restore_response_to_pydict`,
//!   `show_response_to_pydict` (added in later tasks)
//! - Error mapping `map_git_error` (added in later tasks)
//!
//! The free functions are invoked from thin `#[pymethods]` wrappers in `lib.rs`.

use std::sync::Arc;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use ragfs::core::FileSystem;
use ragfs::git::{
    GitConfig, GitService, IndexStore, LocalIndexStore, LocalObjectStore, LocalRefStore,
    ObjectStore, RefStore,
};

#[cfg(feature = "s3")]
use ragfs::git::{CasMode, S3Config, S3IndexStore, S3ObjectStore, S3RefStore};

/// Build a `GitService` from a `GitConfig` and the binding's MountableFS.
///
/// Returns `Ok(None)` when `enabled = false`; `Err(PyErr)` if the config is
/// invalid (missing required section, unknown backend, etc.).
///
/// Backend-specific notes:
/// - `local`: requires `[git.local]` with `base_dir`. Builds `LocalObjectStore`
///   and `LocalRefStore`, both rooted at `base_dir`.
/// - `s3` (feature-gated): requires `[git.s3]` with `bucket`, `region`.
///   `access_key` and `secret_key` are read directly from the config; when
///   omitted, the AWS SDK default credentials chain is used.
pub fn build_git_service(
    cfg: &GitConfig,
    vfs: Arc<dyn FileSystem>,
) -> PyResult<Option<Arc<GitService>>> {
    if !cfg.enabled {
        return Ok(None);
    }

    let (object_store, ref_store, index_store): (
        Arc<dyn ObjectStore>,
        Arc<dyn RefStore>,
        Option<Arc<dyn IndexStore>>,
    ) = match cfg.backend.as_str() {
        "local" => {
            let lc = cfg
                .local
                .as_ref()
                .ok_or_else(|| PyValueError::new_err("[git.local] missing"))?;
            let os = Arc::new(LocalObjectStore::new(lc.base_dir.clone()));
            let rs = Arc::new(LocalRefStore::new(lc.base_dir.clone()));
            let is: Option<Arc<dyn IndexStore>> = if cfg.tuning.commit_index_enabled {
                Some(Arc::new(LocalIndexStore::new(lc.base_dir.clone())))
            } else {
                None
            };
            (os, rs, is)
        }
        #[cfg(feature = "s3")]
        "s3" => build_s3_service(cfg)?,
        #[cfg(not(feature = "s3"))]
        "s3" => {
            return Err(PyRuntimeError::new_err(
                "git backend 's3' requested but ragfs-python built without `s3` feature",
            ));
        }
        other => {
            return Err(PyValueError::new_err(format!(
                "unsupported git backend: {}",
                other
            )));
        }
    };

    Ok(Some(Arc::new(
        GitService::with_index(vfs, object_store, ref_store, index_store)
            .with_blob_exists_precheck(cfg.tuning.blob_exists_precheck_enabled),
    )))
}

#[cfg(feature = "s3")]
fn build_s3_service(
    cfg: &GitConfig,
) -> PyResult<(
    Arc<dyn ObjectStore>,
    Arc<dyn RefStore>,
    Option<Arc<dyn IndexStore>>,
)> {
    let sc = cfg
        .s3
        .as_ref()
        .ok_or_else(|| PyValueError::new_err("[git.s3] missing"))?;

    let access_key_id = match sc.access_key.as_deref() {
        Some(v) if !v.is_empty() => Some(v.to_string()),
        _ => None,
    };
    let secret_access_key = match sc.secret_key.as_deref() {
        Some(v) if !v.is_empty() => Some(v.to_string()),
        _ => None,
    };

    let cas_mode = match sc.cas_mode.as_str() {
        "native" => CasMode::Native,
        "redis_lock" => CasMode::RedisLock,
        other => {
            return Err(PyValueError::new_err(format!(
                "unsupported cas_mode: {}",
                other
            )));
        }
    };

    let s3_config = S3Config {
        bucket: sc.bucket.clone(),
        prefix: sc.prefix.clone(),
        region: sc.region.clone(),
        endpoint: if sc.endpoint.is_empty() {
            None
        } else {
            Some(sc.endpoint.clone())
        },
        access_key_id,
        secret_access_key,
        use_path_style: sc.use_path_style,
        cas_mode,
    };

    let rt = tokio::runtime::Handle::try_current()
        .map_err(|_| PyRuntimeError::new_err("build_s3_service must run inside a tokio runtime"))?;
    let os_cfg = s3_config.clone();
    let object_store = Arc::new(
        rt.block_on(async move { S3ObjectStore::from_config(os_cfg).await })
            .map_err(|e| PyRuntimeError::new_err(format!("S3ObjectStore: {}", e)))?,
    ) as Arc<dyn ObjectStore>;

    let rs_cfg = s3_config.clone();
    let ref_store = Arc::new(
        rt.block_on(async move { S3RefStore::from_config(rs_cfg).await })
            .map_err(|e| PyRuntimeError::new_err(format!("S3RefStore: {}", e)))?,
    ) as Arc<dyn RefStore>;

    let index_store: Option<Arc<dyn IndexStore>> = if cfg.tuning.commit_index_enabled {
        let is_cfg = s3_config;
        Some(Arc::new(
            rt.block_on(async move { S3IndexStore::from_config(is_cfg).await })
                .map_err(|e| PyRuntimeError::new_err(format!("S3IndexStore: {}", e)))?,
        ) as Arc<dyn IndexStore>)
    } else {
        None
    };

    Ok((object_store, ref_store, index_store))
}

/// Map a `GitError` to the appropriate Python exception.
///
/// Loads exception classes from the `openviking.pyagfs` module. When the
/// module is not importable (e.g. during unit tests), falls back to
/// `PyRuntimeError` with the same message.
pub fn map_git_error(py: Python<'_>, e: ragfs::git::GitError) -> PyErr {
    use ragfs::git::{GitError, ObjectStoreError, RefStoreError};
    let msg = e.to_string();
    match e {
        GitError::FeatureDisabled => new_py_err_pub(py, "AGFSNotSupportedError", msg),
        GitError::ConcurrentCommit { .. } => new_py_err_pub(py, "GitConcurrentCommitError", msg),
        GitError::PathNotFound(_) => new_py_err_pub(py, "AGFSNotFoundError", msg),
        GitError::PathIsDirectory(_) => new_py_err_pub(py, "AGFSInvalidOperationError", msg),
        GitError::SubtreeNotFoundInCommit { .. } => new_py_err_pub(py, "AGFSNotFoundError", msg),
        GitError::InvalidAccountId(_) => new_py_err_pub(py, "AGFSInvalidPathError", msg),
        GitError::InvalidProjectDir(_) => new_py_err_pub(py, "AGFSInvalidPathError", msg),
        GitError::InvalidPath(_) => new_py_err_pub(py, "AGFSInvalidPathError", msg),
        GitError::BlobTooLarge { .. } => new_py_err_pub(py, "AGFSInvalidOperationError", msg),
        GitError::TooManyFiles { .. } => new_py_err_pub(py, "AGFSInvalidOperationError", msg),
        GitError::CorruptedObject(_) => new_py_err_pub(py, "AGFSInternalError", msg),
        GitError::RefStore(RefStoreError::NotFound(_)) => {
            new_py_err_pub(py, "AGFSNotFoundError", msg)
        }
        GitError::RefStore(RefStoreError::Conflict { .. }) => {
            new_py_err_pub(py, "GitConcurrentCommitError", msg)
        }
        GitError::OidPrefixNotFound { .. } => new_py_err_pub(py, "AGFSNotFoundError", msg),
        GitError::AmbiguousOid { .. } => new_py_err_pub(py, "AGFSInvalidOperationError", msg),
        GitError::ObjectStore(ObjectStoreError::NotFound(_)) => {
            new_py_err_pub(py, "AGFSNotFoundError", msg)
        }
        GitError::RestoreWritebackPartial(p) => writeback_partial_to_pyerr(py, *p, msg),
        GitError::ObjectStore(_)
        | GitError::RefStore(_)
        | GitError::Vfs(_)
        | GitError::Other(_) => PyRuntimeError::new_err(msg),
    }
}

/// Build a Python exception carrying the structured `RestoreWritebackPartial`
/// payload. Falls back to `PyRuntimeError` when `openviking.pyagfs` is not
/// importable (e.g. cargo-test environment) — in that case the structured
/// data is lost, but the error message still survives.
fn writeback_partial_to_pyerr(py: Python<'_>, p: RestoreWritebackPartial, msg: String) -> PyErr {
    let exc_class = match PyModule::import(py, "openviking.pyagfs")
        .and_then(|m| m.getattr("GitRestoreWritebackPartialError"))
    {
        Ok(c) => c,
        Err(_) => {
            return PyRuntimeError::new_err(format!(
                "{msg} (structured payload dropped: pyagfs unavailable)"
            ));
        }
    };

    let payload = PyDict::new(py);
    // Strings + ints marshal trivially; (String, String) tuples become
    // Python tuples via pyo3's IntoPy impl.
    let set_oid =
        |k: &str, oid: &gix_hash::ObjectId| -> PyResult<()> { payload.set_item(k, oid_hex(oid)) };

    let build = || -> PyResult<()> {
        set_oid("new_commit_oid", &p.new_commit_oid)?;
        set_oid("source_commit", &p.source_commit)?;
        set_oid("parent_commit", &p.parent_commit)?;
        payload.set_item("written", p.written)?;
        payload.set_item("deleted", p.deleted)?;
        payload.set_item("unchanged", p.unchanged)?;
        payload.set_item("written_paths", p.written_paths.clone())?;
        payload.set_item("deleted_paths", p.deleted_paths.clone())?;
        payload.set_item("failed_writes", p.failed_writes.clone())?;
        payload.set_item("failed_deletes", p.failed_deletes.clone())?;
        Ok(())
    };
    if let Err(e) = build() {
        return e;
    }

    match exc_class.call1((msg.clone(), &payload)) {
        Ok(instance) => PyErr::from_value(instance),
        Err(_) => PyRuntimeError::new_err(format!(
            "{msg} (structured payload dropped: failed to instantiate \
             GitRestoreWritebackPartialError)"
        )),
    }
}

/// Local copy of the new_py_err pattern used in lib.rs. We duplicate it here
/// to keep git.rs self-contained — lib.rs's helper is private. If lib.rs's
/// helper is later made `pub(crate)`, this can be deleted in favor of that.
pub fn new_py_err_pub(py: Python<'_>, name: &str, msg: String) -> PyErr {
    let exc = PyModule::import(py, "openviking.pyagfs")
        .and_then(|m| m.getattr(name))
        .and_then(|exc| Ok(exc.cast_into::<pyo3::types::PyType>()?));
    match exc {
        Ok(exc) => PyErr::from_type(exc, msg),
        Err(_) => PyRuntimeError::new_err(msg),
    }
}

use pyo3::types::{PyBytes, PyDict, PyList};
use ragfs::git::{
    Actor, CommitRequest, CommitResponse, RestoreDiff, RestoreRequest, RestoreResponse,
    RestoreWritebackPartial, ShowRequest, ShowResponse,
};

// ---------- request parsers ----------

fn require_str(kwargs: &Bound<PyDict>, key: &str) -> PyResult<String> {
    let val = kwargs
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing required kwarg: {}", key)))?;
    val.extract::<String>()
        .map_err(|_| PyValueError::new_err(format!("kwarg {} must be a string", key)))
}

fn optional_str(kwargs: &Bound<PyDict>, key: &str) -> PyResult<Option<String>> {
    match kwargs.get_item(key)? {
        Some(v) if !v.is_none() => v
            .extract::<String>()
            .map(Some)
            .map_err(|_| PyValueError::new_err(format!("kwarg {} must be a string", key))),
        _ => Ok(None),
    }
}

fn optional_bool(kwargs: &Bound<PyDict>, key: &str, default: bool) -> PyResult<bool> {
    match kwargs.get_item(key)? {
        Some(v) if !v.is_none() => v
            .extract::<bool>()
            .map_err(|_| PyValueError::new_err(format!("kwarg {} must be a bool", key))),
        _ => Ok(default),
    }
}

fn optional_string_list(kwargs: &Bound<PyDict>, key: &str) -> PyResult<Option<Vec<String>>> {
    match kwargs.get_item(key)? {
        Some(v) if !v.is_none() => v
            .extract::<Vec<String>>()
            .map(Some)
            .map_err(|_| PyValueError::new_err(format!("kwarg {} must be a list of strings", key))),
        _ => Ok(None),
    }
}

pub fn parse_commit_request(kwargs: &Bound<PyDict>) -> PyResult<CommitRequest> {
    Ok(CommitRequest {
        account: require_str(kwargs, "account")?,
        branch: require_str(kwargs, "branch")?,
        message: require_str(kwargs, "message")?,
        paths: optional_string_list(kwargs, "paths")?,
        author_name: require_str(kwargs, "author_name")?,
        author_email: require_str(kwargs, "author_email")?,
    })
}

pub fn parse_restore_request(kwargs: &Bound<PyDict>) -> PyResult<RestoreRequest> {
    Ok(RestoreRequest {
        account: require_str(kwargs, "account")?,
        branch: require_str(kwargs, "branch")?,
        project_dir: optional_str(kwargs, "project_dir")?,
        source_commit: require_str(kwargs, "source_commit")?,
        dry_run: optional_bool(kwargs, "dry_run", false)?,
        message: optional_str(kwargs, "message")?,
        author_name: require_str(kwargs, "author_name")?,
        author_email: require_str(kwargs, "author_email")?,
    })
}

pub fn parse_show_request(kwargs: &Bound<PyDict>) -> PyResult<ShowRequest> {
    Ok(ShowRequest {
        account: require_str(kwargs, "account")?,
        target_ref: require_str(kwargs, "target_ref")?,
        path: optional_str(kwargs, "path")?,
    })
}

// ---------- response converters ----------

fn oid_hex(oid: &gix_hash::ObjectId) -> String {
    oid.to_hex().to_string()
}

fn actor_to_dict(py: Python<'_>, a: &Actor) -> PyResult<Py<PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", &a.name)?;
    d.set_item("email", &a.email)?;
    d.set_item("time_seconds", a.time_seconds)?;
    d.set_item("tz_offset_seconds", a.tz_offset_seconds)?;
    Ok(d.into())
}

pub fn commit_response_to_pydict(py: Python<'_>, resp: CommitResponse) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    match resp {
        CommitResponse::Created {
            commit_oid,
            changed,
        } => {
            d.set_item("result", "created")?;
            d.set_item("commit_oid", oid_hex(&commit_oid))?;
            d.set_item("changed", changed)?;
        }
        CommitResponse::Noop { commit_oid } => {
            d.set_item("result", "noop")?;
            d.set_item("commit_oid", oid_hex(&commit_oid))?;
        }
    }
    Ok(d.into_any().unbind())
}

fn diff_to_dict(py: Python<'_>, diff: &RestoreDiff) -> PyResult<Py<PyDict>> {
    let d = PyDict::new(py);
    let to_write = PyList::empty(py);
    for (path, oid) in &diff.to_write {
        let pair = PyDict::new(py);
        pair.set_item("path", path)?;
        pair.set_item("oid", oid_hex(oid))?;
        to_write.append(pair)?;
    }
    d.set_item("to_write", to_write)?;
    d.set_item("to_delete", diff.to_delete.clone())?;
    d.set_item("unchanged", diff.unchanged.clone())?;
    Ok(d.into())
}

pub fn restore_response_to_pydict(py: Python<'_>, resp: RestoreResponse) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    match resp {
        RestoreResponse::Applied {
            new_commit_oid,
            source_commit,
            parent_commit,
            written,
            deleted,
            unchanged,
            written_paths,
            deleted_paths,
        } => {
            d.set_item("result", "applied")?;
            d.set_item("new_commit_oid", oid_hex(&new_commit_oid))?;
            d.set_item("source_commit", oid_hex(&source_commit))?;
            d.set_item("parent_commit", oid_hex(&parent_commit))?;
            d.set_item("written", written)?;
            d.set_item("deleted", deleted)?;
            d.set_item("unchanged", unchanged)?;
            d.set_item("written_paths", written_paths)?;
            d.set_item("deleted_paths", deleted_paths)?;
        }
        RestoreResponse::Noop { head, source } => {
            d.set_item("result", "noop")?;
            d.set_item("head", oid_hex(&head))?;
            d.set_item("source", oid_hex(&source))?;
        }
        RestoreResponse::DryRun { diff, head, source } => {
            d.set_item("result", "dry_run")?;
            d.set_item("head", oid_hex(&head))?;
            d.set_item("source", oid_hex(&source))?;
            d.set_item("diff", diff_to_dict(py, &diff)?)?;
        }
    }
    Ok(d.into_any().unbind())
}

pub fn show_response_to_pydict(py: Python<'_>, resp: ShowResponse) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    match resp {
        ShowResponse::Commit {
            oid,
            tree,
            parents,
            author,
            committer,
            message,
        } => {
            d.set_item("oid", oid_hex(&oid))?;
            d.set_item("tree", oid_hex(&tree))?;
            let plist = PyList::empty(py);
            for p in &parents {
                plist.append(oid_hex(p))?;
            }
            d.set_item("parents", plist)?;
            d.set_item("author", actor_to_dict(py, &author)?)?;
            d.set_item("committer", actor_to_dict(py, &committer)?)?;
            d.set_item("message", message)?;
        }
        ShowResponse::Blob { oid, size, bytes } => {
            d.set_item("oid", oid_hex(&oid))?;
            d.set_item("size", size)?;
            d.set_item("bytes", PyBytes::new(py, &bytes))?;
        }
    }
    Ok(d.into_any().unbind())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ragfs::core::MountableFS;
    use ragfs::git::GitError;
    use std::sync::Arc;

    fn local_cfg(base_dir: &str) -> ragfs::git::GitConfig {
        ragfs::git::GitConfig {
            enabled: true,
            backend: "local".into(),
            default_branch: "main".into(),
            author_name: "test".into(),
            author_email: "t@e".into(),
            local: Some(ragfs::git::GitLocalConfig {
                base_dir: base_dir.into(),
            }),
            s3: None,
            tuning: Default::default(),
        }
    }

    #[tokio::test]
    async fn build_git_service_disabled_returns_none() {
        let fs = Arc::new(MountableFS::new()) as Arc<dyn ragfs::core::FileSystem>;
        let mut cfg = local_cfg("/tmp/ov-git-test-disabled");
        cfg.enabled = false;
        let svc = build_git_service(&cfg, fs).expect("build ok");
        assert!(svc.is_none());
    }

    #[tokio::test]
    async fn build_git_service_local_returns_some() {
        let fs = Arc::new(MountableFS::new()) as Arc<dyn ragfs::core::FileSystem>;
        let cfg = local_cfg("/tmp/ov-git-test-local");
        let svc = build_git_service(&cfg, fs).expect("build ok");
        assert!(svc.is_some());
    }

    #[tokio::test]
    async fn build_git_service_unknown_backend_errors() {
        // Building a PyErr requires the Python interpreter to be initialized;
        // the `extension-module` feature disables auto-initialize.
        Python::initialize();
        let fs = Arc::new(MountableFS::new()) as Arc<dyn ragfs::core::FileSystem>;
        let mut cfg = local_cfg("/tmp/ov-git-test-bad");
        cfg.backend = "bogus".into();
        // `GitService` is not `Debug`, so we can't use `unwrap_err()`; match instead.
        let err = match build_git_service(&cfg, fs) {
            Ok(_) => panic!("expected error for bogus backend"),
            Err(e) => e,
        };
        assert!(err.to_string().contains("unsupported git backend"));
    }

    #[tokio::test]
    async fn build_git_service_local_without_section_errors() {
        Python::initialize();
        let fs = Arc::new(MountableFS::new()) as Arc<dyn ragfs::core::FileSystem>;
        let mut cfg = local_cfg("/tmp/ov-git-test-nolocal");
        cfg.local = None;
        let err = match build_git_service(&cfg, fs) {
            Ok(_) => panic!("expected error when [git.local] missing"),
            Err(e) => e,
        };
        assert!(err.to_string().contains("[git.local] missing"));
    }

    #[test]
    fn map_git_error_feature_disabled() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let err = map_git_error(py, GitError::FeatureDisabled);
            // We don't require the openviking.pyagfs module to be importable
            // in this Rust-only test, so the fallback PyRuntimeError is fine.
            // We just assert that mapping does not panic and yields a PyErr.
            assert!(err.to_string().to_lowercase().contains("git"));
        });
    }

    #[test]
    fn map_git_error_concurrent_commit() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let err = map_git_error(
                py,
                GitError::ConcurrentCommit {
                    ref_name: "refs/heads/main".into(),
                    expected: None,
                    actual: None,
                },
            );
            assert!(err.to_string().to_lowercase().contains("concurrent"));
        });
    }

    /// `RestoreWritebackPartial` must round-trip through `map_git_error`
    /// without panicking. When `openviking.pyagfs` is not importable (the
    /// usual case in pure cargo-test environments) the helper falls back to
    /// `PyRuntimeError` and tags the message so the operator notices the
    /// dropped payload; we only assert on the message preamble here.
    #[test]
    fn map_git_error_writeback_partial() {
        use ragfs::git::RestoreWritebackPartial;
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let payload = RestoreWritebackPartial {
                new_commit_oid: gix_hash::ObjectId::null(gix_hash::Kind::Sha1),
                source_commit: gix_hash::ObjectId::null(gix_hash::Kind::Sha1),
                parent_commit: gix_hash::ObjectId::null(gix_hash::Kind::Sha1),
                written: 1,
                deleted: 0,
                unchanged: 0,
                written_paths: vec!["resources/proj_a/b.md".to_string()],
                deleted_paths: vec![],
                failed_writes: vec![(
                    "resources/proj_a/a.md".to_string(),
                    "forced write failure".to_string(),
                )],
                failed_deletes: vec![],
            };
            let err = map_git_error(py, GitError::RestoreWritebackPartial(Box::new(payload)));
            let s = err.to_string().to_lowercase();
            assert!(
                s.contains("restore writeback partial"),
                "expected partial message, got {s:?}"
            );
            // The Display includes counts derived from the payload.
            assert!(
                s.contains("1 write"),
                "expected write count in message: {s:?}"
            );
        });
    }

    #[test]
    fn map_git_error_path_not_found() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let err = map_git_error(py, GitError::PathNotFound("foo/bar".into()));
            assert!(err.to_string().contains("foo/bar"));
        });
    }

    #[test]
    fn map_git_error_invalid_account() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let err = map_git_error(py, GitError::InvalidAccountId("../bad".into()));
            assert!(err.to_string().contains("bad"));
        });
    }

    #[test]
    fn map_git_error_blob_too_large() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let err = map_git_error(
                py,
                GitError::BlobTooLarge {
                    size: 200,
                    limit: 100,
                },
            );
            assert!(err.to_string().contains("200"));
        });
    }

    use pyo3::types::PyDict;

    #[test]
    fn parse_commit_request_required_fields() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("account", "acct1").unwrap();
            kwargs.set_item("branch", "main").unwrap();
            kwargs.set_item("message", "hello").unwrap();
            kwargs.set_item("author_name", "alice").unwrap();
            kwargs.set_item("author_email", "a@e.com").unwrap();
            let req = parse_commit_request(&kwargs).expect("parses");
            assert_eq!(req.account, "acct1");
            assert_eq!(req.branch, "main");
            assert_eq!(req.message, "hello");
            assert!(req.paths.is_none());
            assert_eq!(req.author_name, "alice");
            assert_eq!(req.author_email, "a@e.com");
        });
    }

    #[test]
    fn parse_commit_request_with_paths_list() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("account", "a").unwrap();
            kwargs.set_item("branch", "main").unwrap();
            kwargs.set_item("message", "m").unwrap();
            kwargs.set_item("author_name", "n").unwrap();
            kwargs.set_item("author_email", "e").unwrap();
            kwargs
                .set_item("paths", vec!["resources/a.md", "resources/b.md"])
                .unwrap();
            let req = parse_commit_request(&kwargs).expect("parses");
            assert_eq!(req.paths.as_ref().unwrap().len(), 2);
            assert_eq!(req.paths.as_ref().unwrap()[0], "resources/a.md");
        });
    }

    #[test]
    fn parse_commit_request_missing_required_errors() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("branch", "main").unwrap();
            // missing account, message, author_*
            let err = parse_commit_request(&kwargs).unwrap_err();
            assert!(err.to_string().contains("account"));
        });
    }

    #[test]
    fn parse_restore_request_defaults_project_dir_none() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("account", "a").unwrap();
            kwargs.set_item("branch", "main").unwrap();
            kwargs.set_item("source_commit", "deadbeef").unwrap();
            kwargs.set_item("author_name", "n").unwrap();
            kwargs.set_item("author_email", "e").unwrap();
            let req = parse_restore_request(&kwargs).expect("parses");
            assert!(req.project_dir.is_none());
            assert!(!req.dry_run);
            assert!(req.message.is_none());
        });
    }

    #[test]
    fn parse_restore_request_project_dir_some() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("account", "a").unwrap();
            kwargs.set_item("branch", "main").unwrap();
            kwargs.set_item("project_dir", "resources/proj").unwrap();
            kwargs.set_item("source_commit", "deadbeef").unwrap();
            kwargs.set_item("author_name", "n").unwrap();
            kwargs.set_item("author_email", "e").unwrap();
            let req = parse_restore_request(&kwargs).expect("parses");
            assert_eq!(req.project_dir.as_deref(), Some("resources/proj"));
        });
    }

    #[test]
    fn parse_restore_request_dry_run_and_message() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("account", "a").unwrap();
            kwargs.set_item("branch", "main").unwrap();
            kwargs.set_item("project_dir", "x").unwrap();
            kwargs.set_item("source_commit", "abc123").unwrap();
            kwargs.set_item("author_name", "n").unwrap();
            kwargs.set_item("author_email", "e").unwrap();
            kwargs.set_item("dry_run", true).unwrap();
            kwargs.set_item("message", "custom msg").unwrap();
            let req = parse_restore_request(&kwargs).expect("parses");
            assert!(req.dry_run);
            assert_eq!(req.message.as_deref(), Some("custom msg"));
        });
    }

    #[test]
    fn parse_show_request_with_and_without_path() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("account", "a").unwrap();
            kwargs.set_item("target_ref", "main").unwrap();
            let req = parse_show_request(&kwargs).expect("parses");
            assert!(req.path.is_none());

            let kwargs2 = PyDict::new(py);
            kwargs2.set_item("account", "a").unwrap();
            kwargs2.set_item("target_ref", "main").unwrap();
            kwargs2.set_item("path", "resources/a.md").unwrap();
            let req2 = parse_show_request(&kwargs2).expect("parses");
            assert_eq!(req2.path.as_deref(), Some("resources/a.md"));
        });
    }

    #[test]
    fn commit_response_created_to_dict() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let oid = gix_hash::ObjectId::null(gix_hash::Kind::Sha1);
            let resp = ragfs::git::CommitResponse::Created {
                commit_oid: oid,
                changed: 3,
            };
            let obj = commit_response_to_pydict(py, resp).expect("converts");
            let d: &Bound<PyDict> = obj.bind(py).downcast().unwrap();
            let result: String = d.get_item("result").unwrap().unwrap().extract().unwrap();
            assert_eq!(result, "created");
            let changed: usize = d.get_item("changed").unwrap().unwrap().extract().unwrap();
            assert_eq!(changed, 3);
        });
    }

    #[test]
    fn commit_response_noop_to_dict() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let oid = gix_hash::ObjectId::null(gix_hash::Kind::Sha1);
            let resp = ragfs::git::CommitResponse::Noop { commit_oid: oid };
            let obj = commit_response_to_pydict(py, resp).expect("converts");
            let d: &Bound<PyDict> = obj.bind(py).downcast().unwrap();
            let result: String = d.get_item("result").unwrap().unwrap().extract().unwrap();
            assert_eq!(result, "noop");
        });
    }

    #[test]
    fn show_response_blob_to_dict_carries_bytes() {
        pyo3::prepare_freethreaded_python();
        Python::attach(|py| {
            let oid = gix_hash::ObjectId::null(gix_hash::Kind::Sha1);
            let resp = ragfs::git::ShowResponse::Blob {
                oid,
                size: 5,
                bytes: bytes::Bytes::from_static(b"hello"),
            };
            let obj = show_response_to_pydict(py, resp).expect("converts");
            let d: &Bound<PyDict> = obj.bind(py).downcast().unwrap();
            let b: Vec<u8> = d.get_item("bytes").unwrap().unwrap().extract().unwrap();
            assert_eq!(b, b"hello".to_vec());
            let size: u64 = d.get_item("size").unwrap().unwrap().extract().unwrap();
            assert_eq!(size, 5);
        });
    }

    // ---------- direct binding: account isolation ----------

    /// Build a `GitService` over local backends, mirroring what the binding's
    /// `build_git_service` does for the `local` backend. The `base_dir` does
    /// not need to exist: a malicious account must be rejected at the
    /// `GitService` boundary *before* any path under `base_dir` is built.
    fn local_git_service() -> Arc<GitService> {
        use ragfs::git::{LocalObjectStore, LocalRefStore};
        let base = std::env::temp_dir().join("ov-git-account-validation-test");
        let vfs = Arc::new(MountableFS::new()) as Arc<dyn ragfs::core::FileSystem>;
        let object_store = Arc::new(LocalObjectStore::new(&base)) as Arc<dyn ObjectStore>;
        let ref_store = Arc::new(LocalRefStore::new(&base)) as Arc<dyn RefStore>;
        Arc::new(GitService::new(vfs, object_store, ref_store))
    }

    #[tokio::test]
    async fn binding_commit_rejects_traversal_account() {
        let svc = local_git_service();
        let req = CommitRequest {
            account: "../escape".into(),
            branch: "main".into(),
            message: "m".into(),
            paths: None,
            author_name: "n".into(),
            author_email: "e".into(),
        };
        let err = svc.commit(req).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn binding_commit_rejects_slash_account() {
        let svc = local_git_service();
        let req = CommitRequest {
            account: "a/b".into(),
            branch: "main".into(),
            message: "m".into(),
            paths: None,
            author_name: "n".into(),
            author_email: "e".into(),
        };
        let err = svc.commit(req).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn binding_show_rejects_backslash_account() {
        let svc = local_git_service();
        let req = ShowRequest {
            account: "a\\b".into(),
            target_ref: "main".into(),
            path: None,
        };
        let err = svc.show(req).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn binding_restore_rejects_empty_account() {
        let svc = local_git_service();
        let req = RestoreRequest {
            account: "".into(),
            branch: "main".into(),
            project_dir: Some("resources/x".into()),
            source_commit: "deadbeef".into(),
            dry_run: false,
            message: None,
            author_name: "n".into(),
            author_email: "e".into(),
        };
        let err = svc.restore(req).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }
}
