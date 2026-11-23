use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyString, PyStringMethods};
use ragfs::core::compile_grep_regex;
use regex::Regex;

#[pyclass(name = "GrepPattern", module = "ragfs_python")]
pub(crate) struct PyGrepPattern {
    regex: Regex,
}

#[pymethods]
impl PyGrepPattern {
    #[new]
    fn new(py: Python<'_>, pattern: &str, case_insensitive: bool) -> PyResult<Self> {
        let regex = py
            .detach(|| compile_grep_regex(pattern, case_insensitive))
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        Ok(Self { regex })
    }

    /// Return zero-based indices of matching lines, up to the requested limit.
    fn matching_lines(
        &self,
        py: Python<'_>,
        lines: Vec<Bound<'_, PyString>>,
        limit: usize,
    ) -> PyResult<Vec<usize>> {
        let lines = lines
            .iter()
            .map(|line| line.to_str())
            .collect::<PyResult<Vec<_>>>()?;
        Ok(py.detach(|| {
            lines
                .into_iter()
                .enumerate()
                .filter_map(|(index, line)| self.regex.is_match(line).then_some(index))
                .take(limit)
                .collect()
        }))
    }
}
