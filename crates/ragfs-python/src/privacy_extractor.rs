//! PyO3 access to credential format detectors, separate from file scanning.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use ragfs::privacy_extractor::PrivacyExtractor;
use std::sync::Arc;

#[pyclass(name = "PrivacyExtractor", module = "ragfs_python")]
pub(crate) struct PyPrivacyExtractor {
    inner: Arc<PrivacyExtractor>,
}

#[pymethods]
impl PyPrivacyExtractor {
    #[new]
    fn new() -> PyResult<Self> {
        Ok(Self {
            inner: Arc::new(PrivacyExtractor::new().map_err(PyValueError::new_err)?),
        })
    }

    fn extract(&self, py: Python<'_>, content: String) -> PyResult<Vec<(usize, usize, String)>> {
        let inner = self.inner.clone();
        py.detach(move || inner.extract(&content))
            .map_err(PyValueError::new_err)
    }
}
