//! Python bindings for RAGFS - Rust AGFS filesystem
//!
//! Provides `RAGFSBindingClient`, a PyO3 native class that is API-compatible
//! with the existing Go-based `AGFSBindingClient`. This embeds the ragfs
//! filesystem engine directly in the Python process (no HTTP server needed).

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyType};
use std::collections::HashMap;
use std::future::Future;
use std::sync::Arc;
use std::time::UNIX_EPOCH;

use ragfs::core::{
    build_default_stack, ConfigValue, EncryptionConfig, FileInfo, FileSystem, FilesystemStats,
    FsContext, FsContextInner, FsOperation, GrepResult, MountableFS, OperationStats, PluginConfig,
    RagfsConfig, TreeEntry, WriteFlag, FS_CTX,
};

fn py_detach_blocking<T, F>(py: Python<'_>, f: F) -> T
where
    T: Send,
    F: Send + FnOnce() -> T,
{
    py.detach(f)
}

/// Get a Python exception class from the pyagfs module
fn get_exception<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyType>> {
    let pyagfs = PyModule::import(py, "openviking.pyagfs")?;
    let exc = pyagfs.getattr(name)?;
    Ok(exc.cast_into()?)
}

/// Create a PyErr from an exception type name and message
fn new_py_err(name: &str, msg: String) -> PyErr {
    Python::attach(|py| {
        if let Ok(exc) = get_exception(py, name) {
            PyErr::from_type(exc, msg)
        } else {
            PyRuntimeError::new_err(msg)
        }
    })
}

/// Convert a ragfs error into the appropriate Python exception
fn to_py_err(e: ragfs::core::Error) -> PyErr {
    let msg = e.to_string();
    match e {
        ragfs::core::Error::NotFound(_) => new_py_err("AGFSNotFoundError", msg),
        ragfs::core::Error::AlreadyExists(_) => new_py_err("AGFSAlreadyExistsError", msg),
        ragfs::core::Error::PermissionDenied(_) => new_py_err("AGFSPermissionDeniedError", msg),
        ragfs::core::Error::InvalidPath(_) => new_py_err("AGFSInvalidPathError", msg),
        ragfs::core::Error::NotADirectory(_) => new_py_err("AGFSNotADirectoryError", msg),
        ragfs::core::Error::IsADirectory(_) => new_py_err("AGFSIsADirectoryError", msg),
        ragfs::core::Error::DirectoryNotEmpty(_) => new_py_err("AGFSDirectoryNotEmptyError", msg),
        ragfs::core::Error::InvalidOperation(_) => new_py_err("AGFSInvalidOperationError", msg),
        ragfs::core::Error::Io(_) => new_py_err("AGFSIoError", msg),
        ragfs::core::Error::Plugin(_) => {
            // Check if the plugin error message contains known patterns
            let err_msg = msg.to_lowercase();
            if err_msg.contains("directory not empty") {
                new_py_err("AGFSDirectoryNotEmptyError", msg)
            } else {
                new_py_err("AGFSPluginError", msg)
            }
        }
        ragfs::core::Error::Config(_) => new_py_err("AGFSConfigError", msg),
        ragfs::core::Error::MountPointNotFound(_) => new_py_err("AGFSMountPointNotFoundError", msg),
        ragfs::core::Error::MountPointExists(_) => new_py_err("AGFSMountPointExistsError", msg),
        ragfs::core::Error::Serialization(_) => new_py_err("AGFSSerializationError", msg),
        ragfs::core::Error::Network(_) => new_py_err("AGFSNetworkError", msg),
        ragfs::core::Error::Timeout(_) => new_py_err("AGFSTimeoutError", msg),
        ragfs::core::Error::Internal(_) => new_py_err("AGFSInternalError", msg),
    }
}

/// Convert FileInfo to a Python dict matching the Go binding JSON format:
/// {"name": str, "size": int, "mode": int, "modTime": str, "isDir": bool}
fn file_info_to_py_dict(py: Python<'_>, info: &FileInfo) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("name", &info.name)?;
    dict.set_item("size", info.size)?;
    dict.set_item("mode", info.mode)?;

    // modTime as RFC3339 string (Go binding format)
    let secs = info
        .mod_time
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let mod_time = format_rfc3339(secs);
    dict.set_item("modTime", mod_time)?;

    dict.set_item("isDir", info.is_dir)?;
    Ok(dict.into())
}

/// Convert TreeEntry to a Python dict:
/// {"path": str, "rel_path": str, "info": dict, "extra": dict}
fn tree_entry_to_py_dict(py: Python<'_>, entry: &TreeEntry) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("path", &entry.path)?;
    dict.set_item("rel_path", &entry.rel_path)?;
    dict.set_item("info", file_info_to_py_dict(py, &entry.info)?)?;

    let extra_dict = PyDict::new(py);
    for (k, v) in &entry.extra {
        let py_val: Py<PyAny> = serde_json_to_py(py, v)?;
        extra_dict.set_item(k, py_val)?;
    }
    dict.set_item("extra", extra_dict)?;

    Ok(dict.into())
}

/// Convert a serde_json::Value to a Python object.
fn serde_json_to_py(py: Python<'_>, val: &serde_json::Value) -> PyResult<Py<PyAny>> {
    match val {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => {
            let val: bool = *b;
            let bound = val
                .into_pyobject(py)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
            Ok(bound.as_any().clone().unbind())
        }
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.into_any().unbind())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        serde_json::Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(serde_json_to_py(py, item)?)?;
            }
            Ok(list.into())
        }
        serde_json::Value::Object(obj) => {
            let d = PyDict::new(py);
            for (k, v) in obj {
                d.set_item(k, serde_json_to_py(py, v)?)?;
            }
            Ok(d.into())
        }
    }
}

