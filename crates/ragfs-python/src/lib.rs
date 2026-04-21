//! Python bindings for RAGFS - Rust AGFS filesystem
//!
//! Provides `RAGFSBindingClient`, a PyO3 native class that is API-compatible
//! with the existing Go-based `AGFSBindingClient`. This embeds the ragfs
//! filesystem engine directly in the Python process (no HTTP server needed).

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyType};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::UNIX_EPOCH;

use ragfs::core::{ConfigValue, FileInfo, FileSystem, MountableFS, PluginConfig, WriteFlag};
#[cfg(feature = "s3")]
use ragfs::plugins::S3FSPlugin;
use ragfs::plugins::{
    KVFSPlugin, LocalFSPlugin, MemFSPlugin, QueueFSPlugin, SQLFSPlugin, ServerInfoFSPlugin,
};

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

/// RAGFS Python Binding Client.
///
/// Embeds the ragfs filesystem engine directly in the Python process.
/// API-compatible with the Go-based AGFSBindingClient.
#[pyclass]
struct RAGFSBindingClient {
    fs: Arc<MountableFS>,
    rt: tokio::runtime::Runtime,
}

#[pymethods]
impl RAGFSBindingClient {
    /// Create a new RAGFS binding client.
    ///
    /// Initializes the filesystem engine with all built-in plugins registered.
    #[new]
    #[pyo3(signature = (config_path=None))]
    fn new(config_path: Option<&str>) -> PyResult<Self> {
        let _ = config_path; // reserved for future use

        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create runtime: {}", e)))?;

        let fs = Arc::new(MountableFS::new());

        // Register all built-in plugins
        rt.block_on(async {
            fs.register_plugin(MemFSPlugin).await;
            fs.register_plugin(KVFSPlugin).await;
            fs.register_plugin(QueueFSPlugin::new()).await;
            fs.register_plugin(SQLFSPlugin::new()).await;
            fs.register_plugin(LocalFSPlugin::new()).await;
            fs.register_plugin(ServerInfoFSPlugin::new()).await;
            #[cfg(feature = "s3")]
            fs.register_plugin(S3FSPlugin::new()).await;
        });

        Ok(Self { fs, rt })
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
    fn ls(&self, path: String) -> PyResult<Py<PyAny>> {
        let fs = self.fs.clone();
        let entries = self
            .rt
            .block_on(async move { fs.read_dir(&path).await })
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
    #[pyo3(signature = (path, offset=0, size=-1, stream=false))]
    fn read(&self, path: String, offset: i64, size: i64, stream: bool) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs = self.fs.clone();
        let off = if offset < 0 { 0u64 } else { offset as u64 };
        let sz = if size < 0 { 0u64 } else { size as u64 };

        let data = self
            .rt
            .block_on(async move { fs.read(&path, off, sz).await })
            .map_err(to_py_err)?;

        Python::attach(|py| Ok(PyBytes::new(py, &data).into()))
    }

    /// Read file content (alias for read).
    #[pyo3(signature = (path, offset=0, size=-1, stream=false))]
    fn cat(&self, path: String, offset: i64, size: i64, stream: bool) -> PyResult<Py<PyAny>> {
        self.read(path, offset, size, stream)
    }

    /// Write data to file.
    ///
    /// Args:
    ///     path: File path
    ///     data: File content as bytes
    #[pyo3(signature = (path, data, max_retries=3))]
    fn write(&self, path: String, data: Vec<u8>, max_retries: i32) -> PyResult<String> {
        let _ = max_retries; // not applicable for local binding
        let fs = self.fs.clone();
        let len = data.len();
        self.rt
            .block_on(async move { fs.write(&path, &data, 0, WriteFlag::Create).await })
            .map_err(to_py_err)?;

        Ok(format!("Written {} bytes", len))
    }

    /// Create a new empty file.
    fn create(&self, path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        self.rt
            .block_on(async move { fs.create(&path).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Create a directory.
    #[pyo3(signature = (path, mode="755"))]
    fn mkdir(&self, path: String, mode: &str) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs = self.fs.clone();
        self.rt
            .block_on(async move { fs.mkdir(&path, mode_int).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Remove a file or directory.
    #[pyo3(signature = (path, recursive=false))]
    fn rm(&self, path: String, recursive: bool) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        self.rt
            .block_on(async move {
                if recursive {
                    fs.remove_all(&path).await
                } else {
                    fs.remove(&path).await
                }
            })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "deleted".to_string());
        Ok(m)
    }

    /// Get file/directory information.
    fn stat(&self, path: String) -> PyResult<Py<PyAny>> {
        let fs = self.fs.clone();
        let info = self
            .rt
            .block_on(async move { fs.stat(&path).await })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = file_info_to_py_dict(py, &info)?;
            Ok(dict.into())
        })
    }

    /// Rename/move a file or directory.
    fn mv(&self, old_path: String, new_path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        self.rt
            .block_on(async move { fs.rename(&old_path, &new_path).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "renamed".to_string());
        Ok(m)
    }

    /// Change file permissions.
    fn chmod(&self, path: String, mode: u32) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        self.rt
            .block_on(async move { fs.chmod(&path, mode).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "chmod ok".to_string());
        Ok(m)
    }

    /// Touch a file (create if not exists, or update timestamp).
    fn touch(&self, path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        self.rt
            .block_on(async move {
                // Try create; if already exists, write empty to update mtime
                match fs.create(&path).await {
                    Ok(_) => Ok(()),
                    Err(_) => {
                        // File exists, write empty bytes to update timestamp
                        fs.write(&path, &[], 0, WriteFlag::None).await.map(|_| ())
                    }
                }
            })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "touched".to_string());
        Ok(m)
    }

    /// List all mounted plugins.
    fn mounts(&self) -> PyResult<Vec<HashMap<String, String>>> {
        let fs = self.fs.clone();
        let mount_list = self.rt.block_on(async move { fs.list_mounts().await });

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

        let fs = self.fs.clone();
        self.rt
            .block_on(async move { fs.mount(plugin_config).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            format!("mounted {} at {}", fstype, path),
        );
        Ok(m)
    }

    /// Unmount a plugin.
    fn unmount(&self, path: String) -> PyResult<HashMap<String, String>> {
        let fs = self.fs.clone();
        let path_clone = path.clone();
        self.rt
            .block_on(async move { fs.unmount(&path_clone).await })
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

    /// Search for pattern in files (not yet implemented in ragfs).
    #[pyo3(signature = (path, pattern, recursive=false, case_insensitive=false, stream=false, node_limit=None))]
    fn grep(
        &self,
        path: String,
        pattern: String,
        recursive: bool,
        case_insensitive: bool,
        stream: bool,
        node_limit: Option<i32>,
    ) -> PyResult<Py<PyAny>> {
        let _ = (
            path,
            pattern,
            recursive,
            case_insensitive,
            stream,
            node_limit,
        );
        Err(PyRuntimeError::new_err(
            "grep not yet implemented in ragfs-python",
        ))
    }

    /// Calculate file digest (not yet implemented in ragfs).
    #[pyo3(signature = (path, algorithm="xxh3"))]
    fn digest(&self, path: String, algorithm: &str) -> PyResult<HashMap<String, String>> {
        let _ = (path, algorithm);
        Err(PyRuntimeError::new_err(
            "digest not yet implemented in ragfs-python",
        ))
    }
}

/// Python module definition
#[pymodule]
fn ragfs_python(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RAGFSBindingClient>()?;
    Ok(())
}
