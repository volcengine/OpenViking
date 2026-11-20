#[pymethods]
impl RAGFSBindingClient {
    /// Return an observability snapshot of current lock state.
    #[pyo3(signature = (ctx=None))]
    fn pathlock_observe(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let snapshot = self.run_scoped(py, fs_ctx, move || {
            let mgr = mgr.clone();
            async move { mgr.observe().await }
        });
        Python::attach(|py| {
            let dict = PyDict::new(py);
            dict.set_item("active_locks", snapshot.active_locks)?;
            dict.set_item("waiting_locks", snapshot.waiting_locks)?;
            dict.set_item("stale_locks_removed", snapshot.stale_locks_removed)?;
            let conflicts = PyList::empty(py);
            for c in &snapshot.conflicts {
                let cd = PyDict::new(py);
                cd.set_item("lock_path", &c.lock_path)?;
                cd.set_item("conflicting_owner", &c.conflicting_owner)?;
                cd.set_item("conflicting_kind", format!("{:?}", c.conflicting_kind))?;
                conflicts.append(cd)?;
            }
            dict.set_item("conflicts", conflicts)?;
            Ok(dict.into())
        })
    }

    /// Read all native metrics with no arguments and return a list of flat Python dicts.
    fn metrics(&self, py: Python<'_>) -> PyResult<Py<PyList>> {
        let fs = self.mountable.clone();
        let metrics = py_detach_blocking(py, move || {
            self.rt.block_on(async move { fs.metrics().await })
        })
        .map_err(to_py_err)?;
        let result = PyList::empty(py);
        for metric in &metrics {
            result.append(metric_to_py_dict(py, metric)?)?;
        }
        Ok(result.into())
    }

    /// Get filesystem statistics.
    ///
    /// Args:
    ///     path: Optional mount path to get stats for. If None, get stats for all mounts.
    ///
    /// Returns:
    ///     Statistics data as a dict.
    #[pyo3(signature = (path=None))]
    fn get_stats(&self, py: Python<'_>, path: Option<String>) -> PyResult<Py<PyAny>> {
        let fs = self.mountable.clone();

        if let Some(mount_path) = path {
            // Get stats for a specific mount
            let fs2 = fs.clone();
            let mount_path_clone = mount_path.clone();
            let stats = py_detach_blocking(py, move || {
                self.rt
                    .block_on(async move { fs.get_mount_stats(&mount_path_clone).await })
            })
            .map_err(to_py_err)?;

            let mounts = py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs2.list_mounts().await })
            });

            let plugin_name = mounts
                .into_iter()
                .find(|(p, _)| p == &mount_path)
                .map(|(_, plugin)| plugin)
                .unwrap_or_default();

            Python::attach(|py| {
                let result = PyDict::new(py);
                result.set_item("path", mount_path)?;
                result.set_item("plugin", plugin_name)?;
                let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                result.set_item("stats", stats_dict)?;
                Ok(result.into())
            })
        } else {
            // Get stats for all mounts
            let all_stats = py_detach_blocking(py, move || {
                self.rt.block_on(async move { fs.get_all_stats().await })
            });

            Python::attach(|py| {
                let mounts_list = PyList::empty(py);
                for (path, (plugin, stats)) in all_stats {
                    let mount_dict = PyDict::new(py);
                    mount_dict.set_item("path", path)?;
                    mount_dict.set_item("plugin", plugin)?;
                    let stats_dict = filesystem_stats_to_py_dict(py, &stats)?;
                    mount_dict.set_item("stats", stats_dict)?;
                    mounts_list.append(mount_dict)?;
                }

                let result = PyDict::new(py);
                result.set_item("mounts", mounts_list)?;
                Ok(result.into())
            })
        }
    }
}
