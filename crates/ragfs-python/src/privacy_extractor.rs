//! PyO3 access to credential format detectors, separate from file scanning.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use ragfs::privacy_extractor::PrivacyExtractor;
use std::collections::HashSet;
use std::sync::Arc;

#[pyclass(name = "PrivacyExtractor", module = "ragfs_python")]
pub(crate) struct PyPrivacyExtractor {
    inner: Arc<PrivacyExtractor>,
}

#[pymethods]
impl PyPrivacyExtractor {
    #[new]
    #[pyo3(signature = (custom_rules_json="[]"))]
    fn new(custom_rules_json: &str) -> PyResult<Self> {
        Ok(Self {
            inner: Arc::new(
                PrivacyExtractor::with_custom_rules(custom_rules_json)
                    .map_err(PyValueError::new_err)?,
            ),
        })
    }

    #[pyo3(signature = (content, never_mask=None))]
    fn extract(
        &self,
        py: Python<'_>,
        content: String,
        never_mask: Option<Vec<String>>,
    ) -> PyResult<Vec<(usize, usize, String)>> {
        let inner = self.inner.clone();
        let never_mask: HashSet<_> = never_mask.unwrap_or_default().into_iter().collect();
        py.detach(move || inner.extract_excluding(&content, &never_mask))
            .map_err(PyValueError::new_err)
    }
}
