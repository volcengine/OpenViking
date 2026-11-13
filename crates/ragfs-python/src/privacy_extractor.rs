//! PyO3 access to fixed-rule credential discovery, separate from file scanning.

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

    #[pyo3(signature = (content, field_name=None))]
    fn extract(
        &self,
        py: Python<'_>,
        content: String,
        field_name: Option<String>,
    ) -> PyResult<Vec<(usize, usize, String)>> {
        let inner = self.inner.clone();
        py.detach(move || inner.extract(&content, field_name.as_deref()))
            .map_err(PyValueError::new_err)
    }

    fn is_sensitive_key(&self, key: &str) -> bool {
        self.inner.is_sensitive_key(key)
    }
}
