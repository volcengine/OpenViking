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
        ragfs::core::Error::QuotaExceeded(_) => new_py_err("AGFSResourceExhaustedError", msg),
        ragfs::core::Error::RateLimited(_) => new_py_err("AGFSNetworkError", msg),
        ragfs::core::Error::InvalidPath(_) => new_py_err("AGFSInvalidPathError", msg),
        ragfs::core::Error::NotADirectory(_) => new_py_err("AGFSNotADirectoryError", msg),
        ragfs::core::Error::IsADirectory(_) => new_py_err("AGFSIsADirectoryError", msg),
        ragfs::core::Error::DirectoryNotEmpty(_) => new_py_err("AGFSDirectoryNotEmptyError", msg),
        ragfs::core::Error::InvalidOperation(_) => new_py_err("AGFSInvalidOperationError", msg),
        ragfs::core::Error::NotSupported(_) => new_py_err("AGFSNotSupportedError", msg),
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
        ragfs::core::Error::WouldBlock(_) => new_py_err("AGFSTimeoutError", msg),
        ragfs::core::Error::PathLock(error) => pathlock_err_to_py(error),
        ragfs::core::Error::SyncWriteQuorum { .. } => new_py_err("AGFSInternalError", msg),
        ragfs::core::Error::ContextMissing(_) => new_py_err("AGFSInternalError", msg),
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
/// {"matches": [{"file": str, "line": int, "content": str, ...}, ...], "count": int}
fn grep_result_to_py_dict(py: Python<'_>, result: &GrepResult) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);

    let matches_list = PyList::empty(py);
    for m in &result.matches {
        let match_dict = PyDict::new(py);
        match_dict.set_item("file", &m.file)?;
        match_dict.set_item("line", m.line)?;
        match_dict.set_item("content", &m.content)?;
        if let Some(lines) = &m.before_context {
            let context = PyList::empty(py);
            for line in lines {
                let item = PyDict::new(py);
                item.set_item("line", line.line)?;
                item.set_item("content", &line.content)?;
                context.append(item)?;
            }
            match_dict.set_item("before_context", context)?;
        }
        if let Some(lines) = &m.after_context {
            let context = PyList::empty(py);
            for line in lines {
                let item = PyDict::new(py);
                item.set_item("line", line.line)?;
                item.set_item("content", &line.content)?;
                context.append(item)?;
            }
            match_dict.set_item("after_context", context)?;
        }
        matches_list.append(match_dict)?;
    }

    dict.set_item("matches", matches_list)?;
    dict.set_item("count", result.count)?;

    Ok(dict.into())
}

/// Convert the supplied native statistics into the legacy microsecond Python dict.
fn operation_stats_to_py_dict(py: Python<'_>, stats: &OperationStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("count", stats.count)?;
    dict.set_item("total_time_us", stats.total_time_ns / 1_000)?;
    dict.set_item(
        "min_time_us",
        if stats.count == 0 {
            u64::MAX
        } else {
            stats.min_time_ns / 1_000
        },
    )?;
    dict.set_item("max_time_us", stats.max_time_ns / 1_000)?;
    dict.set_item("avg_time_us", stats.avg_time_ns() / 1_000.0)?;
    Ok(dict.into())
}

