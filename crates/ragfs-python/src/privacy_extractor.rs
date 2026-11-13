//! PyO3 access to credential format detectors, separate from file scanning.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use ragfs::privacy_extractor::{PrivacyExtractor, PrivacyRuleLimits};
use std::collections::HashSet;
use std::sync::Arc;

#[pyclass(name = "PrivacyExtractor", module = "ragfs_python")]
pub(crate) struct PyPrivacyExtractor {
    inner: Arc<PrivacyExtractor>,
}

#[pymethods]
impl PyPrivacyExtractor {
    #[new]
    #[pyo3(signature = (custom_rules_json="[]", *, max_rules=None, max_source_bytes=None, max_compiled_bytes=None))]
    fn new(
        py: Python<'_>,
        custom_rules_json: &str,
        max_rules: Option<usize>,
        max_source_bytes: Option<usize>,
        max_compiled_bytes: Option<usize>,
    ) -> PyResult<Self> {
        let defaults = PrivacyRuleLimits::default();
        let limits = PrivacyRuleLimits {
            max_rules: max_rules.unwrap_or(defaults.max_rules),
            max_source_bytes: max_source_bytes.unwrap_or(defaults.max_source_bytes),
            max_compiled_bytes: max_compiled_bytes.unwrap_or(defaults.max_compiled_bytes),
        };
        Ok(Self {
            inner: Arc::new(
                py.detach(|| PrivacyExtractor::with_limits(custom_rules_json, limits))
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
