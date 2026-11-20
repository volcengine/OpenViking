//! Python bindings for RAGFS - Rust AGFS filesystem
//!
//! Provides `RAGFSBindingClient`, a PyO3 native class that is API-compatible
//! with the existing Go-based `AGFSBindingClient`. This embeds the ragfs
//! filesystem engine directly in the Python process (no HTTP server needed).

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyType};
use pyo3::wrap_pyfunction;
use cache_runtime_handle::PyCacheRuntimeHandle;
use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::field::{Field, Visit};
use tracing::Level;
use tracing_appender::non_blocking::WorkerGuard;
use tracing_subscriber::fmt::format::Writer;
use tracing_subscriber::fmt::writer::BoxMakeWriter;
use tracing_subscriber::fmt::{FmtContext, FormatEvent, FormatFields};
use tracing_subscriber::registry::LookupSpan;

/// Cached reference to the Python `openviking.storage.errors.LockAcquisitionError` class,
/// imported at module init so that native and Python code share the same exception type.
static LOCK_ACQUISITION_ERROR_TYPE: OnceLock<Py<PyType>> = OnceLock::new();
static HANDOFF_RECOVERY_ERROR_TYPE: OnceLock<Py<PyType>> = OnceLock::new();
static RUST_TRACING_CONFIG: OnceLock<(String, String)> = OnceLock::new();
static RUST_TRACING_INIT_RESULT: OnceLock<Result<(), String>> = OnceLock::new();
static RUST_TRACING_GUARD: OnceLock<WorkerGuard> = OnceLock::new();
static RUST_TRACING_FILE_STATE: OnceLock<TracingFileState> = OnceLock::new();

include!("binding/logging.rs");
include!("binding/errors.rs");
include!("binding/config.rs");

mod cache_runtime_handle;
mod git;
mod global_policy;
mod privacy;
mod privacy_extractor;

include!("binding/convert.rs");
include!("binding/client.rs");

/// Python module definition
#[pymodule]
fn ragfs_python(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RAGFSBindingClient>()?;
    m.add_class::<PyCacheRuntimeHandle>()?;
    m.add_class::<global_policy::PyUserGlobalPolicy>()?;
    m.add_class::<privacy::PyPrivacyMatcher>()?;
    m.add_class::<privacy_extractor::PyPrivacyExtractor>()?;
    let global_policy_error = m.py().get_type::<global_policy::GlobalPolicyError>();
    global_policy_error.setattr(
        "code",
        ragfs::global_policy::GlobalPolicyError::INVALID_CODE,
    )?;
    m.add("GlobalPolicyError", global_policy_error)?;
    let unsupported_global_policy_error = m
        .py()
        .get_type::<global_policy::UnsupportedGlobalPolicySchemaError>();
    unsupported_global_policy_error.setattr(
        "code",
        ragfs::global_policy::GlobalPolicyError::UNSUPPORTED_SCHEMA_CODE,
    )?;
    m.add(
        "UnsupportedGlobalPolicySchemaError",
        unsupported_global_policy_error,
    )?;
    m.add_function(wrap_pyfunction!(reopen_tracing_file, m)?)?;

    // Import the Python business exception so native code raises the same type
    // that `openviking.storage.errors` defines, enabling `except LockAcquisitionError`.
    let py = m.py();
    let errors_mod = py.import("openviking.storage.errors")?;
    let exc_type: Py<PyType> = errors_mod.getattr("LockAcquisitionError")?.extract()?;
    LOCK_ACQUISITION_ERROR_TYPE
        .set(exc_type)
        .map_err(|_| PyRuntimeError::new_err("LockAcquisitionError already initialized"))?;
    HANDOFF_RECOVERY_ERROR_TYPE
        .set(errors_mod.getattr("HandoffRecoveryRequired")?.extract()?)
        .map_err(|_| PyRuntimeError::new_err("HandoffRecoveryRequired already initialized"))?;

    Ok(())
}

#[cfg(test)]
#[path = "tests/mod.rs"]
mod tests;
