//! Thin PyO3 wrapper around RAGFS's compiled Privacy matcher.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use ragfs::privacy_matcher::PrivacyMatcher;
use std::collections::HashMap;
use std::sync::Arc;

/// Compiled multi-pattern matcher reused across Python sanitizer calls.
#[pyclass(name = "PrivacyMatcher", module = "ragfs_python")]
pub(crate) struct PyPrivacyMatcher {
    inner: Arc<PrivacyMatcher>,
}

#[pymethods]
impl PyPrivacyMatcher {
    /// Build one immutable matcher from `value -> candidate references`.
    #[new]
    #[pyo3(signature = (references_by_value, ambiguous_placeholder="{{ov_privacy:ambiguous}}"))]
    fn new(
        py: Python<'_>,
        references_by_value: HashMap<String, Vec<String>>,
        ambiguous_placeholder: &str,
    ) -> PyResult<Self> {
        let entries = references_by_value.into_iter().collect();
        let ambiguous_placeholder = ambiguous_placeholder.to_string();
        let matcher = py
            .detach(move || PrivacyMatcher::new(entries, ambiguous_placeholder))
            .map_err(PyValueError::new_err)?;
        Ok(Self {
            inner: Arc::new(matcher),
        })
    }

    /// Return `(content, replacements, ambiguous_references)`.
    ///
    /// Replacement positions are Unicode character offsets, matching Python
    /// string slicing rather than UTF-8 byte offsets. The GIL is released while
    /// RAGFS scans and rewrites the input.
    fn sanitize(
        &self,
        py: Python<'_>,
        content: String,
    ) -> (String, Vec<(usize, usize, String)>, Vec<Vec<String>>) {
        let matcher = self.inner.clone();
        let result = py.detach(move || matcher.sanitize(&content));
        let replacements = result
            .replacements
            .into_iter()
            .map(|replacement| (replacement.start, replacement.end, replacement.reference))
            .collect();
        (result.content, replacements, result.ambiguous_references)
    }

    /// Return the regular result plus whether the replacement cap was exceeded.
    fn sanitize_limited(
        &self,
        py: Python<'_>,
        content: String,
        max_replacements: usize,
    ) -> (String, Vec<(usize, usize, String)>, Vec<Vec<String>>, bool) {
        let matcher = self.inner.clone();
        let (result, limit_exceeded) =
            py.detach(move || matcher.sanitize_limited(&content, max_replacements));
        let replacements = result
            .replacements
            .into_iter()
            .map(|replacement| (replacement.start, replacement.end, replacement.reference))
            .collect();
        (
            result.content,
            replacements,
            result.ambiguous_references,
            limit_exceeded,
        )
    }
}
