//! PyO3 wrapper for the user-controlled GLOBAL access policy.

use std::sync::Arc;

use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes, PyString};
use ragfs::global_policy::{
    validate_policy_path, GlobalPolicy, GlobalPolicyError as CoreGlobalPolicyError, PolicySnapshot,
    PolicyStatus,
};
use ragfs::write_hook::WriteHookError;

create_exception!(ragfs_python, GlobalPolicyError, PyValueError);
create_exception!(
    ragfs_python,
    UnsupportedGlobalPolicySchemaError,
    GlobalPolicyError
);

/// Immutable parsed policy exposed to Python as ``UserGlobalPolicy``.
///
/// A file-backed instance reads its source once at construction.
/// Reuse, expiry and eviction are managed by the caller.
#[pyclass(name = "UserGlobalPolicy", module = "ragfs_python", frozen)]
pub(crate) struct PyUserGlobalPolicy {
    snapshot: PolicySnapshot,
}

#[pymethods]
impl PyUserGlobalPolicy {
    /// Parse a complete policy document from ``str`` or UTF-8 ``bytes``.
    #[new]
    fn new(raw: &Bound<'_, PyAny>) -> PyResult<Self> {
        Self::parse(raw)
    }

    /// Parse a complete policy document from ``str`` or UTF-8 ``bytes``.
    #[staticmethod]
    fn parse(raw: &Bound<'_, PyAny>) -> PyResult<Self> {
        let text = extract_policy_text(raw)?;
        GlobalPolicy::parse(&text)
            .map(|policy| Self {
                snapshot: PolicySnapshot::valid(Arc::new(policy)),
            })
            .map_err(map_error)
    }

    /// Read ``fs.read(path)`` once and capture the result as a snapshot.
    #[staticmethod]
    fn from_file(py: Python<'_>, fs: Py<PyAny>, path: String) -> PyResult<Self> {
        if path.is_empty() {
            return Err(GlobalPolicyError::new_err("policy path must be non-empty"));
        }
        let read = fs
            .bind(py)
            .getattr("read")
            .map_err(|_| GlobalPolicyError::new_err("filesystem object must expose read(path)"))?;
        if !read.is_callable() {
            return Err(GlobalPolicyError::new_err(
                "filesystem read must be callable",
            ));
        }
        Ok(Self {
            snapshot: snapshot_from_source(read_policy_file(py, &fs, &path)),
        })
    }

    /// Validate one canonical, literal path relative to a user root.
    #[staticmethod]
    fn validate_path(path: &Bound<'_, PyAny>) -> PyResult<String> {
        let path = extract_string(path, "policy path must be a non-empty string")?;
        validate_policy_path(&path)
            .map(str::to_owned)
            .map_err(map_error)
    }

    /// Parsed schema version. Only v1 is accepted today.
    #[getter]
    fn schema_version(&self, py: Python<'_>) -> PyResult<u32> {
        Ok(self.current_policy(py)?.schema_version())
    }

    /// Policy default as ``allow`` or ``deny``.
    #[getter]
    fn default(&self, py: Python<'_>) -> PyResult<String> {
        Ok(self.current_policy(py)?.default_str().to_string())
    }

    /// Rules as ``(effect, path, recursive)`` tuples in document order.
    #[getter]
    fn rules(&self, py: Python<'_>) -> PyResult<Vec<(String, String, bool)>> {
        Ok(self
            .current_policy(py)?
            .rules()
            .iter()
            .map(|rule| {
                (
                    rule.effect.as_str().to_string(),
                    rule.path.clone(),
                    rule.recursive,
                )
            })
            .collect())
    }

    /// Markdown body after the closing frontmatter delimiter.
    #[getter]
    fn body(&self, py: Python<'_>) -> PyResult<String> {
        Ok(self.current_policy(py)?.body().to_string())
    }

    /// SHA-256 of the exact source text.
    fn policy_hash(&self, py: Python<'_>) -> PyResult<String> {
        Ok(self.current_policy(py)?.policy_hash().to_string())
    }

    /// Evaluate ordinary-content rules for one owner-relative path.
    ///
    /// A missing, unreadable or invalid policy denies access.
    fn allows_path(&self, path: &str) -> bool {
        self.snapshot.allows_path(path)
    }

    /// Snapshot status: ``valid``, ``missing``, ``unreadable``, ``invalid``
    /// or ``unsupported_schema``.
    #[getter]
    fn status(&self) -> String {
        self.snapshot.status().as_str().to_string()
    }

    /// Diagnostic message for the current non-valid status.
    #[getter]
    fn last_error(&self) -> Option<String> {
        self.snapshot.status().message()
    }
}

impl PyUserGlobalPolicy {
    fn current_policy(&self, py: Python<'_>) -> PyResult<Arc<GlobalPolicy>> {
        match self.snapshot.policy_arc() {
            Some(policy) => Ok(policy),
            None => Err(status_error(py, self.snapshot.status())),
        }
    }
}

