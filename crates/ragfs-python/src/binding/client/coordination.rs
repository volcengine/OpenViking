#[pymethods]
impl RAGFSBindingClient {
    // ── Coordination API ──

    /// Validate that the configured coordination service is available.
    fn leader_validate_async<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.validate_async().await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))
        })
    }

    /// Try to acquire a leader term and return an opaque reference.
    #[pyo3(signature = (key, holder_id, ttl_secs))]
    fn leader_try_acquire_async<'py>(
        &self,
        py: Python<'py>,
        key: String,
        holder_id: String,
        ttl_secs: f64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let ttl = validate_leader_ttl_secs(ttl_secs)?;
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.try_acquire(&key, &holder_id, ttl).await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))
        })
    }

    /// Renew an opaque leader lease. False means ownership has been lost.
    #[pyo3(signature = (lease_ref, ttl_secs))]
    fn leader_renew_async<'py>(
        &self,
        py: Python<'py>,
        lease_ref: String,
        ttl_secs: f64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let ttl = validate_leader_ttl_secs(ttl_secs)?;
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.renew(&lease_ref, ttl).await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))
        })
    }

    /// Release an opaque leader lease if it is still owned.
    #[pyo3(signature = (lease_ref))]
    fn leader_release_async<'py>(
        &self,
        py: Python<'py>,
        lease_ref: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.release(&lease_ref).await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))
        })
    }

    /// Reserve a revision without changing the latest published work.
    fn revision_allocate_async<'py>(
        &self,
        py: Python<'py>,
        key: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.allocate_revision(&key).await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))
        })
    }

    /// Publish persisted work, taking the maximum published revision.
    fn revision_publish_async<'py>(
        &self,
        py: Python<'py>,
        key: String,
        revision: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.publish_revision(&key, revision).await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))
        })
    }

    /// Read the latest published revision, or zero for an unseen key.
    fn revision_latest_async<'py>(
        &self,
        py: Python<'py>,
        key: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let coordination = self.coordination.clone();
        let task = self
            .rt
            .spawn(async move { coordination.latest_revision(&key).await });
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            task.await
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))
        })
    }

    // ── PathLock API ──

    /// Return the actual provider's handle mode without performing storage I/O.
    fn pathlock_handle_mode(&self) -> &'static str {
        match self.pathlock_manager.handle_mode() {
            PathLockHandleMode::LockPath => "lock_path",
            PathLockHandleMode::LogicalPath => "logical_path",
        }
    }

    /// Acquire an exact lock on a single path.
    #[pyo3(signature = (ctx, path, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_exact(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        path: String,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner_capability = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let path = path.clone();
                let capability = owner_capability.clone();
                async move {
                    let capability = capability.as_ref().map(|(lease_ref, ownership_ref)| {
                        (lease_ref.as_str(), ownership_ref.as_str())
                    });
                    mgr.acquire_exact(&path, timeout, capability).await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Acquire exact locks on multiple paths.
    #[pyo3(signature = (ctx, paths, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_exact_batch(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        paths: Vec<String>,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner_capability = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let paths = paths.clone();
                let capability = owner_capability.clone();
                async move {
                    let capability = capability.as_ref().map(|(lease_ref, ownership_ref)| {
                        (lease_ref.as_str(), ownership_ref.as_str())
                    });
                    mgr.acquire_exact_batch(&paths, timeout, capability).await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Acquire a tree lock on a single path.
    #[pyo3(signature = (ctx, path, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_tree(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        path: String,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner_capability = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let path = path.clone();
                let capability = owner_capability.clone();
                async move {
                    let capability = capability.as_ref().map(|(lease_ref, ownership_ref)| {
                        (lease_ref.as_str(), ownership_ref.as_str())
                    });
                    mgr.acquire_tree(&path, timeout, capability).await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Acquire tree locks on multiple paths.
    #[pyo3(signature = (ctx, paths, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_tree_batch(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        paths: Vec<String>,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner_capability = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let paths = paths.clone();
                let capability = owner_capability.clone();
                async move {
                    let capability = capability.as_ref().map(|(lease_ref, ownership_ref)| {
                        (lease_ref.as_str(), ownership_ref.as_str())
                    });
                    mgr.acquire_tree_batch(&paths, timeout, capability).await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Acquire a mixed batch of exact and tree locks.
    #[pyo3(signature = (ctx, exact_paths, tree_paths, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_exact_tree_batch(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        exact_paths: Vec<String>,
        tree_paths: Vec<String>,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner_capability = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let exact = exact_paths.clone();
                let tree = tree_paths.clone();
                let capability = owner_capability.clone();
                async move {
                    let capability = capability.as_ref().map(|(lease_ref, ownership_ref)| {
                        (lease_ref.as_str(), ownership_ref.as_str())
                    });
                    mgr.acquire_exact_tree_batch(&exact, &tree, timeout, capability)
                        .await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Acquire a batch of locks from a list of request dicts.
    /// Each request dict: {"path": str, "kind": "exact"|"tree"}
    #[pyo3(signature = (ctx, requests, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_batch(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        requests: Vec<HashMap<String, String>>,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner_capability = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;

        let lock_requests = parse_pathlock_request_batch(&requests)?;

        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let reqs = lock_requests.clone();
                let capability = owner_capability.clone();
                async move {
                    let capability = capability.as_ref().map(|(lease_ref, ownership_ref)| {
                        (lease_ref.as_str(), ownership_ref.as_str())
                    });
                    mgr.acquire_batch(&reqs, timeout, capability).await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Acquire a batch on Tokio; the Python adapter settles cancellation before releasing it.
    #[pyo3(signature = (ctx, requests, timeout_secs=0.0, owner_lease_ref=None))]
    fn pathlock_acquire_batch_async<'py>(
        &self,
        py: Python<'py>,
        ctx: Option<HashMap<String, String>>,
        requests: Vec<HashMap<String, String>>,
        timeout_secs: f64,
        owner_lease_ref: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let owner = extract_optional_owned_lease_ref(py, owner_lease_ref.as_ref())?;
        let requests = parse_pathlock_request_batch(&requests)?;
        let manager = self.clone_pathlock_manager();
        let task = self.rt.spawn(FS_CTX.scope(fs_ctx, async move {
            let capability = owner
                .as_ref()
                .map(|(lease_ref, ownership_ref)| (lease_ref.as_str(), ownership_ref.as_str()));
            manager.acquire_batch(&requests, timeout, capability).await
        }));
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let lease = task
                .await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
                .map_err(pathlock_err_to_py)?;
            Python::attach(|py| owned_lease_to_py_dict(py, &lease))
        })
    }

    /// Reacquire validated durable coverage under its original logical owner.
    #[pyo3(signature = (ctx, requests, owner_id, timeout_secs=0.0))]
    fn pathlock_reacquire_batch_async<'py>(
        &self,
        py: Python<'py>,
        ctx: Option<HashMap<String, String>>,
        requests: Vec<HashMap<String, String>>,
        owner_id: String,
        timeout_secs: f64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let timeout = validate_timeout_secs(timeout_secs)?;
        let requests = parse_pathlock_request_batch(&requests)?;
        let manager = self.clone_pathlock_manager();
        let task = self.rt.spawn(FS_CTX.scope(fs_ctx, async move {
            manager.reacquire_batch(&requests, &owner_id, timeout).await
        }));
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let lease = task
                .await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?
                .map_err(pathlock_err_to_py)?;
            Python::attach(|py| owned_lease_to_py_dict(py, &lease))
        })
    }

    /// Create a borrowed view of an owned lease.
    #[pyo3(signature = (ctx, owned_lease_ref))]
    fn pathlock_as_borrowed(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        owned_lease_ref: Py<PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let mgr2 = mgr.clone();
        let fs_ctx = build_fs_context(ctx);
        let lease_ref = extract_lease_ref(py, &owned_lease_ref)?;
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr2.clone();
                let lr = lease_ref.clone();
                async move {
                    mgr.get_owned_lease_by_ref(&lr).await.ok_or_else(|| {
                        PathLockError::Internal(format!("lease not found for ref '{lr}'"))
                    })
                }
            })
            .map_err(|e: PathLockError| PyRuntimeError::new_err(e.to_string()))?;
        let borrowed = mgr.as_borrowed(&lease);
        Python::attach(|py| borrowed_lease_to_py_dict(py, &borrowed))
    }

    /// Refresh an owned lease. Returns "refreshed", "lost", or "failed".
    #[pyo3(signature = (ctx, owned_lease_ref))]
    fn pathlock_refresh(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        owned_lease_ref: Py<PyAny>,
    ) -> PyResult<String> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let (lease_ref, ownership_ref) = extract_owned_lease_ref(py, &owned_lease_ref)?;
        let status = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let lr = lease_ref.clone();
                let ownership = ownership_ref.clone();
                async move {
                    let lease = mgr
                        .get_owned_lease_by_capability(&lr, &ownership)
                        .await
                        .ok_or_else(|| {
                            PathLockError::InvalidRequest(format!(
                                "owned lease capability does not match ref '{lr}'"
                            ))
                        })?;
                    mgr.refresh(&lease).await
                }
            })
            .map_err(pathlock_err_to_py)?;
        Ok(status)
    }

    /// Release an owned lease.
    #[pyo3(signature = (ctx, owned_lease_ref))]
    fn pathlock_release(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        owned_lease_ref: Py<PyAny>,
    ) -> PyResult<()> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let (lease_ref, ownership_ref) = extract_owned_lease_ref(py, &owned_lease_ref)?;
        self.run_scoped(py, fs_ctx, move || {
            let mgr = mgr.clone();
            let lr = lease_ref.clone();
            let ownership = ownership_ref.clone();
            async move {
                let lease = mgr
                    .get_releasable_lease_by_capability(&lr, &ownership)
                    .await
                    .ok_or_else(|| {
                        PathLockError::InvalidRequest(format!(
                            "owned lease capability does not match ref '{lr}'"
                        ))
                    })?;
                mgr.release(&lease).await
            }
        })
        .map_err(pathlock_err_to_py)?;
        Ok(())
    }

    /// Release selected lock paths from an owned lease.
    #[pyo3(signature = (ctx, owned_lease_ref, lock_paths))]
    fn pathlock_release_selected(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        owned_lease_ref: Py<PyAny>,
        lock_paths: Vec<String>,
    ) -> PyResult<()> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let (lease_ref, ownership_ref) = extract_owned_lease_ref(py, &owned_lease_ref)?;
        self.run_scoped(py, fs_ctx, move || {
            let mgr = mgr.clone();
            let lr = lease_ref.clone();
            let ownership = ownership_ref.clone();
            let paths = lock_paths.clone();
            async move {
                let lease = mgr
                    .get_releasable_lease_by_capability(&lr, &ownership)
                    .await
                    .ok_or_else(|| {
                        PathLockError::InvalidRequest(format!(
                            "owned lease capability does not match ref '{lr}'"
                        ))
                    })?;
                mgr.release_selected(&lease, &paths).await
            }
        })
        .map_err(pathlock_err_to_py)?;
        Ok(())
    }

    /// Export a handoff ref from an owned lease.
    #[pyo3(signature = (ctx, owned_lease_ref))]
    fn pathlock_to_handoff(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        owned_lease_ref: Py<PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let (lease_ref, ownership_ref) = extract_owned_lease_ref(py, &owned_lease_ref)?;
        let handoff = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let lr = lease_ref.clone();
                let ownership = ownership_ref.clone();
                async move {
                    let lease = mgr
                        .get_owned_lease_by_capability(&lr, &ownership)
                        .await
                        .ok_or_else(|| {
                            PathLockError::InvalidRequest(format!(
                                "owned lease capability does not match ref '{lr}'"
                            ))
                        })?;
                    Ok(mgr.to_handoff(&lease))
                }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| handoff_ref_to_py_dict(py, &handoff))
    }

    /// Mark an owned lease as handed off.
    #[pyo3(signature = (ctx, owned_lease_ref))]
    fn pathlock_handoff(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        owned_lease_ref: Py<PyAny>,
    ) -> PyResult<()> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let (lease_ref, ownership_ref) = extract_owned_lease_ref(py, &owned_lease_ref)?;
        self.run_scoped(py, fs_ctx, move || {
            let mgr = mgr.clone();
            let lr = lease_ref.clone();
            let ownership = ownership_ref.clone();
            async move {
                let lease = mgr
                    .get_owned_lease_by_capability(&lr, &ownership)
                    .await
                    .ok_or_else(|| {
                        PathLockError::InvalidRequest(format!(
                            "owned lease capability does not match ref '{lr}'"
                        ))
                    })?;
                mgr.handoff(&lease).await
            }
        })
        .map_err(pathlock_err_to_py)?;
        Ok(())
    }

    /// Adopt a handoff ref, returning a new owned lease.
    #[pyo3(signature = (ctx, handoff_ref))]
    fn pathlock_adopt(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        handoff_ref: HashMap<String, Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        let owner_id: String = handoff_ref
            .get("owner_id")
            .or_else(|| handoff_ref.get("handle_id"))
            .ok_or_else(|| {
                PyValueError::new_err("handoff_ref.owner_id or handoff_ref.handle_id required")
            })?
            .extract(py)?;
        let lock_paths: Vec<String> = handoff_ref
            .get("lock_paths")
            .ok_or_else(|| PyValueError::new_err("handoff_ref.lock_paths required"))?
            .extract(py)?;
        if owner_id.is_empty()
            || lock_paths.is_empty()
            || lock_paths.iter().any(|path| !path.starts_with('/'))
        {
            return Err(PyValueError::new_err(
                "handoff_ref owner_id/handle_id and absolute lock_paths are required",
            ));
        }
        let covered_paths: Vec<PathLockRequest> = handoff_ref
            .get("covered_paths")
            .map(|v| {
                let raw: Vec<HashMap<String, String>> = v.extract(py)?;
                raw.iter().map(parse_pathlock_request).collect()
            })
            .transpose()?
            .unwrap_or_default();
        let lease_ref: Option<String> = handoff_ref
            .get("lease_ref")
            .map(|v| v.extract(py))
            .transpose()?
            .filter(|s: &String| !s.is_empty());
        let handle_mode = handoff_ref
            .get("handle_mode")
            .filter(|v| !v.bind(py).is_none())
            .map(|v| -> PyResult<PathLockHandleMode> {
                match v.extract::<String>(py)?.as_str() {
                    "lock_path" => Ok(PathLockHandleMode::LockPath),
                    "logical_path" => Ok(PathLockHandleMode::LogicalPath),
                    _ => Err(PyValueError::new_err("invalid handoff handle_mode")),
                }
            })
            .transpose()?;
        let handoff = PathLockHandoffRef {
            holder_id: handoff_ref
                .get("holder_id")
                .map(|v| v.extract(py))
                .transpose()?,
            lease_ref,
            handle_mode,
            owner_id,
            lock_paths,
            covered_paths,
        };
        let lease = self
            .run_scoped(py, fs_ctx, move || {
                let mgr = mgr.clone();
                let h = handoff.clone();
                async move { mgr.adopt(&h).await }
            })
            .map_err(pathlock_err_to_py)?;
        Python::attach(|py| owned_lease_to_py_dict(py, &lease))
    }

    /// Check if a path is locked.
    #[pyo3(signature = (ctx, path, ignore_stale=true))]
    fn pathlock_is_locked(
        &self,
        py: Python<'_>,
        ctx: Option<HashMap<String, String>>,
        path: String,
        ignore_stale: bool,
    ) -> PyResult<bool> {
        let mgr = self.clone_pathlock_manager();
        let fs_ctx = build_fs_context(ctx);
        self.run_scoped(py, fs_ctx, move || {
            let mgr = mgr.clone();
            let p = path.clone();
            async move { mgr.is_locked(&p, ignore_stale).await }
        })
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }

}