/// Convert GrepResult to a Python dict matching the Go binding JSON format:
/// {"matches": [{"file": str, "line": int, "content": str}, ...], "count": int}
fn grep_result_to_py_dict(py: Python<'_>, result: &GrepResult) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);

    let matches_list = PyList::empty(py);
    for m in &result.matches {
        let match_dict = PyDict::new(py);
        match_dict.set_item("file", &m.file)?;
        match_dict.set_item("line", m.line)?;
        match_dict.set_item("content", &m.content)?;
        matches_list.append(match_dict)?;
    }

    dict.set_item("matches", matches_list)?;
    dict.set_item("count", result.count)?;

    Ok(dict.into())
}

/// Convert OperationStats to a Python dict.
fn operation_stats_to_py_dict(py: Python<'_>, stats: &OperationStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("count", stats.count)?;
    dict.set_item("total_time_us", stats.total_time_us)?;
    dict.set_item("min_time_us", stats.min_time_us)?;
    dict.set_item("max_time_us", stats.max_time_us)?;
    dict.set_item("avg_time_us", stats.avg_time_us())?;
    Ok(dict.into())
}

/// Convert FilesystemStats to a Python dict.
fn filesystem_stats_to_py_dict(py: Python<'_>, stats: &FilesystemStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    let ops_dict = PyDict::new(py);

    for op in FsOperation::all() {
        let op_stats = stats.get(*op);
        let op_dict = operation_stats_to_py_dict(py, op_stats)?;
        ops_dict.set_item(op.as_str(), op_dict)?;
    }

    dict.set_item("operations", ops_dict)?;
    Ok(dict.into())
}

/// Format unix timestamp as RFC3339 string (simplified, UTC)
fn format_rfc3339(secs: u64) -> String {
    let s = secs;
    let days = s / 86400;
    let time_of_day = s % 86400;
    let h = time_of_day / 3600;
    let m = (time_of_day % 3600) / 60;
    let sec = time_of_day % 60;

    // Calculate date from days since epoch (simplified)
    let (year, month, day) = days_to_ymd(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year, month, day, h, m, sec
    )
}