fn snapshot_from_source(source: Result<Option<String>, String>) -> PolicySnapshot {
    match source {
        Ok(Some(text)) => PolicySnapshot::from_text(&text),
        Ok(None) => PolicySnapshot::missing(),
        Err(reason) => PolicySnapshot::unreadable(reason),
    }
}

fn extract_policy_text(raw: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(text) = raw.cast::<PyString>() {
        return text
            .to_str()
            .map(str::to_owned)
            .map_err(|_| GlobalPolicyError::new_err("policy content must be UTF-8 text"));
    }
    if let Ok(bytes) = raw.cast::<PyBytes>() {
        return String::from_utf8(bytes.as_bytes().to_vec())
            .map_err(|_| GlobalPolicyError::new_err("policy content must be UTF-8 text"));
    }
    Err(GlobalPolicyError::new_err(
        "policy content must be UTF-8 text",
    ))
}

fn extract_string(value: &Bound<'_, PyAny>, message: &'static str) -> PyResult<String> {
    match value.cast::<PyString>() {
        Ok(text) => text
            .to_str()
            .map(str::to_owned)
            .map_err(|_| GlobalPolicyError::new_err(message)),
        Err(_) => Err(GlobalPolicyError::new_err(message)),
    }
}

fn read_policy_file(py: Python<'_>, fs: &Py<PyAny>, path: &str) -> Result<Option<String>, String> {
    match fs.bind(py).call_method1("read", (path,)) {
        Ok(value) => optional_policy_text(&value),
        Err(error) if is_not_found(py, &error) => Ok(None),
        Err(error) => Err(format!("failed to read {path}: {error}")),
    }
}

fn optional_policy_text(value: &Bound<'_, PyAny>) -> Result<Option<String>, String> {
    if value.is_none() {
        return Ok(None);
    }
    if let Ok(text) = value.cast::<PyString>() {
        return text
            .to_str()
            .map(|text| Some(text.to_owned()))
            .map_err(|_| "policy content must be UTF-8 text".to_string());
    }
    if let Ok(bytes) = value.cast::<PyBytes>() {
        return String::from_utf8(bytes.as_bytes().to_vec())
            .map(Some)
            .map_err(|_| "policy content must be UTF-8 text".to_string());
    }
    Err("policy source must return str, bytes or None".to_string())
}

fn is_not_found(py: Python<'_>, error: &PyErr) -> bool {
    error
        .get_type(py)
        .name()
        .ok()
        .and_then(|name| name.to_str().ok().map(str::to_owned))
        .is_some_and(|name| name.contains("NotFound"))
}

fn status_error(py: Python<'_>, status: &PolicyStatus) -> PyErr {
    if let PolicyStatus::Invalid(error) = status {
        return map_error(error.clone());
    }
    let (code, message) = match status {
        PolicyStatus::Missing => (
            "GLOBAL_POLICY_MISSING",
            "GLOBAL_POLICY_MISSING: policy file not found".to_string(),
        ),
        PolicyStatus::Unreadable(reason) => (
            "GLOBAL_POLICY_UNAVAILABLE",
            format!("GLOBAL_POLICY_UNAVAILABLE: {reason}"),
        ),
        PolicyStatus::Valid => ("GLOBAL_POLICY_INVALID", "policy is valid".to_string()),
        PolicyStatus::Invalid(_) => unreachable!(),
    };
    let error = GlobalPolicyError::new_err(message);
    let _ = error.value(py).setattr("code", code);
    error
}

fn map_error(error: CoreGlobalPolicyError) -> PyErr {
    let message = error.to_string();
    match error {
        CoreGlobalPolicyError::UnsupportedSchema { .. } => {
            UnsupportedGlobalPolicySchemaError::new_err(message)
        }
        CoreGlobalPolicyError::Invalid(_) => GlobalPolicyError::new_err(message),
    }
}

/// Build the Python exception for a write rejected by a registered write hook.
///
/// Global-policy codes keep their dedicated exception types and stable ``code``
/// attribute. Unknown hook codes fall back to ``AGFSInternalError`` so a future
/// hook can reject a write without adding a Python-specific type.
pub(crate) fn write_hook_error(py: Python<'_>, error: WriteHookError) -> PyErr {
    let code = error.code();
    let message = format!("{code}: {}", error.message());
    let exception = match code {
        CoreGlobalPolicyError::INVALID_CODE => GlobalPolicyError::new_err(message),
        CoreGlobalPolicyError::UNSUPPORTED_SCHEMA_CODE => {
            UnsupportedGlobalPolicySchemaError::new_err(message)
        }
        _ => return crate::new_py_err("AGFSInternalError", message),
    };
    let _ = exception.value(py).setattr("code", code);
    exception
}