/// Convert the supplied native metric into a flat Python dict, preserving integer values.
fn metric_to_py_dict(py: Python<'_>, metric: &RagfsMetric) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("name", &metric.name)?;
    dict.set_item("labels", &metric.labels)?;
    match &metric.value {
        RagfsMetricValue::Counter { value, scale } => {
            dict.set_item("type", "counter")?;
            dict.set_item("value", value)?;
            dict.set_item("scale", scale)?;
        }
        RagfsMetricValue::Gauge(value) => {
            dict.set_item("type", "gauge")?;
            dict.set_item("value", value)?;
        }
        RagfsMetricValue::Histogram {
            bucket_bounds,
            bucket_counts,
            count,
            sum,
            scale,
        } => {
            dict.set_item("type", "histogram")?;
            dict.set_item("bucket_bounds", bucket_bounds)?;
            dict.set_item("bucket_counts", bucket_counts)?;
            dict.set_item("count", count)?;
            dict.set_item("sum", sum)?;
            dict.set_item("scale", scale)?;
        }
    }
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

/// Convert a Python dict to HashMap<String, ConfigValue>, with recursive nesting support.
fn py_dict_to_config(dict: &Bound<'_, PyDict>) -> PyResult<HashMap<String, ConfigValue>> {
    let mut params = HashMap::new();
    for (k, v) in dict.iter() {
        let key: String = k.extract()?;
        let value = py_any_to_config_value(&v)?;
        params.insert(key, value);
    }
    Ok(params)
}

/// Convert a single Python value to ConfigValue, recursing into dicts/lists.
fn py_any_to_config_value(v: &Bound<'_, PyAny>) -> PyResult<ConfigValue> {
    if let Ok(s) = v.extract::<String>() {
        Ok(ConfigValue::String(s))
    } else if let Ok(b) = v.extract::<bool>() {
        Ok(ConfigValue::Bool(b))
    } else if let Ok(i) = v.extract::<i64>() {
        Ok(ConfigValue::Int(i))
    } else if let Ok(list) = v.downcast::<PyList>() {
        let mut strings = Vec::new();
        let mut has_nested = false;
        for item in list.iter() {
            if let Ok(s) = item.extract::<String>() {
                strings.push(s);
            } else {
                has_nested = true;
                break;
            }
        }
        if has_nested || strings.is_empty() {
            // Convert to serde_json::Value for complex lists
            let json_val = py_to_json_value(v)?;
            Ok(ConfigValue::Json(json_val))
        } else {
            Ok(ConfigValue::StringList(strings))
        }
    } else if v.is_instance_of::<PyDict>() {
        let json_val = py_to_json_value(v)?;
        Ok(ConfigValue::Json(json_val))
    } else {
        // Fallback: try string representation
        Ok(ConfigValue::String(v.str()?.to_string()))
    }
}

/// Convert a Python value to serde_json::Value recursively.
fn py_to_json_value(v: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    if v.is_none() {
        Ok(serde_json::Value::Null)
    } else if let Ok(b) = v.extract::<bool>() {
        Ok(serde_json::Value::Bool(b))
    } else if let Ok(i) = v.extract::<i64>() {
        Ok(serde_json::Value::Number(i.into()))
    } else if let Ok(f) = v.extract::<f64>() {
        if let Some(n) = serde_json::Number::from_f64(f) {
            Ok(serde_json::Value::Number(n))
        } else {
            Ok(serde_json::Value::String(f.to_string()))
        }
    } else if let Ok(s) = v.extract::<String>() {
        Ok(serde_json::Value::String(s))
    } else if let Ok(list) = v.downcast::<PyList>() {
        let mut arr = Vec::new();
        for item in list.iter() {
            arr.push(py_to_json_value(&item)?);
        }
        Ok(serde_json::Value::Array(arr))
    } else if let Ok(dict) = v.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (k, val) in dict.iter() {
            let key: String = k.extract()?;
            map.insert(key, py_to_json_value(&val)?);
        }
        Ok(serde_json::Value::Object(map))
    } else {
        Ok(serde_json::Value::String(v.str()?.to_string()))
    }
}

/// Build an immutable `FsContext` from an optional Python `{"account_id": str}` dict.
///
/// A context is always constructed (even when the dict is absent or lacks `account_id`), so the
/// "every ragfs call carries a ctx" invariant holds. Missing `account_id` is represented as an
/// empty field; `FsContextView::account_id()` treats it as absent, so encrypted content operations
/// fail fast instead of using an accidental empty tenant key.
///
/// When the dict contains `"disable_auto_pathlock": "true"`, the `PathLockWrappedFS` auto-lock
/// layer is suppressed so callers that already hold a pathlock lease can safely delegate to the
/// inner filesystem without double-locking.
///
/// When the dict contains `"bypass_cache": "true"`, plugin-local metadata caches (e.g. the S3FS
/// stat cache) are bypassed so the operation observes fresh backend state.
fn build_fs_context(ctx: Option<HashMap<String, String>>) -> FsContext {
    let mut account_id = String::new();
    let mut disable_auto_pathlock = false;
    let mut lease_ref: Option<String> = None;
    let mut bypass_cache = false;
    if let Some(m) = &ctx {
        account_id = m.get("account_id").cloned().unwrap_or_default();
        disable_auto_pathlock = m
            .get("disable_auto_pathlock")
            .map(|s| s == "true")
            .unwrap_or(false);
        lease_ref = m.get("lease_ref").cloned();
        bypass_cache = m.get("bypass_cache").map(|s| s == "true").unwrap_or(false);
    }
    let pathlock = if disable_auto_pathlock || lease_ref.is_some() {
        Some(PathLockContext {
            lease_ref,
            disable_auto_pathlock,
        })
    } else {
        None
    };
    Arc::new(
        FsContextInner::with_pathlock(account_id, pathlock.unwrap_or_default())
            .with_bypass_cache(bypass_cache),
    )
}

