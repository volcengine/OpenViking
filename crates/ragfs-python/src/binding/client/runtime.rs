#[pymethods]
impl RAGFSBindingClient {
    /// Stop mounts requiring explicit cleanup, then close every CacheRuntime.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        let mountable = Arc::clone(&self.mountable);
        let cache_runtimes = self.cache_runtimes.clone();
        let coordination = self.coordination.clone();
        py_detach_blocking(py, || {
            self.rt.block_on(async move {
                let coordination_result = coordination.close().await;
                let mount_result = mountable.shutdown().await;
                let mut cache_result = Ok(());
                for runtime in cache_runtimes.values() {
                    if let Err(error) = runtime.close().await {
                        cache_result = Err(error.to_string());
                    }
                }
                mount_result.map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                coordination_result.map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                cache_result.map_err(PyRuntimeError::new_err)
            })
        })
    }

    /// Return a handle for the named configured cache Runtime.
    #[pyo3(signature = (instance="default".to_string()))]
    fn cache_runtime(
        &self,
        py: Python<'_>,
        instance: String,
    ) -> PyResult<Py<PyCacheRuntimeHandle>> {
        let runtime = self.cache_runtimes.get(&instance).cloned().ok_or_else(|| {
            PyValueError::new_err(format!("unknown cache instance '{instance}'"))
        })?;
        Py::new(py, PyCacheRuntimeHandle::new(instance, runtime))
    }

    /// Check client health.
    fn health(&self) -> PyResult<HashMap<String, String>> {
        let mut m = HashMap::new();
        m.insert("status".to_string(), "healthy".to_string());
        m.insert(
            "git_enabled".to_string(),
            if self.git_service.is_some() {
                "true".into()
            } else {
                "false".into()
            },
        );
        if let Some(b) = &self.git_backend {
            m.insert("git_backend".to_string(), b.clone());
        }
        Ok(m)
    }

}
