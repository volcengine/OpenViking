#[pymethods]
impl RAGFSBindingClient {
    /// Commit a snapshot of the account's tree.
    #[pyo3(signature = (**kwargs))]
    fn git_commit(
        &self,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_commit_request(kw)?;
        let resp = py_detach_blocking(py, move || self.rt.block_on(svc.commit(req)))
            .map_err(|e| git::map_git_error(py, e))?;
        git::commit_response_to_pydict(py, resp)
    }

    /// Restore a project_dir subtree to the state at source_commit.
    #[pyo3(signature = (*, ctx=None, **kwargs))]
    fn git_restore(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_restore_request(kw)?;
        let resp = self
            .run_scoped(py, build_fs_context(ctx), || svc.restore(req))
            .map_err(|e| git::map_git_error(py, e))?;
        git::restore_response_to_pydict(py, resp)
    }

    /// Read a commit's metadata or a blob's bytes at a path.
    #[pyo3(signature = (**kwargs))]
    fn git_show(&self, py: Python<'_>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_show_request(kw)?;
        let max_blob_bytes = git::parse_show_max_blob_bytes(kw)?;
        let resp = py_detach_blocking(py, move || {
            self.rt.block_on(svc.show_with_limit(req, max_blob_bytes))
        })
        .map_err(|e| git::map_git_error(py, e))?;
        git::show_response_to_pydict(py, resp)
    }

    /// Check the tree entry mode at a commit OID without reading the blob.
    #[pyo3(signature = (*, account, commit_oid, path))]
    fn git_path_is_file(
        &self,
        py: Python<'_>,
        account: &str,
        commit_oid: &str,
        path: &str,
    ) -> PyResult<bool> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let oid = gix_hash::ObjectId::from_hex(commit_oid.as_bytes())
            .map_err(|_| PyValueError::new_err("commit_oid must be a full commit OID"))?;
        py_detach_blocking(py, move || {
            self.rt.block_on(svc.path_is_file(account, &oid, path))
        })
        .map_err(|e| git::map_git_error(py, e))
    }

    /// Build a bounded unified text diff outside the Python interpreter.
    #[pyo3(signature = (**kwargs))]
    fn git_diff_text(
        &self,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<String> {
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_diff_text_request(kw)?;
        let result = py_detach_blocking(py, move || {
            git::build_bounded_unified_diff(
                &req.before,
                &req.after,
                &req.fromfile,
                &req.tofile,
                req.timeout_ms,
                req.max_output_bytes,
            )
        });
        result.map_err(|err| match err {
            git::BoundedDiffError::OutputTooLarge { limit } => git::new_py_err_pub(
                py,
                "AGFSResourceExhaustedError",
                format!("snapshot diff output size limit exceeded ({limit} bytes)"),
            ),
            git::BoundedDiffError::Render(message) => {
                git::new_py_err_pub(py, "AGFSInternalError", message)
            }
        })
    }

    /// List changed files under directories between two commits.
    #[pyo3(signature = (**kwargs))]
    fn git_diff_paths(
        &self,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Vec<(String, String)>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_diff_paths_request(kw)?;
        let resp = py_detach_blocking(py, move || {
            self.rt.block_on(svc.diff_paths(
                &req.account,
                req.base.as_deref(),
                &req.head,
                &req.paths,
            ))
        })
        .map_err(|e| git::map_git_error(py, e))?;
        Ok(resp
            .into_iter()
            .map(|(path, change)| (path, change.to_string()))
            .collect())
    }

    /// Walk snapshot history, optionally filtered to commits touching paths.
    #[pyo3(signature = (**kwargs))]
    fn git_log(&self, py: Python<'_>, kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<Py<PyAny>> {
        let svc = self.git_service.clone().ok_or_else(|| {
            git::new_py_err_pub(py, "AGFSNotSupportedError", "git feature disabled".into())
        })?;
        let empty = PyDict::new(py);
        let kw = kwargs.unwrap_or(&empty);
        let req = git::parse_log_request(kw)?;
        let resp = py_detach_blocking(py, move || self.rt.block_on(svc.log(req)))
            .map_err(|e| git::map_git_error(py, e))?;
        git::log_entries_to_pylist(py, resp)
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

}