/// Convert an OwnedPathLockLease to a Python dict.
fn owned_lease_to_py_dict(py: Python<'_>, lease: &OwnedPathLockLease) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("lease_ref", &lease.lease.lease_ref)?;
    dict.set_item("owner_id", &lease.lease.owner_id)?;
    dict.set_item("lock_paths", &lease.lease.lock_paths)?;
    dict.set_item("ownership_ref", &lease.ownership_ref)?;
    dict.set_item("owned", true)?;
    Ok(dict.into())
}

/// Convert a BorrowedPathLockLease to a Python dict.
fn borrowed_lease_to_py_dict(py: Python<'_>, lease: &BorrowedPathLockLease) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    dict.set_item("lease_ref", &lease.lease.lease_ref)?;
    dict.set_item("owner_id", &lease.lease.owner_id)?;
    dict.set_item("lock_paths", &lease.lease.lock_paths)?;
    dict.set_item("owned", false)?;
    Ok(dict.into())
}

/// Require an owned marker and return its opaque lease and lifecycle capability refs.
fn require_owned_lease_ref(
    py: Python<'_>,
    ref_dict: &HashMap<String, Py<PyAny>>,
) -> PyResult<(String, String)> {
    let owned: bool = ref_dict
        .get("owned")
        .map(|v| v.extract(py))
        .transpose()?
        .unwrap_or(false);
    if !owned {
        return Err(PyValueError::new_err(
            "lease ref is not owned; release/refresh/handoff require an owned lease",
        ));
    }
    let lease_ref: String = ref_dict
        .get("lease_ref")
        .ok_or_else(|| PyValueError::new_err("owned_lease_ref.lease_ref required"))?
        .extract(py)?;
    let ownership_ref: String = ref_dict
        .get("ownership_ref")
        .ok_or_else(|| PyValueError::new_err("owned_lease_ref.ownership_ref required"))?
        .extract(py)?;
    if lease_ref.is_empty() || ownership_ref.is_empty() {
        return Err(PyValueError::new_err(
            "owned lease and ownership refs must not be empty",
        ));
    }
    Ok((lease_ref, ownership_ref))
}

/// Extract an opaque PathLock lease ref without granting lifecycle permissions.
fn extract_lease_ref(py: Python<'_>, lease_ref: &Py<PyAny>) -> PyResult<String> {
    if let Ok(raw) = lease_ref.extract::<String>(py) {
        if !raw.is_empty() {
            return Ok(raw);
        }
    }
    let ref_dict: HashMap<String, Py<PyAny>> = lease_ref.extract(py)?;
    ref_dict
        .get("lease_ref")
        .ok_or_else(|| PyValueError::new_err("lease_ref.lease_ref required"))?
        .extract(py)
}

/// Extract an owned PathLock lease and lifecycle capability from a typed dictionary.
fn extract_owned_lease_ref(py: Python<'_>, lease_ref: &Py<PyAny>) -> PyResult<(String, String)> {
    let ref_dict: HashMap<String, Py<PyAny>> = lease_ref.extract(py)?;
    require_owned_lease_ref(py, &ref_dict)
}

/// Extract an optional owned lease capability used for reentrant acquisition.
fn extract_optional_owned_lease_ref(
    py: Python<'_>,
    lease_ref: Option<&Py<PyAny>>,
) -> PyResult<Option<(String, String)>> {
    lease_ref
        .map(|value| extract_owned_lease_ref(py, value))
        .transpose()
}

/// Convert a PathLockHandoffRef to a Python dict.
fn handoff_ref_to_py_dict(py: Python<'_>, handoff: &PathLockHandoffRef) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    if let Some(holder_id) = &handoff.holder_id {
        dict.set_item("holder_id", holder_id)?;
    }
    if let Some(lease_ref) = &handoff.lease_ref {
        dict.set_item("lease_ref", lease_ref)?;
    }
    dict.set_item("owner_id", &handoff.owner_id)?;
    if let Some(mode) = handoff.handle_mode {
        dict.set_item(
            "handle_mode",
            match mode {
                PathLockHandleMode::LockPath => "lock_path",
                PathLockHandleMode::LogicalPath => "logical_path",
            },
        )?;
    }
    dict.set_item("lock_paths", &handoff.lock_paths)?;
    let covered = PyList::empty(py);
    for req in &handoff.covered_paths {
        let item = PyDict::new(py);
        item.set_item("path", &req.path)?;
        item.set_item(
            "kind",
            match req.kind {
                PathLockKind::Exact => "exact",
                PathLockKind::Tree => "tree",
            },
        )?;
        covered.append(item)?;
    }
    dict.set_item("covered_paths", covered)?;
    Ok(dict.into())
}