/// Convert days since Unix epoch to (year, month, day)
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    // Algorithm from http://howardhinnant.github.io/date_algorithms.html
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Convert a Python dict to HashMap<String, ConfigValue>
fn py_dict_to_config(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, ConfigValue>> {
    let mut params = HashMap::new();
    for (k, v) in dict.iter() {
        let key: String = k.extract()?;
        let value = if let Ok(s) = v.extract::<String>() {
            ConfigValue::String(s)
        } else if let Ok(b) = v.extract::<bool>() {
            ConfigValue::Bool(b)
        } else if let Ok(i) = v.extract::<i64>() {
            ConfigValue::Int(i)
        } else {
            ConfigValue::String(v.str()?.to_string())
        };
        params.insert(key, value);
    }
    Ok(params)
}

/// Build an immutable `FsContext` from an optional Python `{"account_id": str}` dict.
///
/// A context is always constructed (even when the dict is absent or lacks `account_id`), so the
/// "every ragfs call carries a ctx" invariant holds. Missing `account_id` is represented as an
/// empty field; `FsContextView::account_id()` treats it as absent, so encrypted content operations
/// fail fast instead of using an accidental empty tenant key.
fn build_fs_context(ctx: Option<HashMap<String, String>>) -> FsContext {
    let account_id = ctx
        .and_then(|m| m.get("account_id").cloned())
        .unwrap_or_default();
    Arc::new(FsContextInner::new(account_id))
}

/// RAGFS Python Binding Client.
///
/// Embeds the ragfs filesystem engine directly in the Python process.
/// API-compatible with the Go-based AGFSBindingClient.
#[pyclass]
struct RAGFSBindingClient {
    /// Mount manager: mount/unmount/list/get_*_stats/register_plugin.
    mountable: Arc<MountableFS>,
    /// Data entry point: `Stats(Encryption(Mountable))` when encrypted, else `Stats(Mountable)`.
    top: Arc<dyn FileSystem>,
    rt: tokio::runtime::Runtime,
}

impl RAGFSBindingClient {
    /// Run an async filesystem op on the runtime, releasing the GIL, inside the FS_CTX scope.
    ///
    /// Centralizes the GIL-detach + `FS_CTX.scope` + `block_on` pattern so every data method
    /// shares one implementation (no per-method duplication).
    fn run_scoped<T, Fut, F>(&self, py: Python<'_>, ctx: FsContext, f: F) -> T
    where
        T: Send,
        Fut: Future<Output = T>,
        F: Send + FnOnce() -> Fut,
    {
        py_detach_blocking(py, move || {
            self.rt
                .block_on(FS_CTX.scope(ctx, async move { f().await }))
        })
    }
}

#[pymethods]
impl RAGFSBindingClient {
    /// Create a new RAGFS binding client.
    ///
    /// `config_path` is a deprecated compatibility parameter kept only so legacy callers using
    /// `RAGFSBindingClient(config_path=...)` do not fail. `config` is an optional sectioned dict
    /// (mirrors ov.conf). The `encryption` section, when present, carries `root_key` (32 bytes) +
    /// `provider_type` (int) and causes the stack to include an `EncryptionWrappedFS` layer.
    /// Absence of the section yields a plaintext stack.
    #[new]
    #[pyo3(signature = (config_path=None, config=None))]
    fn new(
        py: Python<'_>,
        config_path: Option<&str>,
        config: Option<HashMap<String, Py<PyAny>>>,
    ) -> PyResult<Self> {
        let _ = config_path;
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create runtime: {}", e)))?;

        // Phase A (holding GIL): parse the sectioned config into an owned RagfsConfig.
        let mut ragfs_cfg = RagfsConfig::default();
        if let Some(cfg) = config {
            if let Some(enc_obj) = cfg.get("encryption") {
                let enc: HashMap<String, Py<PyAny>> = enc_obj.extract(py)?;
                let rk: Vec<u8> = enc
                    .get("root_key")
                    .ok_or_else(|| PyValueError::new_err("encryption.root_key required"))?
                    .extract(py)?;
                let root_key: [u8; 32] = rk
                    .as_slice()
                    .try_into()
                    .map_err(|_| PyValueError::new_err("root_key must be 32 bytes"))?;
                let provider_type: u8 = enc
                    .get("provider_type")
                    .ok_or_else(|| PyValueError::new_err("encryption.provider_type required"))?
                    .extract(py)?;
                ragfs_cfg.encryption = Some(EncryptionConfig {
                    root_key,
                    provider_type,
                });
            }
        }

        // Phase B: build the stack (no encryption layer when the section is absent).
        let stack = rt.block_on(build_default_stack(ragfs_cfg));
        Ok(Self {
            mountable: stack.mountable,
            top: stack.top,
            rt,
        })
    }

    /// Check client health.
    fn health(&self) -> PyResult<HashMap<String, String>> {
        let mut m = HashMap::new();
        m.insert("status".to_string(), "healthy".to_string());
        Ok(m)
    }

    /// Get client capabilities.
    fn get_capabilities(&self) -> PyResult<HashMap<String, Py<PyAny>>> {
        Python::attach(|py| {
            let mut m = HashMap::new();
            m.insert(
                "version".to_string(),
                "ragfs-python".into_pyobject(py)?.into_any().unbind(),
            );
            let mut features = vec![
                "memfs",
                "kvfs",
                "queuefs",
                "sqlfs",
                "localfs",
                "serverinfofs",
            ];
            #[cfg(feature = "s3")]
            features.push("s3fs");
            m.insert(
                "features".to_string(),
                features.into_pyobject(py)?.into_any().unbind(),
            );
            Ok(m)
        })
    }

    /// List directory contents.
    ///
    /// Returns a list of file info dicts with keys:
    /// name, size, mode, modTime, isDir
    #[pyo3(signature = (path, ctx=None))]
    fn ls(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let entries = self
            .run_scoped(py, fs_ctx, move || async move { top.read_dir(&path).await })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let list = PyList::empty(py);
            for entry in &entries {
                let dict = file_info_to_py_dict(py, entry)?;
                list.append(dict)?;
            }
            Ok(list.into())
        })
    }

    /// Read file content.
    ///
    /// Args:
    ///     path: File path
    ///     offset: Starting position (default: 0)
    ///     size: Number of bytes to read (default: -1, read all)
    ///     stream: Not supported in binding mode
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    #[pyo3(signature = (path, offset=0, size=-1, stream=false, ctx=None))]
    fn read(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
        stream: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let off = if offset < 0 { 0u64 } else { offset as u64 };
        let sz = if size < 0 { 0u64 } else { size as u64 };

        let data = self
            .run_scoped(
                py,
                fs_ctx,
                move || async move { top.read(&path, off, sz).await },
            )
            .map_err(to_py_err)?;

        Python::attach(|py| Ok(PyBytes::new(py, &data).into()))
    }

    /// Read file content (alias for read).
    #[pyo3(signature = (path, offset=0, size=-1, stream=false, ctx=None))]
    fn cat(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
        stream: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        self.read(py, path, offset, size, stream, ctx)
    }

    /// Read raw bytes straight from the mount layer, bypassing the encryption layer.
    ///
    /// Used by cp / persist whole-blob copies (ciphertext is moved verbatim, never decrypted).
    #[pyo3(signature = (path, offset=0, size=-1))]
    fn read_raw(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
    ) -> PyResult<Py<PyAny>> {
        let mountable = self.mountable.clone();
        let off = if offset < 0 { 0u64 } else { offset as u64 };
        let sz = if size < 0 { 0u64 } else { size as u64 };
        let data = py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.read(&path, off, sz).await })
        })
        .map_err(to_py_err)?;
        Python::attach(|py| Ok(PyBytes::new(py, &data).into()))
    }

    /// Write raw bytes straight to the mount layer, bypassing the encryption layer.
    ///
    /// Counterpart to `read_raw` for cp / persist whole-blob copies.
    fn write_raw(&self, py: Python<'_>, path: String, data: Vec<u8>) -> PyResult<String> {
        let mountable = self.mountable.clone();
        let len = data.len();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.write(&path, &data, 0, WriteFlag::Create).await })
        })
        .map_err(to_py_err)?;
        Ok(format!("Written {} bytes", len))
    }

    /// Write data to file.
    ///
    /// Args:
    ///     path: File path
    ///     data: File content as bytes
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    #[pyo3(signature = (path, data, max_retries=3, ctx=None))]
    fn write(
        &self,
        py: Python<'_>,
        path: String,
        data: Vec<u8>,
        max_retries: i32,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<String> {
        let _ = max_retries; // not applicable for local binding
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let len = data.len();
        self.run_scoped(py, fs_ctx, move || async move {
            top.write(&path, &data, 0, WriteFlag::Create).await
        })
        .map_err(to_py_err)?;

        Ok(format!("Written {} bytes", len))
    }

    /// Create a new empty file.
    #[pyo3(signature = (path, ctx=None))]
    fn create(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move { top.create(&path).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Create a directory.
    #[pyo3(signature = (path, mode="755", ctx=None))]
    fn mkdir(
        &self,
        py: Python<'_>,
        path: String,
        mode: &str,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.mkdir(&path, mode_int).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Ensure all parent directories exist for the given path.
    #[pyo3(signature = (path, mode="755", ctx=None))]
    fn ensure_parent_dirs(
        &self,
        py: Python<'_>,
        path: String,
        mode: &str,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.ensure_parent_dirs(&path, mode_int).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            "parent directories ensured".to_string(),
        );
        Ok(m)
    }

    /// Remove a file or directory.
    #[pyo3(signature = (path, recursive=false, ctx=None))]
    fn rm(
        &self,
        py: Python<'_>,
        path: String,
        recursive: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            if recursive {
                top.remove_all(&path).await
            } else {
                top.remove(&path).await
            }
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "deleted".to_string());
        Ok(m)
    }

    /// Get file/directory information.
    #[pyo3(signature = (path, ctx=None))]
    fn stat(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let info = self
            .run_scoped(py, fs_ctx, move || async move { top.stat(&path).await })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = file_info_to_py_dict(py, &info)?;
            Ok(dict.into())
        })
    }

    /// Rename/move a file or directory.
    #[pyo3(signature = (old_path, new_path, ctx=None))]
    fn mv(
        &self,
        py: Python<'_>,
        old_path: String,
        new_path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.rename(&old_path, &new_path).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "renamed".to_string());
        Ok(m)
    }

    /// Change file permissions.
    #[pyo3(signature = (path, mode, ctx=None))]
    fn chmod(
        &self,
        py: Python<'_>,
        path: String,
        mode: u32,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(
            py,
            fs_ctx,
            move || async move { top.chmod(&path, mode).await },
        )
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "chmod ok".to_string());
        Ok(m)
    }

    /// Touch a file (create if not exists, or update timestamp).
    #[pyo3(signature = (path, ctx=None))]
    fn touch(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            // Try create; if already exists, write empty to update mtime
            match top.create(&path).await {
                Ok(_) => Ok(()),
                Err(_) => {
                    let existing = top.read(&path, 0, 0).await?;
                    top.write(&path, &existing, 0, WriteFlag::Create).await?;
                    Ok(())
                }
            }
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "touched".to_string());
        Ok(m)
    }

    /// List all mounted plugins.
    fn mounts(&self, py: Python<'_>) -> PyResult<Vec<HashMap<String, String>>> {
        let mountable = self.mountable.clone();
        let mount_list = py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.list_mounts().await })
        });

        let result: Vec<HashMap<String, String>> = mount_list
            .into_iter()
            .map(|(path, fstype)| {
                let mut m = HashMap::new();
                m.insert("path".to_string(), path);
                m.insert("fstype".to_string(), fstype);
                m
            })
            .collect();

        Ok(result)
    }

    /// Mount a plugin dynamically.
    ///
    /// Args:
    ///     fstype: Filesystem type (e.g., "memfs", "sqlfs", "kvfs", "queuefs")
    ///     path: Mount path
    ///     config: Plugin configuration as dict
    #[pyo3(signature = (fstype, path, config=None))]
    fn mount(
        &self,
        py: Python<'_>,
        fstype: String,
        path: String,
        config: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<HashMap<String, String>> {
        let params = match config {
            Some(dict) => py_dict_to_config(dict)?,
            None => HashMap::new(),
        };

        let plugin_config = PluginConfig {
            name: fstype.clone(),
            mount_path: path.clone(),
            params,
        };

        let mountable = self.mountable.clone();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.mount(plugin_config).await })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            format!("mounted {} at {}", fstype, path),
        );
        Ok(m)
    }

    /// Unmount a plugin.
    fn unmount(&self, py: Python<'_>, path: String) -> PyResult<HashMap<String, String>> {
        let mountable = self.mountable.clone();
        let path_clone = path.clone();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.unmount(&path_clone).await })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), format!("unmounted {}", path));
        Ok(m)
    }

    /// List all registered plugin names.
    fn list_plugins(&self) -> PyResult<Vec<String>> {
        // Return names of built-in plugins
        let mut plugins = vec![
            "memfs".to_string(),
            "kvfs".to_string(),
            "queuefs".to_string(),
            "sqlfs".to_string(),
            "localfs".to_string(),
            "serverinfofs".to_string(),
        ];
        #[cfg(feature = "s3")]
        plugins.push("s3fs".to_string());
        Ok(plugins)
    }

    /// Get detailed plugin information.
    fn get_plugins_info(&self) -> PyResult<Vec<String>> {
        self.list_plugins()
    }

    /// Load an external plugin (not supported in Rust binding).
    fn load_plugin(&self, _library_path: String) -> PyResult<HashMap<String, String>> {
        Err(PyRuntimeError::new_err(
            "External plugin loading not supported in ragfs-python binding",
        ))
    }

    /// Unload an external plugin (not supported in Rust binding).
    fn unload_plugin(&self, _library_path: String) -> PyResult<HashMap<String, String>> {
        Err(PyRuntimeError::new_err(
            "External plugin unloading not supported in ragfs-python binding",
        ))
    }

    /// Search for pattern in files using regular expressions.
    ///
    /// Args:
    ///     path: File or directory path to search
    ///     pattern: Regular expression pattern to search for
    ///     recursive: Whether to search recursively in subdirectories (default: false)
    ///     case_insensitive: Whether to perform case-insensitive matching (default: false)
    ///     stream: Not supported in binding mode
    ///     node_limit: Maximum number of matches to return (default: None, no limit)
    ///     exclude_path: Optional path prefix to exclude from search (default: None)
    ///     level_limit: Optional maximum depth relative to query root (default: None)
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A dict with "matches" (list of match dicts) and "count" (total matches)
    #[pyo3(signature = (path, pattern, recursive=false, case_insensitive=false, stream=false, node_limit=None, exclude_path=None, level_limit=None, ctx=None))]
    #[allow(clippy::too_many_arguments)]
    fn grep(
        &self,
        py: Python<'_>,
        path: String,
        pattern: String,
        recursive: bool,
        case_insensitive: bool,
        stream: bool,
        node_limit: Option<i32>,
        exclude_path: Option<String>,
        level_limit: Option<i32>,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let result = self
            .run_scoped(py, fs_ctx, move || async move {
                top.grep(
                    &path,
                    &pattern,
                    recursive,
                    case_insensitive,
                    limit,
                    exclude_path.as_deref(),
                    level_limit_usize,
                )
                .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = grep_result_to_py_dict(py, &result)?;
            Ok(dict.into())
        })
    }

    /// Recursively traverse a directory tree.
    ///
    /// Args:
    ///     path: The root path of the traversal
    ///     show_hidden: Whether to include hidden files (default: False)
    ///     node_limit: Maximum number of nodes to return (default: None, no limit)
    ///     level_limit: Maximum depth relative to query root (default: None, no limit)
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A list of dicts, each with keys: path, rel_path, info, extra
    #[pyo3(signature = (path, show_hidden=false, node_limit=None, level_limit=None, ctx=None))]
    fn tree_directory(
        &self,
        py: Python<'_>,
        path: String,
        show_hidden: bool,
        node_limit: Option<i32>,
        level_limit: Option<i32>,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let entries = self
            .run_scoped(py, fs_ctx, move || async move {
                top.tree_directory(&path, show_hidden, limit, level_limit_usize)
                    .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let list = PyList::empty(py);
            for entry in &entries {
                let dict = tree_entry_to_py_dict(py, entry)?;
                list.append(dict)?;
            }
            Ok(list.into())
        })
    }

    /// Calculate file digest (not yet implemented in ragfs).
    #[pyo3(signature = (path, algorithm="xxh3"))]
    fn digest(&self, path: String, algorithm: &str) -> PyResult<HashMap<String, String>> {
        let _ = (path, algorithm);
        Err(PyRuntimeError::new_err(
            "digest not yet implemented in ragfs-python",
        ))
    }

    /// Get filesystem statistics.
    ///
    /// Args:
    ///     path: Optional mount path to get stats for. If None, get stats for all mounts.
    ///
    /// Returns:
    ///     Statistics data as a dict.
    #[pyo3(signature = (path=None))]
    fn get_stats(&self, py: Python<'_>, path: Option<String>) -> PyResult<Py<PyAny>> {
        let fs = self.mountable.clone();

        if let Some(mount_path) = path {
            // Get stats for a specific mount
            let fs2 = fs.clone();
            let mount_path_clone = mount_path.clone();
            let stats = py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.get_mount_stats(&mount_path_clone).await })
            })
            .map_err(to_py_err)?;

            let mounts = py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs2.list_mounts().await })
            });

            let plugin_name = mounts
                .into_iter()
                .find(|(p, _)| p == &mount_path)
                .map(|(_, plugin)| plugin)
                .unwrap_or_default();

            Python::attach(|py| {
                let result = PyDict::new(py);
                result.set_item("path", mount_path)?;
                result.set_item("plugin", plugin_name)?;
                let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                result.set_item("stats", stats_dict)?;
                Ok(result.into())
            })
        } else {
            // Get stats for all mounts
            let all_stats = py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.get_all_stats().await })
            });

            Python::attach(|py| {
                let mounts_list = PyList::empty(py);
                for (path, (plugin, stats)) in all_stats {
                    let mount_dict = PyDict::new(py);
                    mount_dict.set_item("path", path)?;
                    mount_dict.set_item("plugin", plugin)?;
                    let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                    mount_dict.set_item("stats", stats_dict)?;
                    mounts_list.append(mount_dict)?;
                }

                let result = PyDict::new(py);
                result.set_item("mounts", mounts_list)?;
                Ok(result.into())
            })
        }
    }
}

/// Python module definition
#[pymodule]
fn ragfs_python(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RAGFSBindingClient>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::PyDict;

    #[test]
    fn detach_blocking_helper_runs_without_python_objects() {
        Python::attach(|py| {
            let value: i32 = py_detach_blocking(py, || 40 + 2);
            assert_eq!(value, 42);
        });
    }

    #[test]
    fn constructor_accepts_legacy_config_path_keyword() {
        Python::attach(|py| {
            let ty = py.get_type::<RAGFSBindingClient>();
            let kwargs = PyDict::new(py);
            kwargs.set_item("config_path", "/tmp/legacy-ov.conf").unwrap();
            let instance = ty.call((), Some(&kwargs));
            assert!(instance.is_ok());
        });
    }
}
